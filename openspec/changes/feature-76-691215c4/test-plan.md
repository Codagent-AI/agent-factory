## Coverage Strategy

The specifications are the source of unit-test requirements. They cover:

- `TASK` definition fields and `registered()` order;
- config value validation;
- triage-decision parsing;
- title and subject rewriting rules;
- floor path classification;
- `assign.py` classification.

This plan records only three things on top of that:

- the integration and end-to-end obligations that prove the new kind works with the real
  store, scripts, git, packaged catalog, and `tick` cycle;
- the acceptance testing envelope;
- one human-only check of the first live task.

The repository has no CI test job. Every obligation runs in `uv run pytest`, which the
validator's `test` check runs. Tests that need the Codagent `agent-runner` CLI skip when it
is absent, as the existing catalog tests do.

The packaged workflows are not executed with stub agents in automated tests, which matches
the current practice for `factory-fix` and `factory-feature`. Their control flow is covered
in three ways:

- structural assertions on step order and `skip_if` gates;
- `agent-runner -validate`;
- subprocess tests of every script the steps call.

The acceptance pass exercises real execution.

## Integration Tests

### INT-001: Task configuration reaches the runtime
- Covers: `factory-operations` "Configure deployment" (task scenarios); `factory-task-intake` "Use a custom task type name"; `factory-task-execution` "Apply task limits, window, and recovery" (defaults).
- Boundary: TOML files → `load_shared_config` / `load_local_config` → `work_kinds.handlers()` → `PullRequestHandler(TASK)` limits, window, roles, and contract.
- Setup: temporary shared and local TOML files, covering five cases:
  - with `[task]` and `[task.defaults]`;
  - without `[task]`;
  - with `task.execution = "docker"`;
  - with a wrong-type `task.limits.total_seconds`;
  - with `[routing] task_type = "Chore"`.

  Also the committed `config/codagent.toml`.
- Action: load each configuration and build the handlers.
- Assertions:
  - With `[task]` and no local task section, the task handler is enabled, with limits
    900/7200/10800, an always-open window, contract `factory-task/1`, and issue type `Task`
    (or `Chore` when configured).
  - Without `[task]`, `TASK.enabled` is false, and the fix and feature handlers are
    identical to the same configuration without the new keys.
  - Docker execution and the wrong-type value each raise `ConfigurationError` naming the
    `task.*` key.
  - Feature defaults (1800/21600/28800) are unchanged after the parser refactor.
  - `config/codagent.toml` loads with the task kind enabled, a `claude:claude-sonnet-…`
    lead, and a `cli:model:effort` profile for every role.
- Execution: `tests/integration/test_task_config.py`.

### INT-002: Task handoff, eligibility, and slot independence against the store
- Covers: `factory-task-intake` "Hand off tasks to the factory through Ready", "Select eligible tasks by Priority", and "Resolve task branches once per claim"; `factory-task-execution` "Treat task claims as pull-request claims" (own slot, blocked claim holds no slot).
- Boundary: the Project queue snapshot and the GitHub permission fake → `PullRequestHandler(TASK)` handoff, snapshot, and admission → the real SQLite `ClaimStore` with the per-kind unique index.
- Setup: the existing fake GitHub client and board fixtures used by `test_feature_intake.py`, with Task-typed items in a fix target:
  - writer and non-writer authors;
  - an unconfigured repository;
  - differing Priority and creation times;
  - a `needs-input` label;
  - Bug and Feature items alongside.
- Action: run handoff and admission cycles.
- Assertions:
  - A writer's Task in Ready gets `Owner=factory`.
  - A non-writer's Task does not, and gets exactly one explanation comment carrying
    `agent-factory-task-handoff:v1` across repeated cycles. Its feature counterpart still
    uses `agent-factory-feature-handoff:v1`.
  - A Task from an unconfigured repository is untouched.
  - Without `[task]`, nothing is handed off.
  - Selection follows Priority, then newest. A `needs-input` Task is skipped.
  - Fix, feature, and task runs can be reserved at the same time, and a second task
    reservation raises `NonterminalRunError`.
  - Recorded revisions render as `target@<7> runner@<7> skills@<7>`.
  - With `[task]` present, the Bug and Feature items are admitted with exactly the same
    claims and events as without it.
- Execution: `tests/integration/test_task_intake.py`.

### INT-003: Packaged task workflow and modified shared workflows form a valid catalog
- Covers: `factory-task-execution` "Invoke the versioned task workflow", "Triage a task against its risk boundary" (no branch before acceptance), "Exercise new and tightened gates", and "Mark task commits and pull requests as chores"; `factory-review-execution` "Hold task scope through review rounds" (wiring).
- Boundary: the packaged files → `launch.stage_workflow_into` → the project-scope catalog in a temporary repository → the real `agent-runner -validate`, plus the host wrapper script.
- Setup: a temporary git repository and the Codagent `agent-runner` on PATH. The test skips when the CLI is absent.
- Action:
  - stage the catalog for `TASK`, then validate `factory-task-v1.0.yaml`,
    `factory-review-v1.0.yaml`, `factory-implement-v1.0.yaml`, and
    `factory-fix-v1.0.yaml`;
  - build the host wrapper for a task attempt.
- Assertions:
  - Every task staged file is present, and the first line of every packaged workflow
    declares its contract.
  - All four workflows validate.
  - In `factory-task`, `create-branch` sits inside the `implement` group, which is skipped
    unless triage accepted. `record-triage` receives `contract: factory-task/1` and
    `accept_field: doable`.
  - `exercise-gates`, `verify-gate-exercises` (lead session), and `check-gate-exercises`
    precede `review-task`.
  - The pre-push `factory-task-guard` call takes the recorded target commit as `base` and
    comes after `address-findings` and the final validator recheck. So a crossing that an
    initial implementation or a finding repair introduces is checked on the complete diff.
    Its crossing skips `normalize-chore-commits` and `finalize-pr`.
  - `normalize-chore-commits` precedes `finalize-pr`. The post-finalize guard follows
    `finalize-pr`, gated on the head having moved, and precedes `record-outcome`.
    `annotate-chore-pr` follows `finalize-pr`.
  - `factory-implement` runs the pre-push guard before `finalize-pr` and the post-finalize
    guard after it, both gated on `task_scope`.
  - `factory-task-guard-v1.0.yaml` validates, and is staged for every kind.
  - The implement prompt does not request implement-with-tdd.
  - In `factory-review`, the task paragraph and the `task_scope`/`scope_base` parameters
    are present, and `respond` skips on `scope-stop.json`.
  - In `factory-implement`, every new step is gated on `task_scope`. With default
    parameters, its step list and prompts for fix and feature are unchanged from the base
    revision apart from the added gated steps.
  - The host wrapper runs `factory-task` with `issue_file`, `branch_name=factory/task-…`,
    `contract_version=factory-task/1`, and `artifact_dir`, and with no feature-only
    parameters.
- Execution: `tests/integration/test_task_workflow_catalog.py`.

### INT-004: Task workflow scripts against real git and a fake `gh`
- Covers: `factory-task-execution` "Triage a task against its risk boundary" (a decline writes the outcome), "Accept bounded, evidence-based choices" (recording), "Exercise new and tightened gates", and "Mark task commits and pull requests as chores"; `factory-review-execution` "Hold task scope through review rounds" (outcome mapping); the unchanged fix triage behaviour.
- Boundary: each script runs as a subprocess against temporary git repositories, artifact directories, and a `gh` stub first on PATH that records requests and can be told to fail.
- Setup: temporary repositories with linear commits, a merge commit, and an upstream branch; triage decisions; `gate-exercises.json` variants; a `task-choices.json`.
- Action and assertions:
  - **`record-triage.sh` with fix defaults:** stdout and the `fix-outcome.json` bytes are
    identical to the base revision's for the existing decline and accept fixtures.
  - **`record-triage.sh` with task inputs:** a decline writes `task-outcome.json` with
    `contract: factory-task/1` and `needs-input` plus reasons, writes the parsed decision to
    `decision_path`, and prints `false`. An accept prints `true` and writes no outcome.
  - **`check-gate-exercises.py`:** it prints `passed` only when every triage gate has:
    - positive and negative commands equal to the triage-named command;
    - a positive exit of 0 and a non-zero negative exit;
    - a non-empty planted patch;
    - a confirmed verdict with `criterion_met`, whose diagnostic occurs in the negative log
      and not in the positive log;
    - a clean tree.

    Each failure mode is reported with its reason, including an unrelated non-zero failure
    (a configuration error output with no confirmed diagnostic) and a diagnostic that also
    appears in the positive log.
  - **`record-scope.sh` with `task-scope-floor.py`:** on a branch whose diff edits
    `.github/workflows/release.yml`, it records `crossed` with the path, even when the lead
    verdict says clean. On a diff with only `ruff.toml`, it follows the lead verdict.
  - **`record-outcome.sh` with `scope_path`:**
    - a pre-push crossing yields `needs-input` with the reasons and no PR;
    - a post-finalize crossing yields `failed` with the reasons and keeps the PR reference;
    - without `scope_path`, the existing fixtures yield unchanged outcomes.
  - **`check-chore-subjects.py`:** it lists the subjects in a range that lack `chore:` and
    exits 0.
  - **`task-scope-floor.py`:** it flags `openspec/specs/x.md`, a `release.yml` workflow, a
    workflow triggered on tag push, and `CODEOWNERS`. It does not flag `ruff.toml`,
    `.github/workflows/ci.yml` (lint and test), or `tests/`.
  - **`normalize-chore-commits.py`:** subjects become `[step] chore: …` and a leading
    `fix:` or `feat(x)!:` is replaced. Each commit's tree, author, and author and committer
    dates are unchanged, and the working tree is untouched. On a range with a merge commit,
    or a branch with an upstream, it changes nothing and exits with the reason.
  - **`annotate-chore-pr.sh`:**
    - It sends one PATCH that prepends `Refs #N` and the claim marker, appends the
      `agent-factory:task-evidence` section with choices and gate results, and sets the
      title to `chore: …`, replacing a `fix:` prefix.
    - A second run sends no change.
    - When the title PATCH fails, it writes `retitle-failed` and exits 0.
  - **`record-review-outcome.sh`:** a pre-push crossing yields `needs-input` and a
    post-finalize crossing yields `failed`, each with the reasons. Existing review fixtures
    yield unchanged outcomes.
- Execution: `tests/integration/test_task_workflow_scripts.py`.

### INT-005: Task outcomes through the handler, reporting, and board
- Covers: the `factory-task-execution` outcome contract; `factory-task-reporting` "Comment on task activity", "Map task outcomes to the board", and "Deliver task reports durably without duplicates".
- Boundary: `task-outcome.json` in an attempt directory → `read_interpreted_outcome`, `supervisor._load_result`, and `PullRequestHandler.classify` → controller reporting → the fake board and comment recorder.
- Setup: the store with a running task claim and attempt evidence directories holding:
  - a valid `pull-request` outcome;
  - a `needs-input` outcome;
  - a `failed` outcome with a PR;
  - a wrong contract;
  - an unknown outcome value;
  - no outcome file.
- Action: consume each result in a controller cycle.
- Assertions:
  - The valid outcomes map as the spec requires:
    - `pull-request` goes to Review with `pending-human-review`;
    - `needs-input` stays in Running with the label, the slot freed;
    - `failed` goes to Review with `failed`.
  - The wrong contract, the unknown value, and the missing file are technical failures that
    take the recovery path.
  - The decline comment lists the reasons and contains "No branch was pushed.". The
    acceptance comment reads "Task inputs accepted and frozen.". Outcome comments carry the
    host note.
  - When a lost comment response is replayed, the marker is found and no second comment
    is posted.
- Execution: `tests/integration/test_task_outcomes.py`.

### INT-006: Review rounds on task claims
- Covers: `factory-pull-request-lifecycle` "Detect eligible review comments", "Re-admit a review round through the claim's kind slot" (task scenario), and "Sync the working clone" (task claims); `factory-review-execution` "Run the versioned review workflow" and "Merge the target branch…" (task rounds do not merge); `factory-task-reporting` "Park a review round that left task scope".
- Boundary: the fake GitHub PR reviews, threads, and comments → review admission → `review.json` written by the handler → consuming `review-outcome.json` → the board.
- Setup: a settled task claim with an open PR, a new Ready Task, and the task slot free.
- Action: post a writer's PR comment and run cycles. Then consume a `needs-input` review outcome. Then mark the PR merged against a temporary working clone.
- Assertions:
  - The review round is admitted through the task slot before the new Task, with run
    reason `review`.
  - `review.json` has `kind: task` and no `base_head`.
  - The `needs-input` outcome returns the card to Review with the label and the restored
    verdict.
  - The merged PR triggers the sync, which closes the issue.
- Execution: `tests/integration/test_task_review.py`.

### INT-007: Watch events for task attempts
- Covers: `factory-watch-dispatch` "Detect watch events in every cycle" (task scenarios).
- Boundary: the store's runs → `watch/detect.py` → the watch store queue.
- Setup: watching enabled. One task run completed with `pull-request`. One task run failed, its result consumed, past the grace period.
- Action: run detection twice.
- Assertions: one `PR-READY` and one `FAILURE` event are queued for the task runs, and the second pass queues none.
- Execution: extend `tests/integration/test_watch_detection.py`.

### INT-008: Doctor, status, and the assign skill cover tasks
- Covers: `factory-operations` "Diagnose readiness with doctor" (task-host), "Expose current operational status" (task lines), and "Assign and report tasks through the operator skills".
- Boundary: the `doctor` and `status` CLI entry points against a temporary root and store; `assign.py` against fake GitHub responses.
- Setup:
  - configurations with and without `[task]`;
  - a task role lacking a profile;
  - a store with a running task attempt and a blocked task claim.
- Action: run `agent-factory doctor` and `status`, and `assign.py … --apply task` / `--apply fix` on Task, Bug, and Feature issues.
- Assertions:
  - Doctor prints a `task-host` group only when `[task]` is present. The group fails naming
    the missing role profile.
  - Status shows a `task slot:` line with its holder and the blocked task with its decline
    reason.
  - `scripts/slots.sh` `slots_free` returns false while a task runs, and true when every
    slot is free.
  - `--apply task` sets Owner, Status, and Priority on a writer's Task and reports a task
    claim.
  - `--apply task` on a Bug, and `--apply fix` on a Task, are refused, naming the kind.
- Execution: `tests/integration/test_task_operations.py`; `tests/unit/test_factory_assign.py` (extended).

## End-to-End Tests

### E2E-001: A writer's Task travels from Ready to a `chore:` PR, sync, and cleanup
- Covers: issue acceptance criterion 1; `factory-task-intake` handoff and admission; `factory-task-execution` host launch; `factory-task-reporting`; `factory-pull-request-lifecycle` merge sync.
- Surface: the `agent-factory tick` cycle through the `tests/e2e/test_fix_cycle.py` `Harness` in host mode.
- Setup: the harness with a `[task]` section, and a board item typed `Task` in Ready with no Owner. The stub runner writes `task-outcome.json` with a `pull-request` whose title is `chore: …`.
- Journey: tick for handoff and launch; wait for the stub to finish; tick to consume the result; mark the PR merged; tick for sync; move the card to Done; tick for cleanup.
- Assertions:
  - `Owner=factory` is set, and one nonterminal run of kind `task` exists with backend
    `host`.
  - The runner arguments include `factory-task`, `branch_name=factory/task-1-<claim8>`, and
    `contract_version=factory-task/1`.
  - The staged catalog in the clone holds `factory-task-v1.0.yaml`.
  - The fix token is absent from the persisted plan.
  - The card reaches Review with `pending-human-review`, and the PR comment carries the
    host note.
  - The sync closes the issue, and cleanup removes the clones while keeping
    `task-outcome.json`.
- Execution: `tests/e2e/test_task_cycle.py::test_task_happy_path`.

### E2E-002: A declined Task blocks with no branch, then a writer's answer relaunches it
- Covers: issue acceptance criterion 2; `factory-task-intake` "Recognize task retry gestures"; `factory-task-reporting` "Park a declined task".
- Surface: the `agent-factory tick` cycle through the harness in host mode.
- Setup: the stub runner writes a `needs-input` `task-outcome.json` on attempt 1 and pushes nothing. The harness's target remote is a local bare repository.
- Journey: tick, wait, tick; then add a writer's issue comment; then tick.
- Assertions:
  - After attempt 1, the card is in Running with the `needs-input` label, and the comment
    lists the reasons and says "No branch was pushed.".
  - The bare remote has no `refs/heads/factory/task-*`, and the task slot is free.
  - After the comment, the label is removed and attempt 2 launches with the writer's
    comment in `issue.json`.
- Execution: `tests/e2e/test_task_cycle.py::test_declined_task_blocks_then_relaunches`.

### E2E-003: Bugs, Features, and Tasks are admitted side by side, each as its own kind
- Covers: issue acceptance criterion 3; `factory-task-intake` "Bugs and Features are admitted as before" and "Admit a task while a fix and a feature run".
- Surface: one `agent-factory tick` through the harness in host mode with all three kinds configured.
- Setup: one writer's Bug, one Feature, and one Task in Ready, all in fix targets.
- Journey: one tick.
- Assertions:
  - There are three nonterminal runs, of kinds `fix`, `feature`, and `task`, each in its
    own slot.
  - The runner arguments name `factory-fix`, `factory-feature`, and `factory-task`
    respectively, and the branch prefixes are `factory/fix`, `factory/feature`, and
    `factory/task`.
  - The fix and feature launches' arguments and staged catalogs match a run of the same
    board without `[task]`, apart from the extra task files staged in the catalog.
- Execution: `tests/e2e/test_task_cycle.py::test_three_kinds_admitted_independently`.

## Acceptance Testing Envelope

- **Environments and sandboxes:** Paul's Mac, inside the attempt's clone.
  - Use an isolated storage root: a temporary directory with its own `local.toml`,
    `state.sqlite3`, mirrors, clones, and artifacts.
  - The `tests/e2e/test_fix_cycle.py` fixtures provide the stub GitHub board and comment
    recorder.
  - For real-workflow runs, the scratch target is a local repository. Its origin is a local
    bare repository, seeded from a small Python project with ruff configured, such as a
    copy of `tests/fixtures` code with a `pyproject.toml`.
- **Credentials and secrets:**
  - Claude and Codex logins exist in the operator's Keychain and CLI configuration, and the
    operator's `gh` login exists.
  - The Factory App key and the fix credential are under `~/.agent-factory/private/`. The
    pass MUST NOT read or print anything there.
  - Real-workflow runs authenticate git to the local bare origin only, and use a `gh` stub
    for every GitHub call.
- **Authorized effects:**
  - Any number of runs with the model-free stand-in `agent-runner`.
  - Up to three real headless task attempts through the installed Codagent `agent-runner`
    with the committed `[task.defaults]` roles, against the scratch target. The estimated
    total is $15. They are:
    1. a Task that requires a behavior change, which should decline before any branch;
    2. a Task to tighten lint configuration with fix-or-baseline delegated and a
       duplication gate with a stated rejection criterion, which should produce gate
       exercises, `chore:` commits, and a `chore:` title;
    3. one task review round on that PR, with a stubbed reviewer comment asking for an
       out-of-scope behavior change, which should return `needs-input` and push nothing.
  - The `gh` stub must answer the PR list, create, view, edit, PATCH, checks, and comment
    calls that `finalize-pr` and `annotate-chore-pr.sh` make, reporting CI as green.
  - Pushes go only to the local bare origin.
  - Remove the temporary directories afterwards, and confirm that no `agent-runner`,
    `claude`, or `codex` process started by the pass is left running.
- **Off limits:**
  - The live service: `~/.agent-factory/state.sqlite3`, `config.toml`, `releases/`, the
    service clone, and the live `mirrors/` and `clones/`.
  - The LaunchAgent, `scripts/deploy.sh`, and `pause` or `resume` on the live factory.
  - Paul's checkouts under `/Users/paul/codagent/`.
  - Any real GitHub issue, PR, comment, board item, issue type, or branch, including
    agent-factory#74, agent-validator#174, and agent-evals#53.
  - Fly Machines.
- **Permitted substitutes:**
  - The stand-in `agent-runner` for everything except the three authorized real attempts.
  - The `gh` stub and a local bare origin for GitHub.
  - If the installed Runner cannot complete `builtin:core/finalize-pr` against the stub,
    the pass records the gap and verifies the attempt up to the step before finalize. It
    then checks `normalize-chore-commits` and `annotate-chore-pr.sh` directly against the
    branch the attempt produced.
- **Known risk areas:**
  - Triage judgment at the boundary: behavior-preserving refactors, delegated thresholds,
    and runtime versus dev dependencies. These are prompt-judged.
  - Regressions in shared files: `record-triage.sh` defaults, the task paragraph in
    `factory-review` and the gated `factory-implement` steps affecting fix or feature
    rounds, and the refactored config parsers changing feature defaults.
  - CI repair inside `builtin:core/finalize-pr`, which pushes before the post-finalize
    guard sees its commits. Exploration should try a CI failure whose natural repair
    crosses the boundary.
  - Gate proof: a negative exercise that fails for an unrelated reason.
  - History rewriting in `normalize-chore-commits.py`, especially on step-marker subjects
    and multi-line messages.
  - `skip_if` chains where an undefined capture makes the Runner reject the workflow. Every
    new capture needs a seed step.
  - Status-line parsing in `scripts/slots.sh` once a fourth slot line appears.
  - Accepted limitations:
    - task scope is judged by prompts, with the floor catching only path-evident
      crossings;
    - a failed title correction leaves the PR untitled `chore:` and is recorded only in
      evidence;
    - rolling back with open task claims leaves them unhandled.

## Human-Only Testing

### HT-001: The first live Task after deploy
- Reason: this needs Paul's authority to deploy to the live service, to change a real issue's
  board fields, and to let the factory open a real PR on GitHub. These are outward-facing
  effects the acceptance envelope forbids.
- Prerequisites:
  - INT-001 through INT-008 and E2E-001 through E2E-003 pass in `uv run pytest`.
  - The acceptance pass completed its real attempts in isolation, or recorded why it could
    not.
  - After deploy, `doctor` shows the `task-host` group passing.
- Instructions:
  1. Before merging, list the open Task-typed issues in every fix target whose board Status
     is Ready. Move each one other than agent-factory#74 to Backlog, or approve it to run.
     The handoff assigns any writer-authored Task in Ready as soon as `[task]` is live.
  2. Merge, then deploy with `scripts/deploy.sh`.
  3. Run `factory-assign Codagent-AI/agent-factory 74 --apply task`.
  4. When the claim settles, open the issue, the PR, and `status`.
- Required decision or observation:
  - No Task other than #74, or one Paul approved, was claimed after deploy.
  - The claim is a `task` claim in the task slot.
  - Either a PR opened titled `chore: …`, with the claim marker, `Refs #74`, a
    task-evidence section recording the measured duplication and chosen threshold, gate
    exercise results, and `chore:` commits, or a decline names a specific decision.
  - Paul judges whether the triage call was right, and whether Tasks stay enabled or are
    disabled with a PR.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Issue acceptance 1: writer's Task becomes a task claim and a `chore:` PR | INT-002, INT-004 | E2E-001 | HT-001 |
| Issue acceptance 2: declined Task gets `needs-input`, no branch pushed | INT-004, INT-005 | E2E-002 | — |
| Issue acceptance 3: Bugs and Features admitted as before | INT-001, INT-002 | E2E-003 | — |
| factory-task-intake: handoff through Ready | INT-002 | E2E-001 | — |
| factory-task-intake: select eligible tasks by Priority | INT-002 | E2E-003 | — |
| factory-task-intake: resolve branches once per claim | INT-002 | — | — |
| factory-task-intake: retry gestures | INT-006 | E2E-002 | — |
| factory-task-execution: invoke the versioned task workflow | INT-003, INT-005 | E2E-001 | — |
| factory-task-execution: triage risk boundary | INT-003, INT-004 | E2E-002 | HT-001 |
| factory-task-execution: bounded choices (recording) | INT-004 | — | HT-001 |
| factory-task-execution: exercise new and tightened gates | INT-003, INT-004 | — | HT-001 |
| factory-task-execution: guard every pushed head (pre-push and post-finalize) | INT-003, INT-004 | — | — |
| Rollout guard: no unintended Ready Task claimed on enablement | — | — | HT-001 |
| factory-task-execution: `chore:` commits and title | INT-003, INT-004 | E2E-001 | HT-001 |
| factory-task-execution: limits, window, recovery | INT-001, INT-005 | — | — |
| factory-task-execution: shared lifecycle parity (slots) | INT-002, INT-008 | E2E-003 | — |
| factory-task-reporting: comments, board, durable delivery | INT-005, INT-006 | E2E-001, E2E-002 | — |
| factory-pull-request-lifecycle: task review rounds and merge sync | INT-006 | E2E-001 | — |
| factory-review-execution: hold task scope through review rounds | INT-003, INT-004, INT-006 | — | — |
| factory-watch-dispatch: task PR-READY and FAILURE | INT-007 | — | — |
| factory-operations: config, doctor, status, assign skill | INT-001, INT-008 | — | HT-001 |
