# Task: Fix work kind: admission, mirrors, sandboxed workflow launch, and outcome mapping

## Goal

Add the `fix` work-kind handler so an eligible Bug card is admitted in board order, its branch heads are resolved once and recorded, fresh clones are cut from local bare mirrors, the companion Agent Runner workflow runs in the existing sandbox under its own supervisor with fix-specific limits, a per-run image tag, a contained credential, and a memory headroom check, and the structured outcome is mapped to claim state, board presentation, and issue comments. Includes the fix configuration schema, side-effect reconciliation before every launch, and the single fresh-clone recovery retry.

## Background

All paths are in `agent-factory` unless a sibling repository is named. Planning sources are `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "Components", "The handler interface", "Persistence" for the frozen spec, "Cycle", "Fix admission and checkouts", "Launch", "Outcome contract", "Decisions", "Risks"), the specs under `specs/`, and `test-plan.md`.

Preconditions in the repository: the controller and runtime dispatch through `WorkKindHandler` in `src/agent_factory/work_kinds/base.py` with `EvalHandler` registered in `src/agent_factory/work_kinds/__init__.py`; `ClaimStore` is at schema v4 with one nonterminal run per `run.kind`, `nonterminal_runs(kind=...)`, the `blocked` lifecycle, provider-scoped quota holds at `settings("admission","quota:<provider>")`, and per-kind readiness at `settings("runtime","readiness:<kind>")`; `runtime.cycle` admits per registered kind. The companion workflow `core/factory-fix-v1.0.yaml` exists in `agent-runner` on `main` with `# factory-contract: factory-fix/1` as its first line and the outcome contract below. The supervisor (`src/agent_factory/supervisor.py`) launches an `ExecutionPlan` (argv, cwd, allowed environment, progress sources, ownership hints) in a detached session, discovers containers by their `/artifacts` mount, and records results from files; do not change its lifecycle logic, only record `image_tag` from the plan's ownership hints. `scripts/sandbox-run.sh` in agent-runner builds the current Runner checkout into an image (`--image` selects the tag; `IMAGE` is also honored), mounts `/artifacts`, forwards named environment variables and auth files, and accepts `--docker-run-arg`.

### Configuration

`src/agent_factory/config.py`:

- `SharedConfig`: `fix.targets[]` (each `repository = "Codagent-AI/agent-runner"` and optional `branch`, default `main`), `fix.branches` (`runner`, `skills`, default `main`), `fix.defaults` (`lead`, `implementor`, `tester` as `cli:model:effort` triples), `fix.contract = "factory-fix/1"`. Add the Codagent values to `config/codagent.toml` covering agent-runner, agent-skills, agent-validator, agent-plugin, and agent-evals.
- `LocalConfig`: `[fix.limits]` `inactivity_seconds` (default 900), `execution_seconds` (7200), `total_seconds` (10800); `[fix.schedule]` window (default always open) with a comment that the window matters only when a fix role selects Codex; `limits.memory_reservation_gib` (default 3); `credentials.fix_environment` (path); `repositories.working_clones` mapping `owner/repo` to the operator's clone path (consumed by the merge sync, validated here as a path per configured target). Update `config/local.example.toml` with portable placeholders.

Mirrors live at `<storage_root>/mirrors/<owner>__<repo>.git`; clones at `<storage_root>/clones/<claim>/<attempt>/{repo,runner,skills}`; fix run artifacts under the existing runs area with `/artifacts/input/issue.json` and `/artifacts/fix-outcome.json`.

### Handler

`src/agent_factory/work_kinds/fix/` with `FixHandler` implementing every `WorkKindHandler` method, plus modules for mirrors and clones, the launcher, the outcome reader, and issue-input construction. Register it as `"fix"` in `work_kinds/__init__.py`.

- `snapshot`: card with `Owner=factory`, `Status=Ready`, native type `Bug`, open issue, no `needs-input` label, author permission from `get_permission` in `{write, maintain, admin}`; board order from `list_project_items` is preserved, so the first eligible card in the Bug group wins. Ineligible cards are skipped; a card whose author lacks permission gets one explanatory comment through the existing readiness-feedback path (`Controller.report_request_readiness` or equivalent) with a stable marker. Bugs carry no per-issue overrides.
- `accept`: fetch the target mirror (create with `git clone --mirror` on first use; fetch over HTTPS with the App installation token via a temporary `GIT_ASKPASS` helper so the token never lands on disk), resolve `origin/<branch>` in the mirror and in the local Runner and Skills checkouts through the existing `_resolve_revision`, and freeze:

  ```json
  {"version": 1, "kind": "fix",
   "target": {"repository": "Codagent-AI/agent-runner", "branch": "main"},
   "revisions": {"target": "<sha>", "runner": "<sha>", "skills": "<sha>"},
   "roles": {"lead": "cursor:…", "implementor": "…", "tester": "…"},
   "contract": "factory-fix/1"}
  ```

  A mirror fetch failure holds the bug without recording an attempt or consuming a retry and reports the problem (readiness event on the card; `readiness:fix` setting when it is a global mirror problem).
- `readiness(local, shared)`: the checks that must fail closed before any launch: `credentials.fix_environment` is owner-readable and contains exactly one assignment `GH_TOKEN=<value>` (any other variable name, a missing token, or a token equal to the App installation token fails); the Runner branch head contains `workflows/core/factory-fix-v1.0.yaml` (or the repository's actual workflow path) with the `# factory-contract: factory-fix/1` line, checked with `git show <sha>:<path>` at the recorded Runner commit at launch and at the branch head for readiness; each configured mirror exists or can be created. Return `Diagnostic` values so `doctor` can print them; store fix-only holds under `readiness:fix`.
- `prepare`: `git clone --local --no-checkout` from the mirror (target) and from the local Runner and Skills checkouts, then `git checkout --detach <sha>` for each, recorded in `claim.preparation` for cleanup. A recovery attempt gets a fresh set at the same commits; no clone is reused.
- `next_unit`: one unit key, `fix`; attempts numbered as today with `reason` in `{initial, recovery, unblock}`.
- Side-effect reconciliation before reserving any attempt: branch name `factory/fix-<issue>-<claim8>` (first eight characters of the claim id); check `gh api repos/<repo>/branches/<branch>` and `gh pr list --head <branch> --state open --json url,number,headRefOid` through `GitHubClient` (add `get_branch` and `list_open_pull_requests_for_head` or equivalent to `src/agent_factory/github.py`). An open PR settles the claim as `pull-request` without launching. A lookup failure holds the claim for the next poll and launches nothing. An existing branch without a PR is reported in the admission comment and does not block a launch, since the workflow creates the branch with `git checkout -b` and a fresh push will fail loudly if it conflicts.
- Memory headroom (generic admission, applied to every kind): `docker info --format '{{.MemTotal}}'` minus the sum of current values from `docker stats --no-stream --format '{{.MemUsage}}'`, compared with `limits.memory_reservation_gib`. Insufficient headroom or a probe failure holds admission with an explanation stored so `status` can print it (for example `settings("runtime","memory")`), records no attempt, and consumes no retry. Place the probe in `src/agent_factory/operations.py` or a small `resources` module and call it from the runtime's admission step for each kind.
- `plan`: argv runs `src/agent_factory/work_kinds/fix/launch.sh` (factory-owned, shipped in the package) with cwd = the Runner clone. The launcher parses the validated env file copy and invokes:

  ```
  scripts/sandbox-run.sh
    --image agent-runner-factory:<run-id>
    --artifact-dir <evidence>
    --no-default-secrets
    --env-file <validated copy containing only GH_TOKEN=…>
    --mount-cursor-auth | --mount-claude-auth | --mount-codex-auth   # from fix roles
    --docker-run-arg --mount --docker-run-arg type=bind,source=<repo clone>,target=/workspace/repo
    --docker-run-arg --mount --docker-run-arg type=bind,source=<skills clone>,target=/workspace/skills,readonly
    -- <container script>
  ```

  If `sandbox-run.sh` at the recorded Runner commit lacks `--no-default-secrets`, add that flag to `scripts/sandbox-run.sh` in `agent-runner` as part of this task (it must skip loading the checkout's `.sandbox-secrets.env`) and treat its absence as a contract incompatibility. The factory writes the validated single-line env copy under the run's private directory before launch; the App token never appears in the plan's allowed environment. The container script installs the Skills clone the way the and-scene suite's `AGENT_SKILLS_BOOTSTRAP` does, writes `/workspace/home/.agent-runner/settings.yaml` (headless autonomous backend) and `/workspace/repo/.agent-runner/config.yaml` (role profiles from the frozen spec), configures a Git askpass helper from `GH_TOKEN`, then runs:

  ```
  cd /workspace/repo
  AGENT_RUNNER_NO_TUI=1 agent-runner run core/factory-fix \
     --param issue_file=/artifacts/input/issue.json \
     --param branch_name=factory/fix-<issue>-<claim8> \
     --param contract_version=factory-fix/1
  ```

  Before launch the factory writes `/artifacts/input/issue.json`: repository, number, title, body, attempt number, claim id, prior factory PR if any, and the eligible comments (author permission in writers, created after the last decline comment; the factory's own comments excluded). Progress sources: `/artifacts/factory-suite.log`, the Runner session directory under `/artifacts/agent-runner/` including `state.json`, `audit.log`, and `output/*`, and the Cursor and Claude session globs the eval plan already uses. Ownership hints carry the artifact path and `image_tag`. The eval plan (`AndSceneAdapter.plan`) gets `IMAGE=agent-runner-factory:<run-id>` in its allowed environment so and-scene builds under a per-run tag too; the supervisor records the image tag on the run for both kinds.
- `limits` returns the fix limits; `window` returns the fix schedule; `providers` returns the CLIs named by the frozen roles.
- `read_result` maps `/artifacts/fix-outcome.json`: file absent, unparsable, or with an unexpected contract → technical failure; `needs-input` → `blocked`; `pull-request` → settled `pending-human-review`; `failed` → settled `failed`. Non-zero exit with a valid `pull-request` file still settles as a PR (the file is authoritative); non-zero exit with no file is technical. Store on the run the parsed outcome plus `image_tag`, `branch_name`, and `container`. Timeouts record which limit was exceeded and follow the technical recovery policy.
- Recovery: one automatic retry from fresh clones at the recorded commits after reconciliation; exhausted recovery settles `infra-error`. Quota waits and unavailable prerequisites do not consume the retry.
- `presentation` and `report_events` for outcomes: on `pull-request`, comment with the PR link, Status Review, `Verdict=pending-human-review`; on `needs-input`, comment the reasons, label `needs-input` on, Status Running, no verdict; on `failed`, comment reasons and any PR link, Review, `Verdict=failed`; on exhausted recovery, Review with `Verdict=infra-error` and an explanation. Admission comment names `target@<7> runner@<7> skills@<7>` and the attempt number; `Refs` renders the same three. Retry and cancellation comments follow the eval pattern. All comments carry stable markers and never repeat for unchanged state. Never assign `passed`, never close the issue, never merge.
- `cleanup` removes the claim's recorded clones and `docker rmi` of its recorded image tags, tolerating already-removed items and never touching mirrors, shared checkouts, or another claim's clones; the runtime's existing Done-triggered cleanup path calls it.

Outcome contract written by the workflow:

```json
{"contract": "factory-fix/1",
 "outcome": "pull-request" | "needs-input" | "failed",
 "reasons": ["…"],
 "pr": {"url": "…", "number": 214, "branch": "factory/fix-212-1a2b3c4d", "head_sha": "…"},
 "validator": {"status": "passed" | "failed" | "skipped"},
 "ci": {"status": "passed" | "failed" | "pending"}}
```

Constraints: no eval concept in the fix handler and no fix concept in the core; no controller-side push or diff inspection; the push credential is never used by the controller; no LLM triage, caps, or per-issue overrides. The blocked-claim comment scan, drag gestures, merge sync, and operator-facing `doctor`/`status` rendering are outside this task; `FixHandler.gesture` may return `None` for now, and the blocked claim must simply stay `blocked` with its label until that behavior exists. Keep `pyright` strict and `ruff` clean.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-bug-intake/spec.md`.

### Requirement: Select eligible bugs in board order

The factory SHALL select open issues from configured source repositories with native `Type=Bug`, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL follow manual Project order within the Bug horizontal group's Ready column. Priority values, issue age, and repository SHALL NOT override that order. An ineligible bug SHALL NOT prevent selection of a later eligible bug. Eval and bug selection SHALL be independent: each kind fills only its own execution slot. Bugs SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration.

#### Scenario: Pick the top bug

- **WHEN** the fix slot is free and two eligible bugs sit in Ready in the Bug group
- **THEN** the factory admits the higher card regardless of issue age or repository

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Bug card has `Owner=factory` but its author lacks the required repository access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the bug is not eligible

#### Scenario: Skip a blocked or held bug

- **WHEN** the top Bug card carries the `needs-input` label or a fix-specific hold applies
- **THEN** the factory selects the next eligible bug instead

#### Scenario: Reorder while a fix is running

- **WHEN** a user changes the Bug group order during active fix execution
- **THEN** the active fix continues and the new order governs subsequent selection

### Requirement: Resolve branches once per claim

A new fix claim SHALL resolve the configured branches of the target repository, Agent Runner, and Agent Skills (default `main`) to commits at admission and record those commits on the claim. Configuration SHALL name branches, not commits. The claim's attempts, including its technical recovery retry, SHALL use the recorded commits. The `Refs` field SHALL render as `target@<7> runner@<7> skills@<7>`. Only a deliberately fresh claim SHALL re-resolve branch heads.

#### Scenario: Admit a bug

- **WHEN** the factory admits a bug
- **THEN** it records the resolved commits for the target repository, Runner, and Skills on the claim
- **AND** the card's Refs shows those three abbreviated commits

#### Scenario: Retry after the branch advanced

- **WHEN** the target branch receives new commits between a failed attempt and its recovery retry
- **THEN** the retry uses the commits recorded at admission
- **AND** the newer commits are used only by a later fresh claim

### Requirement: Reconcile side effects before launching

Before launching any attempt for a bug, including a recovery retry or a fresh claim, the factory SHALL check the target repository for an existing factory branch for that issue and an open factory pull request referencing it. An existing open factory PR SHALL settle the claim as handed off rather than launch a duplicate. When the factory cannot establish whether a prior attempt pushed a branch or opened a PR, it SHALL hold the claim, report the ambiguity on the issue and in status, and SHALL NOT launch.

#### Scenario: Find a PR from a crashed attempt

- **WHEN** an attempt opened a PR but failed before its outcome was recorded
- **THEN** reconciliation finds the open factory PR for the issue and reports it as the attempt's result without launching the retry

#### Scenario: Fail to reach GitHub before relaunch

- **WHEN** the reconciliation lookup fails
- **THEN** the factory does not launch and retries reconciliation on a later poll

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-fix-execution/spec.md`.

### Requirement: Clone from local mirrors at recorded commits

The factory SHALL maintain a bare mirror for each configured target repository under the local storage root, fetched at admission using the controller's read credential. Each fix attempt SHALL run in fresh clones of the target repository, Agent Runner, and Agent Skills checked out at the claim's recorded commits. The push credential SHALL NOT be used for fetching or by the controller. Clones SHALL be factory-owned and recorded for cleanup.

#### Scenario: Launch an attempt

- **WHEN** an attempt is launched
- **THEN** its clones are checked out at the claim's recorded commits from the local mirrors
- **AND** no clone from a previous attempt is reused

#### Scenario: Fail to fetch a mirror

- **WHEN** the mirror fetch for the target repository fails at admission
- **THEN** the factory holds the bug without recording an attempt or consuming a retry and reports the problem

### Requirement: Invoke the versioned fix workflow

The factory SHALL run the companion Agent Runner fix workflow in the existing sandbox through the Runner sandbox script, passing a per-run image tag, the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, and the location of the fix credential. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the workflow at the recorded Runner commit does not declare a compatible contract version and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure.

#### Scenario: Launch with a compatible workflow

- **WHEN** the recorded Runner commit contains the fix workflow at the expected contract version
- **THEN** the attempt starts under its own supervisor with the configured roles and a run-specific image tag

#### Scenario: Launch with an incompatible workflow

- **WHEN** the workflow is absent or declares an unsupported contract version
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy

### Requirement: Apply fix-specific limits and window

Each fix attempt SHALL have configurable limits with defaults of 15 minutes without progress, two hours of execution, and three hours of total elapsed time. Fix admission SHALL use its own configurable window, defaulting to always open, and SHALL honor pause, disk and memory admission checks, and provider quota holds for providers used by the fix roles. Fix admission SHALL NOT be bound to the eval window.

#### Scenario: Admit a fix outside the eval window

- **WHEN** a bug is eligible at 18:00 under default configuration and the fix slot is free
- **THEN** the factory admits it although the eval window is closed

#### Scenario: Exceed a fix limit

- **WHEN** a fix attempt exceeds its inactivity, execution, or total limit
- **THEN** the factory stops verified owned execution, records which limit was exceeded, preserves evidence, and applies the recovery policy

### Requirement: Recover a fix attempt from a fresh clone

A fix attempt that fails technically SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation. There SHALL be no resume of a partial attempt. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Retry once

- **WHEN** the first attempt fails technically and reconciliation finds no branch or PR
- **THEN** the factory launches one retry from fresh clones at the same commits

#### Scenario: Exhaust recovery

- **WHEN** the retry also fails technically
- **THEN** the claim settles with `infra-error` and its evidence is retained

### Requirement: Isolate concurrent sandbox builds

Each attempt SHALL build and run its sandbox under a unique image tag derived from its run identity, so an eval attempt and a fix attempt building from different Runner checkouts never overwrite each other's image. The run record SHALL store the image tag used. Run-specific images SHALL be removed together with the claim's clones when its card reaches Done, and SHALL be retained while the claim is running, waiting, blocked, or in Review.

#### Scenario: Build while an eval is running

- **WHEN** a fix attempt starts while an eval attempt's container is running
- **THEN** the fix builds and runs under its own tag and the eval's image and provenance are unaffected

### Requirement: Preserve fix evidence

The factory SHALL retain each attempt's workflow output, structured outcome, validator and CI results as available, and the PR reference under the attempt's artifact directory, recorded in SQLite. Evidence SHALL survive clone cleanup.

#### Scenario: Inspect a declined attempt

- **WHEN** an operator inspects a `needs-input` attempt after cleanup
- **THEN** the attempt's reasons and workflow output remain available under its artifact directory

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-claim-lifecycle/spec.md`.

### Requirement: Check memory headroom before admission

Before admitting any attempt, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check.

#### Scenario: Admit a second attempt with headroom

- **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
- **THEN** the second kind's attempt may be admitted

#### Scenario: Wait for memory

- **WHEN** the remaining Docker allowance is below the reservation
- **THEN** no new attempt starts and status names memory as the blocking condition

### Requirement: Prevent overlapping execution (fix portion)

#### Scenario: Run an eval and a fix together

- **WHEN** an eval repetition is running and an eligible bug is admitted
- **THEN** the fix attempt starts in its own slot and the eval continues unaffected

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-fix-reporting/spec.md`.

### Requirement: Comment on fix activity

The factory SHALL comment on the issue when it admits a bug (including the resolved refs and attempt number), when an attempt is declined, fails, is retried, is cancelled, or produces a PR, and when a post-merge sync succeeds or is blocked. Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a bug

- **WHEN** the factory admits a bug
- **THEN** the issue receives one comment naming the target, Runner, and Skills commits and the attempt number

(This task's portion: every comment except the post-merge sync comments.)

### Requirement: Map fix outcomes to the board

On `pull-request`, the factory SHALL link the PR in a comment, move the card to Review, and set `Verdict=pending-human-review`. On `needs-input`, it SHALL post the agent's reasons, apply the red `needs-input` label, leave the card in Running, and release the fix slot. On `failed`, it SHALL post the reasons, link any PR, move the card to Review, and set `Verdict=failed`. On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a PR

- **WHEN** an attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the PR link is on the issue

#### Scenario: Park a declined bug

- **WHEN** an attempt returns `needs-input`
- **THEN** the card stays in Running with the `needs-input` label and the reasons are on the issue
- **AND** another eligible bug can be admitted to the fix slot

#### Scenario: Report a failed fix

- **WHEN** an attempt returns `failed` with an open PR
- **THEN** the card moves to Review with `failed`, the reasons and PR link are on the issue, and the PR remains open

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-operations/spec.md`.

### Requirement: Configure deployment without Codagent-specific controller code (fix portion)

Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, and the fix credential file location.

#### Scenario: Configure fix targets

- **WHEN** an operator configures five target repositories with mirror and working-clone paths and leaves branches unset
- **THEN** fixes resolve `main` for each repository and the merge sync targets each configured working clone

### Requirement: Persist pause and enforce configured admission controls (fix portion)

Configuration SHALL support [...] the default fix limits of 15 minutes, two hours, and three hours, [...] and a default memory reservation of 3 GiB per attempt. These values SHALL be configurable. The configured free-space minimum and memory reservation SHALL be checked before admission; insufficient space or memory SHALL hold new affected work without consuming an execution retry. The local configuration SHALL note that the fix window matters only when a fix role selects Codex.

#### Scenario: Pause during a fix

- **WHEN** the operator pauses while a fix and an eval are both running
- **THEN** both finish under their limits and no new attempt of either kind starts until resume

## Test Plan

- `INT-003` (Fix outcome contract and result mapping): `FixHandler.read_result` and `classify` over real artifact directories and the store. Fixtures for `pull-request`, `needs-input`, `failed`, a missing file, malformed JSON, a wrong contract version, and a valid `pull-request` file with a non-zero exit recorded on the run; consume each through the controller. Assert `pull-request` → settled, verdict `pending-human-review`, PR recorded, presentation Review; `needs-input` → lifecycle `blocked`, presentation Running with label `needs-input` on, reasons in the event body; `failed` → settled with verdict `failed`, PR link retained; missing, malformed, or wrong contract → technical failure consuming the single retry, then `infra-error`; valid PR file with non-zero exit → settled as PR; events carry stable markers and are not duplicated on repeated consumption. Execution: `tests/integration/test_fix_outcomes.py`; `uv run pytest`.
- `INT-005` (Docker memory probe and per-run image tagging): a `docker` stub on `PATH` scripting `info` and `stats` for ample headroom, exact shortfall, a running container consuming most of the allowance, and a failing probe; evaluate admission for each; build an eval plan and a fix plan for two different runs. Assert admission proceeds only when allowance minus usage ≥ reservation, a probe failure holds with an explanatory reason that appears in `status`, the fix argv includes `--image agent-runner-factory:<run-id>`, the eval plan's allowed environment sets `IMAGE=agent-runner-factory:<run-id>`, and the two tags differ and are recorded in ownership hints. Execution: `tests/integration/test_admission_resources.py`; `uv run pytest`.
- `INT-008` (Fix limits and window reach the supervisor): local configuration with fix limits of a few seconds and an always-open fix window while the eval window is closed; controlled executables that go silent, run past the execution limit, and run past the total limit. Assert admission of a bug succeeds while eval admission is refused for the window, the run record stores the fix limits, each executable is stopped for the specific limit it exceeded, evidence is preserved, and the single recovery retry is consumed exactly once. Execution: `tests/integration/test_fix_limits.py`; `uv run pytest`.
- `E2E-003` (Two slots with real processes and controller restart): as the existing independent-execution test: real long-running controlled executables for the suite and the fix launcher, real supervisor sessions, isolated root. Admit an eval and a fix so both run; attempt a second of each through `tick`; kill and restart the controller; let each finish with a scripted result. Assert two non-terminal runs with different kinds and supervisors, the second of each kind refused while status remains usable, both executables survive the restart and are re-observed, each result recorded once against its own claim, and the memory probe stub consulted before each admission. Execution: extend `tests/e2e/test_independent_execution.py` with the `darwin` marker.
- `E2E-004` (Real Docker fix launch from a factory-prepared Runner clone): Docker host; a temporary Runner remote derived from a real checkout plus one committed, command-only test workflow that writes `fix-outcome.json` and records the environment variable names, mount modes, and `AGENT_RUNNER_SOURCE_COMMIT` it sees; the factory prepares the clones at that commit; a throwaway env file with a dummy `GH_TOKEN`; a `.sandbox-secrets.env` planted in the clone. Launch one attempt, then a second with a different run id while the first runs. Assert each container runs an image tagged with its own run id and reports the clone's commit, `/workspace/repo` is writable and `/workspace/skills` read-only, the environment contains `GH_TOKEN` and no variable from the planted file, the supervisor discovers each container by its exact artifact mount and image, the outcome file is read back, and images are removed on cleanup. No model calls. Execution: `tests/e2e/test_docker_fix_launch.py` with the `docker` marker; test-owned containers and images only, cleaned after the test.

## Done When

- `config/codagent.toml` carries `[fix]` targets, branches, defaults, and contract for the five Codagent repositories; `config/local.example.toml` carries fix limits, fix window with the Codex note, `memory_reservation_gib`, `credentials.fix_environment`, and `repositories.working_clones` with portable placeholders; loading rejects malformed values with messages naming the key.
- `FixHandler` is registered as `"fix"`, selects in board order with admission-time permission re-verification, resolves and freezes target, Runner, and Skills commits, renders `Refs` as `target@<7> runner@<7> skills@<7>`, and posts the admission comment.
- Mirrors are created and fetched with the App token through an askpass helper; per-attempt clones are cut at the recorded commits and recorded for cleanup; a fetch failure holds without an attempt or retry.
- Reconciliation runs before every reservation; an open factory PR settles as `pull-request`; a lookup failure holds and launches nothing.
- The launcher passes `--image agent-runner-factory:<run-id>`, `--no-default-secrets`, a validated env file containing only `GH_TOKEN`, the read-write repo mount, the read-only skills mount, and the role auth mounts; `issue.json` is written before launch; the eval plan carries `IMAGE=agent-runner-factory:<run-id>`; the supervisor records `image_tag` on the run.
- Readiness fails closed on a bad credential file, the App token, or a missing contract line, holding fix admission under `readiness:fix` while eval admission is unaffected.
- The memory probe gates admission of every kind and its reason is visible in `status`.
- Outcomes map to lifecycle, verdict, status, label, and comments as specified; recovery retries once from fresh clones; exhausted recovery settles `infra-error`; evidence survives cleanup; cleanup removes clones and images only, tolerating already-removed items.
- INT-003, INT-005, INT-008, E2E-003, and E2E-004 pass (E2E-004 on a Docker host); `uv run pytest`, `uv run ruff check .`, and `uv run pyright` pass; existing eval tests still pass.
