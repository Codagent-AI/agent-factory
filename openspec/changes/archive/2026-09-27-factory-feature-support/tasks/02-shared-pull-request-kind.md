# Task: Turn the fix kind into a shared pull-request work kind

## Goal

Replace the fix-specific coupling in the runtime and the fix package with one pull-request
work-kind implementation parameterized by a frozen per-kind definition, so a second
pull-request kind can be registered from the same class with data alone. Bug behavior is
unchanged: this task is a refactor whose only externally visible additions are the claim's
work kind in `review.json` and the parameterized shared workflow scripts, which produce the
same fix output as before.

## Background

Read `openspec/changes/factory-feature-support/design.md`, section "B. The shared
pull-request work kind" (B1 to B4, B6, B7), D6 ("Shared scripts"), and Decision 3. The
proposal (`proposal.md`, "What Changes" part 2) explains why this must precede adding the
feature kind.

Current coupling (paths relative to `src/agent_factory/`):

- `runtime.py` checks `isinstance(handler, FixHandler)` in several places and
  `claim.kind == "fix"` once; the Ready handoff is `runtime._assign_ready_bug`, using
  `shared.routing.bug_type`.
- `work_kinds/fix/blocked.py`, `review.py`, and `sync.py` hard-code the `fix` kind and unit
  (`store.nonterminal_runs(kind="fix")`, `claim.kind != "fix"`, `run.unit_key == "fix"`,
  the `fix-sync` marker). `retention.py` (`_removal_targets`) treats every non-fix claim as
  an eval.
- `work_kinds/fix/handler.py` hard-codes the `"Bug"` issue type in `handles()` and
  `snapshot()`; `launch.py` hard-codes the workflow name and file, the contract, the branch
  prefix, and `STAGED_FILES`; `outcome.py` maps the contract to the outcome filename with a
  ternary.
- `operations.py` iterates the literals `("eval", "fix")` for status slot lines and provider
  maps; doctor groups (`DiagnosticGroup`, `_GROUP_ORDER`), `host_executables`, and the
  LaunchAgent PATH gate are likewise literal.
- `work_kinds/__init__.py` `handlers()` registers `eval` and `fix`. Per-kind slots are
  already keyed by kind in SQLite (`one_nonterminal_run_per_kind`), so a new kind needs no
  schema change.
- Priority-then-newest-created ranking is already implemented for every queue in
  `github.py` (`_queue_order`, `_priority_rank`); the restated bug and eval selection
  requirements below need no new ranking code, only confirmation that the refactored
  handler still consumes that order.
- Workflow files live in `work_kinds/fix/workflow/`: `factory-fix-v1.0.yaml` (its contract
  check is inline in the `check-contract` step), `factory-review-v1.0.yaml`,
  `factory-implement-v1.0.yaml`, `record-outcome.sh`, `record-triage.sh`,
  `record-review-triage.sh`, `record-review-outcome.sh`, and the orphaned
  `read-regression-marker.sh` (not staged; only a test uses it).

Decisions to implement:

- **Package**: move `work_kinds/fix/` to `work_kinds/pull_request/`, keeping module names
  (`handler.py`, `launch.py`, `outcome.py`, `blocked.py`, `review.py`, `sync.py`,
  `cleanup.py`, `readiness.py`, `workspace.py`, `workflow/`). `FixHandler` becomes
  `PullRequestHandler(definition, shared, local)`; `FixCleanup` and `FixWorkspace` drop the
  prefix. Tests that patch module paths (`tests/integration/test_fix_readiness.py`,
  `tests/integration/test_fix_gestures.py`, and others) move with it.
- **Definition** (`work_kinds/pull_request/kinds.py`): a frozen `PullRequestKind` dataclass
  with `kind`, `unit_key`, `noun`, `item_noun`, `issue_type(shared)`, `workflow_name`,
  `workflow_file`, `staged_files`, `contract(shared)`, `outcome_file`, `branch_prefix`,
  `sync_marker`, `allowed_modes`, `roles`, `doctor_groups`, `reconcile` (a
  `ReconcilePolicy` value), `local(local_config)` (limits, schedule, execution, disk floor),
  and `defaults(shared)` (role profiles), exactly as in design B2. Define the `FIX` instance
  (`"fix"`, `"Fix"`, `"bug"`, `routing.bug_type`, `factory-fix`, `factory-fix-v1.0.yaml`,
  `fix.contract`, `fix-outcome.json`, `factory/fix`, `fix-sync` (unchanged value),
  `("docker", "host")`, `("lead", "implementor", "tester")`,
  `{"docker": "fix-sandbox", "host": "fix-host"}`, `SETTLE_ON_OPEN_PR`). Define the
  `ReconcilePolicy` enum with both values, `SETTLE_ON_OPEN_PR` (any open factory pull
  request settles the claim, today's behavior) and `RESUME_FROM_OWN_BRANCH` (the claim's own
  branch or own draft pull request is the resume point; a non-draft open factory pull
  request for the issue settles the claim as handed off; an ambiguous lookup holds), and
  implement both in the handler's pre-launch reconciliation so the policy is data. Only
  `SETTLE_ON_OPEN_PR` is exercised by a registered kind here; unit-test the other directly.
- **Staging** copies the union of every registered pull-request kind's `staged_files` plus
  the review and implementation workflows, so a clone holds every packaged workflow; the
  shadow check in `launch.py` covers every staged workflow name.
- **Handler capabilities**: runtime stops checking types. `WorkKindHandler`
  (`work_kinds/base.py`) gains optional capabilities, implemented as no-ops by the eval
  handler where they do not apply: `attach_github(client, token_provider)`,
  `resolve_request(request)`, `execution_mode(local)`, `needs_sandbox_memory(local)`,
  `ready_handoff(card, shared)` (the generalized `_assign_ready_bug`), `unblock(...)`
  (today's `process_blocked_claim`), `review_round(...)` (today's `process_review_claim`,
  with `prior_pull_request` public), `merge_sync(...)` and `pending_sync(claim)`,
  `retention_targets(run)`, and `blocked_reason(claim)`. Runtime calls them for every
  registered handler. Status lines, provider maps, `DiagnosticGroup`, `_GROUP_ORDER`,
  `host_executables`, and the LaunchAgent PATH gate iterate the registered kinds.
- **Ready handoff** (`ready_handoff`): for each pull-request kind, the card's repository is
  a fix target, it is an open issue, its native type equals the kind's issue type, its status
  is Ready, its owner is not factory, and its author has write, maintain, or admin access
  (cached per repository and author per tick).
- **Retention**: `retention_targets` returns the pull-request removal list (`logs`,
  `factory-suite.log`, `agent-runner`, `agent-runner-session`, `.runtime`) for any
  pull-request kind, keeping the kind's outcome file and `input/`.
- **review.json** gains the claim's work kind (`"kind": "fix"` for fix claims).
- **Shared scripts** (D6): `record-outcome.sh` takes the contract and outcome path as inputs
  instead of hard-coding `factory-fix/1` and `fix-outcome.json`, and accepts optional extra
  fields (`stopped_step`, `review_attention_counts`, `resume`) that `outcome.py` validates
  per contract; add a `check-contract.sh` that takes the expected and declared contract and
  replace the inline check in `factory-fix-v1.0.yaml` with it (staged with the fix files).
  `factory-fix-v1.0.yaml` passes its values; its behavior is unchanged. `record-triage.sh`
  stays fix-only; remove `read-regression-marker.sh` and the test that only exercises it.
- Do not add a `feature` kind, feature configuration, or feature workflow files here.

Constraints:

- Regression gates that must pass unchanged in intent (fixture edits for moved module paths
  and renamed classes are expected): `tests/e2e/test_fix_cycle.py`,
  `tests/e2e/test_factory_cycle.py`, `tests/e2e/test_independent_execution.py`,
  `tests/integration/test_supervision_hardening.py`,
  `tests/integration/test_supervision_recovery.py`, `tests/integration/test_fly_*.py`,
  `tests/integration/test_fix_*.py`, `tests/integration/test_host_launch.py`,
  `tests/integration/test_post_run_audit.py`, `tests/integration/test_review_contract.py`,
  `tests/integration/test_merge_sync.py`, `tests/integration/test_retention.py`, and
  `tests/integration/test_ready_bug_assignment.py`.
- Unit-test the `FIX` definition, both reconcile policies, and per-contract outcome
  validation test-first.

## Spec

_From `specs/factory-claim-lifecycle/spec.md` (MODIFIED Requirements)_

### Requirement: Keep lifecycle behavior independent of work kind

Core scheduling, claim/run persistence, retries, cancellation, restart recovery, and durable reporting SHALL remain independent of Codagent identities and eval-specific parsing or scoring. Work kinds SHALL supply request interpretation, execution planning, result interpretation, report presentation, admission-time input resolution, kind-specific readiness checks, and the kind's fresh-attempt and unblock gestures through one handler interface that the controller and runtime use for every kind. The eval handler SHALL own repetition semantics and suite integration; the pull-request handler, configured per kind for fix and feature work, SHALL own eligibility for its issue type, branch resolution, workflow invocation, and outcome mapping. The generic core SHALL NOT interpret a score out of 70, Runner/Skills fields, an `and-scene` command path, a pull-request outcome, or the blocked state's label. Extracting this interface from the existing eval-shaped controller SHALL preserve all existing eval behavior.

#### Scenario: Integrate another work kind later

- **WHEN** a later change supplies another work-kind handler
- **THEN** it can reuse the saved-claim, execution-attempt, scheduling, cancellation, and recovery behavior without adding that kind's request or scoring rules throughout the controller

#### Scenario: Extract the interface without changing eval behavior

- **WHEN** the eval handler is moved behind the handler interface
- **THEN** existing eval intake, execution, reporting, and recovery behavior is unchanged and its existing tests pass unmodified

#### Scenario: Read a kind-specific gesture

- **WHEN** a human drags a settled fix card from Review to Ready
- **THEN** the controller asks the fix handler, which treats it as a fresh-attempt request
- **AND** an eval card dragged the same way keeps its existing correction behavior

_From `specs/factory-pull-request-lifecycle/spec.md` (ADDED Requirements)_

### Requirement: Detect eligible review comments on a settled pull-request claim

For each pull-request claim, fix or feature, that is settled, or blocked by a review round's `needs-input`, whose recorded factory pull request is open, the factory SHALL read on each successful poll the pull request's reviews, inline review threads (with their resolution state), and conversation comments. A comment is eligible when its author has write, maintain, or admin access to the repository, it is not authored by the factory's own identity, it has a non-empty body, and it is newer than the claim's review checkpoint. A review with state `APPROVED` and an empty body SHALL NOT be eligible. Comments in resolved threads SHALL be read but SHALL NOT be eligible. The review checkpoint SHALL be initialised, when absent, to the recorded completion time of the claim's latest attempt, SHALL be advanced to the reservation time of each review attempt so the same comment never launches two rounds, and SHALL be advanced to the decline time when a review attempt returns `needs-input`. A claim whose pull request is merged or closed SHALL NOT be scanned.

#### Scenario: Reviewer requests a change

- **WHEN** a writer submits a review with state `CHANGES_REQUESTED` and an inline comment on a factory PR whose fix or feature claim is settled in Review
- **THEN** the next poll finds the comments eligible and re-admits the claim for a review round

#### Scenario: Reviewer asks a question in the PR conversation

- **WHEN** a writer posts a conversation comment on the factory PR after the claim settled
- **THEN** the comment is eligible and a review round is admitted

#### Scenario: Reviewer approves without comment

- **WHEN** a writer submits an `APPROVED` review with no body and no inline comments
- **THEN** nothing is eligible and no attempt starts

#### Scenario: Non-writer or factory comments

- **WHEN** a user without write access, or the factory's own identity, comments on the PR
- **THEN** the comment is ignored and no attempt starts

#### Scenario: Comment arrives during a review round

- **WHEN** a writer comments while a review attempt is running
- **THEN** the running attempt is unaffected and the comment is picked up by the poll after that attempt completes, because it is newer than the advanced checkpoint

#### Scenario: Lookup fails

- **WHEN** reading the PR reviews, threads, or conversation comments fails
- **THEN** the claim is left as it is and the scan retries on a later poll without advancing the checkpoint

#### Scenario: One commenter's permission lookup fails

- **WHEN** the permission lookup for one comment author fails while other lookups succeed
- **THEN** that author's comments are ineligible for this poll and the scan continues with the remaining comments

_From `specs/factory-pull-request-lifecycle/spec.md` (ADDED Requirements)_

### Requirement: Re-admit a review round through the claim's kind slot

An eligible review round SHALL be admitted through the execution slot, window, and limits of the claim's own work kind: a fix claim's round through the fix slot and fix window, and a feature claim's round through the feature slot and feature window. It SHALL be admitted only when the factory is not paused, that slot is free, that window is open, memory headroom is available, and no applicable quota or readiness hold is active. Review rounds SHALL be considered before new Ready work of the same kind in the same cycle, and SHALL NOT compete with work of another kind. Admission SHALL reconcile side effects, verify the recorded PR is still open and read its head commit, cut fresh clones with the target checked out on the PR branch at that head, reserve a run with reason `review` and the claim's recorded Runner and Skills commits, set the claim lifecycle to active, and comment on the issue that a review round started, naming the comments it will address. When the recorded PR is no longer open at admission, the factory SHALL record why and SHALL NOT launch.

#### Scenario: Review round beats a new bug

- **WHEN** the fix slot is free, a settled fix claim has an eligible review comment, and another bug sits in Ready
- **THEN** the review round is admitted first and the new bug waits for the next free slot

#### Scenario: Feature review round uses the feature slot

- **WHEN** a settled feature claim has an eligible review comment, the feature slot is free, and a fix attempt holds the fix slot
- **THEN** the feature review round is admitted through the feature slot under the feature limits
- **AND** it is admitted before any new Ready feature in the same cycle

#### Scenario: PR closed before admission

- **WHEN** the reviewer closed the PR before the poll admits the round
- **THEN** no attempt starts, the checkpoint is not advanced, and the reason is recorded on the claim

#### Scenario: Slot busy

- **WHEN** another attempt of the same kind holds the claim's kind slot
- **THEN** the review round waits and is admitted on a later poll while its comments remain eligible

_From `specs/factory-pull-request-lifecycle/spec.md` (ADDED Requirements)_

### Requirement: Sync the working clone and close the issue after merge

For a settled pull-request claim, fix or feature, with a recorded factory PR that has been merged and whose sync has not completed, regardless of the card's current column, the factory SHALL update the operator's configured working clone of that repository on the next successful poll: verify the working tree and index have no changes to tracked files, fetch `main` from the remote into the local `main` branch (refusing when local `main` has diverged or is checked out in any worktree of that clone), verify by dry run that merging `main` into the currently checked-out branch produces no conflicts, and then perform that merge. On success it SHALL close the issue when it is still open so closure automation moves the card to Done. On tracked changes, a diverged local `main`, `main` checked out elsewhere, a predicted conflict, a detached HEAD, or an unreachable clone, it SHALL leave the card where it is, apply the `needs-input` label when the card is not yet Done, comment with the reason, and retry on later polls, clearing the label when the sync succeeds. Apart from fetched refs and objects, it SHALL NOT change the clone's working tree, index, or checked-out branch except by the merge itself. A merged PR whose issue a human already closed SHALL still receive its one sync attempt sequence.

#### Scenario: Merge a fix or feature while the clone is on dev

- **WHEN** a factory PR for a fix or feature claim merges while the working clone has branch `dev` checked out and a clean tree
- **THEN** the factory updates local `main`, merges it into `dev`, closes the issue, and the card moves to Done

#### Scenario: Merge a fix while the clone is dirty

- **WHEN** the working clone has uncommitted changes
- **THEN** the card stays in Review with the `needs-input` label and a comment naming the uncommitted changes
- **AND** the clone's working tree, index, and branches are unchanged

#### Scenario: Predict a conflict

- **WHEN** the dry-run merge reports conflicts
- **THEN** the factory performs no merge, leaves the working tree unchanged, and blocks the card with the conflict reason

#### Scenario: Find local main diverged or checked out elsewhere

- **WHEN** local `main` has commits not on the remote, or `main` is checked out in another worktree of the clone
- **THEN** the factory refuses to update `main`, blocks the card with that reason, and changes no branch

#### Scenario: Merge after a human closed the issue

- **WHEN** a human closed the issue before the factory observed the merged PR and the card is already Done
- **THEN** the factory still performs the sync once and comments on the outcome without relabeling the Done card

#### Scenario: Resolve and retry

- **WHEN** the operator commits or stashes the changes and the next poll's checks pass
- **THEN** the factory completes the merge, removes the `needs-input` label, closes the issue, and the card moves to Done

_From `specs/factory-bug-intake/spec.md` (MODIFIED Requirements)_

### Requirement: Select eligible bugs by Priority

On each Project poll, the factory SHALL treat placement of an open issue from a configured fix target with native `Type=Bug` in `Status=Ready` as an explicit handoff and set `Owner=factory` before admission when the issue author has effective write, maintain, or admin access, regardless of the prior or missing Owner value. A GitHub issue assignee SHALL NOT be required. The factory SHALL then select open issues with native `Type=Bug`, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified again at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL rank eligible bugs by the Project Priority field, highest first with unset values last, then by newest creation time; repository SHALL NOT affect the order. An ineligible bug SHALL NOT prevent selection of a later eligible bug. Eval, bug, and feature selection SHALL be independent: each kind fills only its own execution slot. Bugs SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration.

#### Scenario: Ready placement assigns factory ownership

- **WHEN** a human moves an open configured Bug to Ready with Owner unset or set to human
- **THEN** the next factory poll verifies the author's repository permission and sets `Owner=factory`
- **AND** admission re-verifies permission before work starts

#### Scenario: Pick the highest-priority bug

- **WHEN** the fix slot is free and two eligible bugs with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority bug regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible bugs share a Priority value
- **THEN** the factory admits the more recently created bug first

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Bug card has `Owner=factory` but its author lacks the required repository access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the bug is not eligible

#### Scenario: Skip a blocked or held bug

- **WHEN** the top-ranked bug carries the `needs-input` label or a fix-specific hold applies
- **THEN** the factory selects the next eligible bug instead

#### Scenario: Reprioritize while a fix is running

- **WHEN** a user changes a bug's Priority during active fix execution
- **THEN** the active fix continues and the new ranking governs subsequent selection

_From `specs/factory-bug-intake/spec.md` (MODIFIED Requirements)_

### Requirement: Recognize fix retry gestures

A fix claim that ended with a pull request, a failed outcome, or exhausted recovery is settled. Moving a settled fix card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and PR history; the new attempt's input SHALL include the issue's eligible comments and any prior factory PR. A settled fix claim whose factory pull request is open SHALL additionally be re-admitted for a review round when an eligible PR-side comment newer than its review checkpoint appears, as defined by `factory-pull-request-lifecycle`; issue comments on a settled claim SHALL NOT trigger a round. A blocked fix claim SHALL be re-admitted when an eligible comment newer than the decline appears on the issue, or when a human moves its card from Running to Ready; a claim blocked by a review round's `needs-input` SHALL instead be re-admitted by an eligible PR-side comment newer than the decline. In each of these cases the factory SHALL remove the `needs-input` label and start a new attempt whose input includes the eligible comments present at that time. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission. The eval fresh-request gesture is unchanged and separate.

#### Scenario: Drag a settled fix back to Ready

- **WHEN** a human moves a fix card whose claim is settled from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits, keeps the earlier claim history, and does not treat the drag as a contradictory status edit

#### Scenario: Comment on the PR of a settled fix

- **WHEN** a writer comments on the open factory PR of a settled fix claim
- **THEN** the next poll admits a review round on the same claim, keeping its history and PR

#### Scenario: Comment on the issue of a settled fix

- **WHEN** a writer comments on the issue, not the PR, of a settled fix claim in Review
- **THEN** no attempt starts

#### Scenario: Answer a blocked bug

- **WHEN** a writer comments on a blocked bug after the decline comment
- **THEN** the next poll removes the `needs-input` label and admits a new attempt when the fix slot and holds permit
- **AND** the new attempt's input includes the writer's comment

#### Scenario: Answer a review round's needs-input

- **WHEN** a review round declined with `needs-input` and a writer then replies on the PR
- **THEN** the next poll removes the `needs-input` label and admits a new review round whose input includes that reply

#### Scenario: Drag a blocked bug to Ready

- **WHEN** a human moves a blocked bug card from Running to Ready without commenting
- **THEN** the next poll removes the `needs-input` label and admits a new attempt with the eligible comments already on the issue

#### Scenario: Post a factory comment on a blocked bug

- **WHEN** the factory delivers one of its own comments to a blocked bug
- **THEN** the bug remains blocked and no attempt starts

#### Scenario: Receive a comment from a non-writer

- **WHEN** a user without write access comments on a blocked bug
- **THEN** the bug remains blocked and the comment is not supplied to any attempt

#### Scenario: Edit a running bug

- **WHEN** the issue body or comments change while a fix attempt is executing
- **THEN** the running attempt is unaffected and no overlapping attempt starts

_From `specs/factory-eval-intake/spec.md` (MODIFIED Requirements)_

### Requirement: Select eligible work by Priority

The factory SHALL select open issues from configured source repositories whose authors have write, maintain, or admin access, with native `Type=Eval`, `Owner=factory`, and `Status=Ready`, valid execution settings, and no applicable admission hold. Selection SHALL rank eligible requests by the Project Priority field, highest first with unset values last, then by newest creation time. Invalid or otherwise ineligible requests SHALL NOT prevent selection of a later eligible request. Reordering or reprioritizing SHALL NOT interrupt active work.

#### Scenario: Prioritize queued evaluations

- **WHEN** a user gives one eligible eval a higher Priority than another before the next selection
- **THEN** the higher-priority eval is selected first, regardless of issue age or board position

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible evals share a Priority value
- **THEN** the more recently created eval is selected first

#### Scenario: Skip an ineligible request

- **WHEN** the highest queued request is invalid or cannot yet resume and a lower request is eligible under current admission controls
- **THEN** the factory selects the lower eligible request

#### Scenario: Reprioritize while an evaluation is running

- **WHEN** a user changes Priority values during active execution
- **THEN** the active evaluation continues and the new ranking governs subsequent selection

_From `specs/factory-fix-execution/spec.md` (MODIFIED Requirements)_

### Requirement: Invoke the versioned fix workflow

The factory SHALL run the packaged fix workflow in the execution mode configured for the fix kind, `docker` or `host`, passing the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, and the attempt's artifact directory. In `docker` mode the workflow runs in the existing sandbox through the Runner sandbox script with a per-run image tag; in `host` mode it runs as described by the host execution requirement. The workflow SHALL write its outcome and any intermediate records under the supplied artifact directory and SHALL NOT assume a fixed container path. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the Runner in use does not support what the workflow requires, and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure. A running attempt SHALL keep the mode it launched with; a recovery retry SHALL launch in the currently configured mode. The factory SHALL ship the review workflow, the shared implementation sub-workflow, and the feature and define workflows beside the fix workflow and stage all of them into the Runner catalog the attempt uses, in either mode, where the Runner resolves them by name and relative path. Each workflow contract SHALL be versioned and checked the same way. A fix attempt SHALL run `factory-fix` on a fresh branch from the recorded target commit; a review attempt SHALL run `factory-review` on the PR branch at its recorded head with the attempt's `review.json` as input, and SHALL write its outcome to `review-outcome.json` under the supplied artifact directory with the same outcome values. Extracting the shared sub-workflow SHALL NOT change the `factory-fix/1` contract or the fix workflow's observable behaviour.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged fix workflow declares a compatible contract version and the Runner in use supports what it requires
- **THEN** the attempt starts under its own supervisor in the configured execution mode with the configured roles

#### Scenario: Launch in Docker mode

- **WHEN** the fix kind is configured for `docker` and the packaged workflow declares the expected contract
- **THEN** the attempt starts under its own supervisor in the sandbox with the configured roles and a run-specific image tag
- **AND** the workflow receives the sandbox's artifact mount as its artifact directory

#### Scenario: Launch in host mode

- **WHEN** the fix kind is configured for `host` and host readiness passes
- **THEN** the attempt starts under its own supervisor on the host with the configured roles and no image tag
- **AND** the workflow receives the attempt's artifact directory under the storage root and writes `fix-outcome.json` there

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged workflow declares an unsupported contract version or the Runner in use lacks a feature the workflow requires
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Launch a review attempt

- **WHEN** a review round is admitted
- **THEN** the target clone is on the PR branch at the recorded head, the staged directory holds every packaged workflow file, and the Runner runs `factory-review` with `review.json`

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy

#### Scenario: Change the mode while an attempt runs

- **WHEN** the operator changes the fix execution mode while a fix attempt is running
- **THEN** the running attempt continues in the mode it launched with
- **AND** a later recovery retry or new attempt launches in the newly configured mode

_From `specs/factory-review-execution/spec.md` (MODIFIED Requirements)_

### Requirement: Run the versioned review workflow

The factory SHALL ship a packaged review workflow declaring contract `factory-review/1`, stage it beside the fix workflow and the shared implementation sub-workflow, and launch it through the execution path of the claim's kind, a fix claim's round as a fix attempt and a feature claim's round on the host as a feature attempt, on clones where the target is checked out on the PR branch at its recorded head. The factory SHALL write `review.json` into the attempt's artifact directory containing the repository, issue number, claim identifier, the claim's work kind, attempt number, the pull request number, URL, branch, base branch and head commit, the original issue title and body, and the eligible comments grouped by source (review summaries, unresolved inline threads with path, line, thread identifier and every comment in the thread, and conversation comments), each with author, identifier, body, and creation time. The workflow SHALL return exactly one structured outcome in `review-outcome.json` declaring its contract: `pull-request` with the PR reference and the identifiers it answered and changed, `needs-input` with reasons, `failed` with reasons, or a technical failure when the file is absent or invalid.

#### Scenario: Launch a review round

- **WHEN** a review round is admitted with a compatible Runner commit
- **THEN** the attempt starts under its own supervisor with the review contract and `review.json` describes the PR and the eligible comments

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `review-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy of the claim's kind


Portion for this task: every requirement above is satisfied for fix and eval claims through
the generalized code paths. Scenarios naming a feature claim, the feature slot, or the
feature and define workflow files are not exercised here, because no feature kind is
registered; the code paths they will use must be kind-generic (no `fix` literals outside
the `FIX` definition). `review.json` carries the claim's work kind.

## Test Plan

- `INT-008` (fix portion) — the parameterized `record-outcome.sh` and `check-contract.sh`
  as invoked by `factory-fix-v1.0.yaml`. Setup: the fix workflow's existing script inputs.
  Action: run both scripts for the fix contract. Assertions: fix outcomes are byte-for-byte
  equivalent to the previous scripts' output for the same inputs (capture the previous
  output from the current scripts before changing them); a contract mismatch fails the
  check. File: extend `tests/integration/test_fix_workflow.py`.

## Done When

- `work_kinds/fix/` no longer exists; `work_kinds/pull_request/` holds the handler and its
  collaborators, `kinds.py` defines `PullRequestKind`, `ReconcilePolicy`, and `FIX`, and
  `work_kinds.handlers()` builds the fix handler as `PullRequestHandler(FIX, ...)`.
- `runtime.py`, `operations.py`, `retention.py`, and the pull-request modules contain no
  `isinstance(..., FixHandler)` checks and no `"fix"` or `"Bug"` literals outside `kinds.py`
  and configuration defaults.
- `review.json` includes the claim's work kind; `read-regression-marker.sh` is removed.
- INT-008's fix portion passes, and the listed regression suites pass.
- `uv run pytest`, `uv run ruff format --check . && uv run ruff check .`, and `uv run pyright`
  pass.
