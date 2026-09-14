## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional integration, end-to-end, agent-acceptance, and exceptional human-only obligations.

Risk drives the layers. The handler extraction touches every eval path, so its safety net is the unchanged existing suite rather than new tests. Per-kind slots, the schema migration, the merge sync, the outcome contract, and the blocked loop are boundary logic best proven with real SQLite, real Git, and controlled process or Docker substitutes. Live acceptance covers what stubs cannot: the deployed routing rule, one real sandboxed fix producing a real PR, the decline-and-unblock gesture, the merge sync against Paul's working clone, and the GitHub provisioning that makes the credential model safe. Concurrency is proven with real processes but stub executables (E2E-003); Paul declined a live concurrent eval-plus-fix run for cost.

The companion workflow `core/factory-fix-v1.0.yaml` is tested in `agent-runner` under that repository's conventions: workflow validation, a fixture-issue run with `--until triage` for both a fixable and a declined case, and outcome-file production. It is a cross-repo obligation recorded here, not planned in detail.

Existing markers apply: `docker` for tests needing a Docker host and test-owned containers, `darwin` for macOS process-session semantics. No new integration or E2E test may call GitHub, model providers, or the network.

## Integration Tests

### INT-001: Schema v2 migration and per-kind execution slots
- Covers: Prevent overlapping execution (per kind); Persist accepted work; blocked claims hold no slot; migration of an iteration-1 database.
- Boundary: `ClaimStore` against a real SQLite file at the currently shipped schema version 3 with eval claims, runs, settings, and reporting progress; the migration to version 4.
- Setup: Fixture database produced by the current v3 schema with one settled eval claim, one waiting claim with a consumed recovery retry, reporting progress, a saved pause, and an active global quota hold.
- Action: Open with the new store; reserve an eval run and a fix run; attempt a second run of each kind from a second connection; mark a fix run terminal with a `blocked` classification; reserve another fix run.
- Assertions: `user_version` is 4, `state.sqlite3.v3.bak` exists and opens with the previous schema, `run.kind` is backfilled from `claim.kind`, all prior rows and reporting progress are intact, the pause survives, and the global quota hold now exists as the provider-scoped `quota:codex` hold and still blocks eval admission. A migration failure injected mid-way leaves the database at v3 untouched. One reserved run per kind succeeds and the second of the same kind fails atomically with no partial row. A terminal blocked run frees the fix slot. `nonterminal_runs(kind=...)` filters correctly.
- Execution: `tests/integration/test_durable_claims.py` (extend) or a new `test_per_kind_slots.py`; `uv run pytest`.

### INT-002: Bug routing rule against the controlled GitHub API
- Covers: Route bug reports to the factory; Restrict factory assignment to repository writers; Deploy a new rule with existing issues; eval rule precedence; Deliver the same routing event again.
- Boundary: `Router` with the existing in-memory `GitHubRoutingClient` stub and the shared configuration parser.
- Setup: Configuration with `bug_type = "Bug"` and `hold_label = "factory-hold"`; source items for a writer's Bug, a non-writer's Bug, a Bug carrying the hold label, a pull request typed Bug, an `agent-evals` issue with both the eval label and Bug type, and a permission lookup that fails.
- Action: Route each event, then re-route the writer's Bug after a human changed Owner to human.
- Assertions: Writer Bug → `Owner=factory`, `Status=Ready`, receipt written. Non-writer or failed lookup → Backlog without ownership. Hold label → `Owner=human`, Backlog. PR → not routed as a bug. Eval-labelled Bug → eval rule applied including the native type mutation, no bug initialization. Re-delivery preserves the human's Owner change. No existing issue is touched without an event.
- Execution: `tests/integration/test_configured_routing.py` (extend); `uv run pytest`.

### INT-003: Fix outcome contract and result mapping
- Covers: Invoke the versioned fix workflow (outcome handling); Map fix outcomes to the board; Recover a fix attempt from a fresh clone (technical classification).
- Boundary: `FixHandler.read_result` and `classify` over real artifact directories and the store.
- Setup: Artifact directories containing `fix-outcome.json` fixtures for `pull-request`, `needs-input`, and `failed`; a missing file; malformed JSON; a wrong contract version; a valid `pull-request` file alongside a non-zero exit recorded on the run.
- Action: Consume each run's result through the controller.
- Assertions: `pull-request` → claim settled, verdict `pending-human-review`, PR recorded, presentation Review. `needs-input` → lifecycle `blocked`, presentation Running with label `needs-input` on, reasons in the event body. `failed` → settled with verdict `failed`, PR link retained when present. Missing, malformed, or wrong-contract file → technical failure that consumes the single recovery retry, then `infra-error` on the second. Valid PR file with non-zero exit → settled as PR. Events carry stable markers and are not duplicated on repeated consumption.
- Execution: `tests/integration/test_fix_outcomes.py`; `uv run pytest`.

### INT-004: Merge sync against real Git working clones
- Covers: Sync the working clone after merge, all scenarios; the sync never modifies the clone in another way.
- Boundary: The sync module against real temporary Git repositories: a bare origin, a factory-merged `main`, and a working clone.
- Setup: Working clones prepared as: `dev` checked out with a clean tree; `dev` with a tracked modification; `dev` with a staged change only; `dev` with an untracked file only; `dev` whose history conflicts with `main`; `dev` while local `main` has an unpushed commit; `dev` while `main` is checked out in a second linked worktree of the same clone; `main` checked out; detached HEAD; missing directory.
- Action: Run the sync for each.
- Assertions: Clean `dev` → local `main` equals `origin/main`, `dev` contains the merge commit, result success. Tracked or staged modification → blocked with reason "uncommitted changes" before any fetch: no new objects or refs, working tree, index, and branches unchanged. Untracked-only → treated as clean. Diverged local `main` → blocked, `main` still points at the local commit. `main` checked out in another worktree → blocked, both worktrees unchanged. Conflict → blocked with reason "conflicts", no merge started, `MERGE_HEAD` absent, working tree unchanged. `main` checked out → fast-forwarded. Detached HEAD or missing clone → blocked with that reason. A second run after the operator resolves the block succeeds and reports success.
- Execution: `tests/integration/test_merge_sync.py`; `uv run pytest`; Git required, no network.

### INT-005: Docker memory probe and per-run image tagging
- Covers: Check memory headroom before admission; Isolate concurrent sandbox builds; the eval plan's `IMAGE` value.
- Boundary: The memory probe over a controlled `docker` executable; `FixHandler.plan` and `AndSceneAdapter.plan` argument construction.
- Setup: A `docker` stub on `PATH` returning scripted `info` and `stats` output: ample headroom, exact shortfall, a running container consuming most of the allowance, and a failing probe.
- Action: Evaluate admission for each; build an eval plan and a fix plan for two different runs.
- Assertions: Admission proceeds only when allowance minus usage ≥ reservation; a probe failure holds with an explanatory reason rather than passing; the reason appears in `status`. The fix plan's argv includes `--image agent-runner-factory:<run-id>` and the eval plan's allowed environment sets `IMAGE=agent-runner-factory:<run-id>`; the two runs' tags differ and are recorded in ownership hints.
- Execution: `tests/integration/test_admission_resources.py`; `uv run pytest`.

### INT-006: Blocked loop, comment eligibility, and kind-specific gestures
- Covers: Recognize fix retry gestures; Read a kind-specific gesture; Leave a blocked fix in Running; Reconcile side effects before launching.
- Boundary: `FixHandler.gesture`, the runtime's blocked-claim scan, and side-effect reconciliation over the controlled GitHub client.
- Setup: A blocked fix claim with a decline comment at T0; comments after T0 from a writer, from a non-writer, and from the bot; a blocked claim whose card was dragged to Ready with no new comment; a blocked claim whose card was dragged to Review; a blocked claim whose card was dragged to Done; an ordinary waiting eval claim whose card was dragged to Running; a settled fix claim with its card dragged to Ready; a settled eval claim with its card dragged to Ready and its verdict intact; GitHub stub responses for an existing factory branch and an open factory PR for an issue.
- Action: Run the reconcile phase for each card; run reconciliation before a relaunch.
- Assertions: Writer comment → label `needs-input` removed, one new run reserved with reason `unblock`, `issue.json` contains only writer comments. Bot comment alone or non-writer comment alone → nothing changes. Blocked card dragged to Ready → unblock with the comments already present. Blocked card dragged to Review or Done → restored to Running with one correction comment and the label intact. Blocked card left in Running → no correction. Waiting eval card dragged to Running → restored to Ready as today. Settled fix card in Ready → new claim via supersede with re-resolved revisions and prior history retained. Eval card in Ready with old verdict → existing behavior (no fresh claim). Existing open factory PR → claim settled as PR without a launch; lookup failure → no launch, retried next cycle.
- Execution: `tests/integration/test_fix_gestures.py`; `uv run pytest`.

### INT-007: Doctor and status for both kinds
- Covers: Diagnose readiness with doctor (fix checks, contract line, leftover `harness_sha`); Expose current operational status (per kind); Classify admission holds by scope.
- Boundary: `operations.doctor` and `operations.status` over a temporary configuration, a real Git checkout standing in for Runner, and the store.
- Setup: A Runner checkout whose branch head contains `workflows/core/factory-fix-v1.0.yaml` with the contract line, and a second checkout without it; fix env files that are valid, missing `GH_TOKEN`, carrying an extra variable, and carrying the App installation token's value; a Runner checkout containing a `.sandbox-secrets.env`; a shared TOML retaining `harness_sha`; a store with an eval run active, a fix claim blocked, a pending sync failure, and a Codex quota hold with fix roles on Cursor.
- Action: Run doctor and status.
- Assertions: Doctor reports the contract check as available or names the missing line; rejects the env files with an extra variable or the App token as readiness failures; the launcher's plan passes `--no-default-secrets` and an env file containing only `GH_TOKEN` even when the checkout has a `.sandbox-secrets.env`; fails configuration loading naming `harness_ref` when `harness_sha` remains; eval and fix diagnostics are listed separately. Status prints the eval slot holder, the fix slot as free, the blocked claim with its reason, the pending sync with its last failure, and shows the Codex hold as not blocking fix admission.
- Execution: `tests/integration/test_cli_operations.py` (extend); `uv run pytest`.

### INT-008: Fix limits and window reach the supervisor
- Covers: Apply fix-specific limits and window; Enforce separate progress and runtime limits (fix values).
- Boundary: `FixHandler.limits` and `window` through `launch_supervisor` and the supervisor's timer logic with a controlled long-running executable.
- Setup: Local configuration with fix limits of a few seconds and an always-open fix window while the eval window is closed; controlled executables that go silent, run past the execution limit, and run past the total limit.
- Action: Admit a bug outside the eval window; run each executable under supervision.
- Assertions: Admission succeeds while eval admission is refused for the window. The run record stores the fix limits, not the eval limits. Each executable is stopped for the specific limit it exceeded, evidence is preserved, and the single recovery retry is consumed exactly once.
- Execution: `tests/integration/test_fix_limits.py`; `uv run pytest`.

### INT-009: Harness branch resolved per claim and rendered in eval Refs
- Covers: Freeze accepted evaluation inputs (harness branch); Evaluate Runner and Skills using an existing suite (harness commit per claim); Admit two evals on different days; Advance the harness branch during a claim.
- Boundary: `EvalHandler.accept` and reporting over a real temporary `agent-evals` remote and local checkout, with the GitHub stub.
- Setup: A harness remote at commit A; an eval request; after admission, advance the remote to commit B.
- Action: Admit the claim, force a technical failure and retry, then admit a second fresh claim.
- Assertions: The first claim and its retry record and use A; the second records B; both cards' Refs read `runner@… skills@… evals@…` with the respective commits; the frozen-inputs comment carries the harness commit; a shared configuration containing `harness_sha` fails to load naming `harness_ref`.
- Execution: `tests/integration/test_revision_resolution.py` (extend); `uv run pytest`.

## End-to-End Tests

### E2E-001: Handler extraction preserves the eval suite
- Covers: Keep lifecycle behavior independent of work kind; Extract the interface without changing eval behavior.
- Surface: The entire existing test suite.
- Setup: The extraction commit and nothing else from this change.
- Journey: Run `uv run pytest` at the extraction commit.
- Assertions: Every pre-existing test passes with no edits other than constructing `Controller` with `{"eval": EvalHandler(...)}` where tests build it directly. Any other test modification in the extraction task is a review finding.
- Execution: Existing `tests/`; enforced by task ordering and PR review, not a new test file.

### E2E-002: Fix journey through the CLI with controlled sandbox and GitHub
- Covers: Select eligible bugs; Resolve branches once per claim; Clone from local mirrors; Invoke the versioned fix workflow; Map fix outcomes; Recover from a fresh clone; Reconcile side effects; Sync the working clone; Clean up worktrees after review (clones and images).
- Surface: Installed factory CLI (`tick`, `status`) through the normal admission path.
- Setup: Isolated root and SQLite; real temporary Git remotes for a target repository, Runner, and Skills; a controlled `sandbox-run.sh` in the Runner checkout that records its arguments, mounts, and environment names and writes a scripted `fix-outcome.json`; a controlled `docker` stub implementing `info`, `stats`, `ps`, `inspect`, and `rmi` for the memory probe, discovery, and cleanup; the local GitHub stub serving cards, comments, permissions, PR lookups, and issue close; a working clone of the target on branch `dev`.
- Journey: Route a writer Bug to Ready; tick to admit; verify mirror fetch and clones at the recorded commits; the stub writes `pull-request`; tick to observe Review and the PR link; the stub marks the PR merged; tick to observe the sync into `dev`, the issue close, and Done; tick to observe clone and image removal. Second pass: stub crashes after "opening" a PR without writing an outcome; tick; reconciliation finds the PR and settles without a relaunch. Third pass: stub writes `failed`; observe Review with `failed`. Fourth pass: advance the target branch after admission and force a technical failure; the retry clones the original commits. Fifth pass: a human closes the issue before the merged PR is observed; the sync still runs once and no cancellation is recorded. Sixth pass: with a real `agent-evals` checkout available, run the real and-scene `run.sh --dry-run` from an eval plan whose environment sets `IMAGE`, and assert the printed build command carries the per-run tag.
- Assertions: Argv contains `--image agent-runner-factory:<run>`, `--no-default-secrets`, the repo mount read-write, the Skills mount read-only, and an env file whose only variable is `GH_TOKEN`; the App token value appears nowhere in the recorded arguments or environment. `issue.json` matches the issue and eligible comments. Refs renders `target@ runner@ skills@`. Board and label transitions match the reporting spec. The working clone's `dev` contains the merge commit and its tree is otherwise untouched. Clones and the image tag are removed only after Done, and mirrors remain. Claim, run, and event history are intact across passes.
- Execution: `tests/e2e/test_fix_cycle.py`; `uv run pytest`; Git required, no network or Docker.

### E2E-003: Two slots with real processes and controller restart
- Covers: Run an eval and a fix together; Preserve running evaluations across controller restarts (applied to both kinds); Attempt simultaneous dispatch per kind.
- Surface: Installed CLI with real supervisor processes.
- Setup: As in the existing independent-execution test: real long-running controlled executables for the suite and the fix launcher, real supervisor sessions, isolated root.
- Journey: Admit an eval and a fix so both run; attempt a second of each through `tick`; kill and restart the controller; let each executable finish with a scripted result.
- Assertions: Two non-terminal runs exist with different kinds and different supervisors; the second of each kind is refused while status remains usable; both executables survive the controller restart and are re-observed; each result is recorded once against its own claim; the memory probe stub was consulted before each admission.
- Execution: `tests/e2e/test_independent_execution.py` (extend) with the `darwin` marker; run on macOS hosts.

### E2E-004: Real Docker fix launch from a factory-prepared Runner clone
- Covers: Invoke the versioned fix workflow (real mounts, environment, and build context); Clone from local mirrors (the clone is what the sandbox builds); Isolate concurrent sandbox builds; Verify execution ownership (container discovered by artifact mount and image).
- Surface: The factory launcher invoking the real `scripts/sandbox-run.sh` from the per-attempt Runner clone the factory prepared.
- Setup: Docker host; a temporary Runner remote derived from a real checkout plus one committed, command-only test workflow (no agent steps) that writes `fix-outcome.json` and records the environment variable names, mount modes, and `AGENT_RUNNER_SOURCE_COMMIT` it sees; the factory prepares the clones at that commit; a throwaway env file with a dummy `GH_TOKEN`; a `.sandbox-secrets.env` planted in the clone to prove it is not loaded.
- Journey: Launch one fix attempt running the real built Agent Runner against the test workflow; while it runs, launch a second with a different run id; let both finish.
- Assertions: Each container runs an image tagged with its own run id and reports the clone's recorded commit as its source commit; `/workspace/repo` is writable and `/workspace/skills` read-only inside; the container environment contains `GH_TOKEN` and no variable from the planted secrets file; the supervisor discovers each container by its exact artifact mount and image; the outcome file is read back; images are removed on cleanup. No model calls.
- Execution: `tests/e2e/test_docker_fix_launch.py` with the `docker` marker; test-owned containers and images only, cleaned after the test.

## Agent Acceptance Tests

### AT-001: Live bug routing and bypass
- Classification: Required
- Covers: Route bug reports to the factory; Restrict factory assignment to repository writers; Deploy a new rule with existing issues; Provision the initial GitHub deployment (labels).
- Actor and surface: Acceptance agent through the operator's GitHub CLI and the deployed Actions routing, plus the installed factory CLI.
- Setup: Factory paused. Caller workflows bumped to the published factory revision. The `factory-hold` label and the "Bug (tracking only)" template present in the five repositories.
- Steps: Create a clearly marked disposable Bug-typed issue as the operator in one target repository; observe the Actions run and the card. Create a second marked Bug from the "Bug (tracking only)" template; observe. Create a marked Task-typed issue; observe. Run `tick` while paused and `status`.
- Expected: First Bug → `Owner=factory`, `Status=Ready` with a routing receipt. Held Bug → `Owner=human`, Backlog. Task → Backlog, no ownership. No execution while paused; status shows the fix slot free and admission paused. Pre-existing open Bugs are untouched.
- Evidence: Issue URLs, Actions run URLs and revision, before/after field values via the Project API, CLI status output.
- Effects and cleanup: Three marked issues, their cards, workflow runs, and comments. Close and delete the held Bug and the Task after evidence; keep the first Bug only if AT-003 will reuse it, otherwise delete. Ordinary Actions usage, no model calls.
- Permitted substitutes: CLI/API issue creation replaces browser interaction. No stub GitHub or direct routing invocation may replace issue-triggered routing.

### AT-002: One real sandboxed fix producing a ready PR
- Classification: Required
- Covers: Select eligible bugs; Resolve branches once per claim; Clone from local mirrors; Invoke the versioned fix workflow; Fix or decline autonomously (fix path); Apply fix-specific limits; Comment on fix activity; Map fix outcomes (pull-request); Diagnose readiness (fix checks).
- Actor and surface: Acceptance agent through the operator's GitHub CLI, the installed factory CLI on the mini, and the live board.
- Setup: Run `doctor`; both kinds must be green. In one target repository, seed a clearly marked, trivially reproducible defect in a small isolated module with a failing test (for example an off-by-one in a helper covered by a new test), landing it on `main` through a normal PR merged by the operator's identity, since the ruleset requires PRs. File a marked Bug-typed issue describing it with reproduction steps, authored by the operator. Fix roles per shared configuration. Resume the factory or run `tick` inside the fix window.
- Steps: Observe admission (comment with refs, card to Running, `status` naming the fix slot holder). Wait for completion within the fix limits. Inspect the PR.
- Expected: A non-draft PR on branch `factory/fix-<n>-<claim8>` referencing the issue without a closing keyword, carrying the claim marker, a regression test, a validator result, and green CI. Card in Review with `Verdict=pending-human-review`, Refs `target@ runner@ skills@` matching the claim, and the PR linked in a comment. The issue remains open. `status` shows the fix slot free again.
- Evidence: Issue and PR URLs, comment URLs, board field values, `status` before and after, the artifact directory listing with `fix-outcome.json`, the run's image tag.
- Effects and cleanup: One seeded defect commit on `main` of a target repository, one Bug issue, one factory branch and PR, model usage for one fix, one sandbox image. The PR is consumed by AT-004; do not delete the branch until AT-004 completes.
- Permitted substitutes: None. A stubbed sandbox, a pre-written outcome, or a manual PR does not satisfy this flow.

### AT-003: Decline, block, and unblock through a comment
- Classification: Required
- Covers: Fix or decline autonomously (decline path); Map fix outcomes (needs-input); Recognize fix retry gestures (unblock); Leave a blocked fix in Running; Read a kind-specific gesture.
- Actor and surface: As AT-002.
- Setup: File a marked Bug that is deliberately ambiguous (two plausible behaviors, no reproduction, or a request that plainly needs a specification change), authored by the operator.
- Steps: Let the factory admit it. Observe the decline. Post a non-writer comment if a second account is available; otherwise skip that sub-step and note it. Post an operator comment resolving the ambiguity. Observe the next poll.
- Expected: Card stays in Running with the `needs-input` label and a decline comment naming the reason (including "belongs in a Feature" when a spec change is the cause). The fix slot is free while blocked. A non-writer comment changes nothing. After the operator comment, the label is removed, a new attempt starts with the comment in its input, and the flow ends in either a PR or a second decline that cites the new information.
- Evidence: Comment URLs with timestamps, label history, `status` output showing blocked then running, the factory's own decline comment not triggering a retry, the new attempt's `issue.json` contents.
- Effects and cleanup: One Bug issue, up to two attempts of model usage, possibly a PR. Close the issue and delete any branch after evidence.
- Permitted substitutes: The non-writer comment sub-step may be skipped when no second account exists; record the omission.

### AT-004: Merge the fix and sync the operator's working clone
- Classification: Required
- Covers: Sync the working clone after merge (clean and dirty cases); Clean up worktrees after review (clones and images); Close tracked work.
- Actor and surface: Acceptance agent with the operator's GitHub CLI, authorized to merge the AT-002 PR, plus the mini's filesystem.
- Setup: AT-002 PR open and green. The operator's configured working clone of the target repository on its current non-main branch with a clean tree. Record the branch and `HEAD`.
- Steps: First, create a throwaway tracked modification in the working clone and merge the PR; observe the next poll. Then revert the throwaway modification; observe the next poll. Finally inspect clones and images.
- Expected: With the dirty tree, the card stays in Review, gains `needs-input`, and a comment names uncommitted changes; the working tree, index, and branches are untouched. After the revert, the next poll merges `main` into the checked-out branch, the label is removed, the issue is closed with a marker comment, closure automation moves the card to Done, and the next poll removes the claim's clones and image while the mirror remains.
- Evidence: `git log` and `git status` of the working clone before and after, the comment URLs, board status history, directory listings under the storage root, `docker image ls` filtered by the run tag.
- Effects and cleanup: The seeded-bug fix merges into `main` of the target repository (authorized). Remove the seeded defect and its test by a follow-up PR or leave them if harmless and marked; the operator decides at handoff. Delete the factory branch if GitHub did not.
- Permitted substitutes: None for the clean-tree merge and sync. The dirty-tree case may be exercised with any harmless tracked edit.

### AT-005: Credential containment and doctor readiness
- Classification: Required
- Covers: Provision the initial GitHub deployment (fix credential, ruleset); Push to main with the fix credential; Diagnose fix readiness; Apply shared deployment changes (harness branch, no commit pins).
- Actor and surface: Acceptance agent through the GitHub CLI using the fix credential file, and the installed factory CLI.
- Setup: Fix credential provisioned in its env file; rulesets applied to the five target repositories.
- Steps: Using only the fix credential, attempt a direct push of an empty commit to `main` of one target repository; attempt to create a branch and open a PR. Run `doctor`. Temporarily reintroduce `harness_sha` in a copy of the shared configuration and run `doctor` against it.
- Expected: The direct push is rejected by the ruleset; branch and PR creation succeed. Doctor reports the contract line at the resolved Runner branch, the credential's identity (not the App, not an org admin), each mirror and working clone, Docker memory allowance, and the harness branch with its resolved commit. The copy with `harness_sha` fails configuration loading naming `harness_ref`.
- Evidence: Command outputs including the ruleset rejection message, doctor output for both configurations.
- Effects and cleanup: One throwaway branch and PR, closed and deleted afterwards. No model usage.
- Permitted substitutes: None.

## Human-Only Testing

None. Paul authorized the acceptance agent to merge the seeded-bug PR in AT-004, and the drag-to-Ready gesture is exercised via the Project API in INT-006 and E2E-002 with the UI-to-API relationship already established by iteration 1's HT-001 obligation.

## Coverage Map

| Requirement or journey | INT | E2E | AT | HT |
| --- | --- | --- | --- | --- |
| Keep lifecycle behavior independent of work kind (extraction) | — | E2E-001 | — | — |
| Prevent overlapping execution (per-kind slots, migration) | INT-001 | E2E-003 | — | — |
| Check memory headroom before admission | INT-005 | E2E-003 | — | — |
| Classify admission holds by scope | INT-007 | — | — | — |
| Route bug reports; writer restriction; bypass; precedence | INT-002 | — | AT-001 | — |
| Select eligible bugs in board order | — | E2E-002 | AT-002 | — |
| Resolve branches once per claim | — | E2E-002 | AT-002 | — |
| Recognize fix retry gestures; kind-specific gesture; blocked in Running; blocked drags | INT-006 | — | AT-003 | — |
| Exempt settled work from closure cancellation | — | E2E-002 | — | — |
| Reconcile side effects before launching | INT-006 | E2E-002 | — | — |
| Clone from local mirrors at recorded commits | — | E2E-002 | AT-002 | — |
| Invoke the versioned fix workflow; outcome contract; credential containment | INT-003, INT-007 | E2E-002, E2E-004 | AT-002, AT-005 | — |
| Fix or decline autonomously | — | — | AT-002, AT-003 | — |
| Apply fix-specific limits and window | INT-008 | — | AT-002 | — |
| Recover a fix attempt from a fresh clone | INT-003 | E2E-002 | — | — |
| Isolate concurrent sandbox builds (image tags, eval IMAGE plumbing) | INT-005 | E2E-002, E2E-004 | — | — |
| Map fix outcomes to the board | INT-003 | E2E-002 | AT-002, AT-003 | — |
| Sync the working clone after merge | INT-004 | E2E-002 | AT-004 | — |
| Clean up clones and images after Done | — | E2E-002 | AT-004 | — |
| Diagnose readiness with doctor (fix, harness branch) | INT-007 | — | AT-005 | — |
| Freeze harness branch per claim; eval Refs rendering | INT-009 | — | — | — |
| Expose current operational status (per kind) | INT-007 | — | AT-001 | — |
| Provision labels, template, ruleset, and fix credential | — | — | AT-001, AT-005 | — |
| Preserve running work across controller restart (both kinds) | — | E2E-003 | — | — |
