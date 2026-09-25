# Task: Stop, resume, continue, and recover feature claims

## Goal

Make a feature claim survive everything after its first launch without redoing finished
work or losing human input: map a definition stop to a blocked claim parked in Running,
recognize the gestures that resume it, continue a failed claim's plan on a new claim,
compute each attempt's resume point from the checkpoints on the pushed branch, reconcile a
feature's branch and draft pull request before every launch, refuse a fresh claim while the
settled claim's pull request is open, and admit review rounds on feature pull requests
through the feature slot. The admission and outcome comments state how each attempt
started and why it stopped.

## Background

Read `openspec/changes/factory-feature-support/design.md`, sections B6 (reconciliation
policies), D2 (stops), D4 (resume and checkpoints), D7 (review rounds), and the risk
"Session reports across attempts".

Where things are (paths relative to `src/agent_factory/`):

- The feature kind is registered as `PullRequestHandler(FEATURE, ...)` from
  `work_kinds/pull_request/kinds.py`, with `reconcile=RESUME_FROM_OWN_BRANCH` and
  `outcome_file="feature-outcome.json"`. The handler's `unblock(...)` (in
  `work_kinds/pull_request/blocked.py`), `review_round(...)` (`review.py`), gesture
  recognition (`handler.py`), and pre-launch reconciliation already serve the fix kind and
  are kind-generic. Feature attempts launch on the host with the params `issue_file`,
  `branch_name`, `change_name`, `contract_version`, `artifact_dir`, `resume_from`, and
  `prior_branch`; until this task they always pass empty `resume_from` and `prior_branch`.
- The packaged `factory-feature` workflow (in `work_kinds/pull_request/workflow/`) skips
  every step before `resume_from` in the order `proposal, proposal-review, specs, design,
  test-plan, approach-review, write-tasks, implement, archive, verify, finalize`; runs
  `reconcile-artifacts` only when `resume_from` names a define step or `prior_branch` is
  non-empty; checks out the claim's own branch on resume, starts from `prior_branch` and
  merges the recorded target commit on continuation, and falls back to fresh (recorded in
  `{{artifact_dir}}/resume.json`) when the resume point is missing or the merge conflicts.
  Its checkpoint commits carry a `Factory-Checkpoint: planned | implemented | archived`
  trailer and are pushed immediately. `feature-outcome.json` for a stop is `needs-input`
  with `stopped_step` (a define step id, or `preflight` when the target has no
  `openspec/`), `questions`, `direction_summary`, and `branch` (absent for `preflight`);
  every outcome may carry `resume` (the fallback record).
- Host attempts write the Runner session to `<evidence>/agent-runner-session`; the
  implementation's session reports are `agent-runner-session/output/*session-report*.out`.
- The fix kind's blocked-claim handling (Running with `needs-input`, unblock by eligible
  comment or Running → Ready drag, review-round `needs-input` parked in Review) and status
  corrections live in `blocked.py` and `runtime.py`; the per-claim durable reporting with
  stable markers is in `controller.py`.

Decisions to implement:

- **Resume point at admission (D4).** Fetch the claim's branch into the mirror
  (`git fetch <mirror> refs/heads/<branch>`), read the newest `Factory-Checkpoint` trailer
  (`git log --format=%(trailers:key=Factory-Checkpoint,valueonly)`), and compute:
  - resumed after a definition stop → `resume_from` = the outcome's `stopped_step`, on the
    same claim and branch; a `preflight` stop → fresh (`resume_from=""`);
  - technical recovery → the step after the newest checkpoint on the pushed branch
    (`planned` → `implement`, `implemented` → `archive`, `archived` → `verify`; an open draft
    pull request for the claim → `verify`); no branch or no checkpoint → fresh;
    `prior_branch` is always empty for a technical recovery;
  - a new claim continuing a failed prior claim (Review → Ready on a settled `failed` or
    exhausted claim) → `resume_from=implement` with `prior_branch` set to the most recent
    prior feature claim's branch when that branch carries at least `planned`; otherwise
    fresh.
  When `resume_from` is `archive` or later, copy the prior attempt's
  `agent-runner-session/output/*session-report*.out` into the new attempt's session
  directory before launch. The design relies on the Runner accepting a pre-populated
  `--session-dir`, which `tests/integration/test_feature_workflow_catalog.py` checks; if
  that test shows the Runner refuses it, resume at `implement` instead of `archive` or
  later and assert that in INT-006.
- **Reconciliation (B6, `RESUME_FROM_OWN_BRANCH`).** Before any feature launch (recovery,
  resume, or new claim): the claim's own pushed branch or own draft pull request is the
  resume point, not a duplicate; a non-draft open factory pull request for the issue settles
  the claim as handed off (its result is that pull request); an ambiguous or failed lookup
  holds the claim, reports it on the issue and in status, and retries on a later poll.
- **Gestures.** Blocked (definition stop): an eligible issue comment newer than the stop, or
  a Running → Ready drag, removes `needs-input` and admits a resumed attempt on the same
  claim with the eligible comments in its input. Settled: Review → Ready requests a new
  claim with re-resolved commits, retaining history, supplying the prior branch as above,
  except when the settled claim's factory pull request is open: then no claim is created,
  the status correction restores Review, and a one-time `open-pr-retry` event explains that
  commenting on the pull request continues it. Issue comments on a settled claim start
  nothing. Non-writer and factory comments start nothing and are not input.
- **Board and status.** A definition-stop `needs-input` leaves the card in Running with the
  label and releases the feature slot; the corrections treat Review or Done moves of such a
  card as contradictions (restore Running) and Ready as the unblock gesture. A review
  round's `needs-input` returns the card to Review with the label and the verdict held at
  admission. Status lists a blocked feature with its stop reason and pushed branch and the
  feature slot as free.
- **Comments.** The admission comment states fresh, resumes at `<step>`, or continues the
  prior claim's branch, and states when a resume point was unavailable and the attempt
  started fresh (from the outcome's `resume`); the stop comment lists the questions,
  summarizes the drafted direction, and links the pushed branch, or for `preflight` states
  that no branch was created and the next attempt starts fresh. Review round admission and
  completion comments for features follow the fix comments, and the completion comment
  states that acceptance was not re-run.
- **Review rounds.** A feature claim's round runs through the feature slot, window, and
  limits, ahead of new Ready features in the same cycle, and writes `review.json` with
  `"kind": "feature"`.
- **Session-report fallback.** Before relying on the session-report copy, run
  `tests/integration/test_feature_workflow_catalog.py` against the installed Runner. If it
  contains `test_runner_refuses_prepopulated_session_dir` (the Runner refuses a
  pre-populated `--session-dir`), implement the fallback: a technical recovery whose newest
  checkpoint would resume at `archive` or later launches with `resume_from=implement`
  instead, and INT-006 asserts that for the `archived` branch.
- **Enable features in the Codagent configuration.** As the last commit of this task, add
  the `[feature]` section to `config/codagent.toml`: `contract = "factory-feature/1"` and
  `[feature.defaults]` roles matching `[fix.defaults]` for `lead`, `implementor`, and
  `tester`, plus a `crosscheck` role from a different model family than `lead` (for
  example a `codex:` profile when `lead` is `claude:`). This is the enabling step of the
  design's migration plan (step 5); keeping it as its own commit lets it ship as a separate
  enabling pull request. Doctor's `feature-host` group holds feature work while the
  installed Runner lacks `core/verify-change`. Confirm that tests loading
  `config/codagent.toml` still pass with the section present.
- Document the feature retry gestures (answer, drag to Ready, continue a failed feature,
  comment on the open pull request) in `docs/operations.md`.

## Spec

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. Feature configuration SHALL include the feature role profiles, feature limits, the feature admission window, and the feature workflow contract; the feature kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new feature work SHALL be enabled only when the feature configuration is present; when it is removed, existing feature claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The feature kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. The eval kind SHALL accept `docker` (the default) or `fly` execution and SHALL NOT accept host execution. Fly configuration SHALL be local and SHALL include the Fly app, region (default `ewr`), Machine CPU kind, CPU count, and memory (default shared, 4 CPUs, 8 GiB), the sandbox image reference, the deploy-token file location alongside the other controller credentials, and the collection grace period. The execution mode, fix disk floor, Fly settings, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration whose eval role defaults are `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`, and `tester = codex:gpt-5.6-luna:medium`, and which enables the feature kind with role defaults matching its fix role defaults. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific and workflow-specific repository and executable locations SHALL be supplied to the relevant handler rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the Eval, Bug, and Feature behavior and the field names, options, and defaults described by the active specifications

#### Scenario: Configure fix targets

- **WHEN** an operator configures five target repositories with mirror and working-clone paths and leaves branches unset
- **THEN** fixes resolve `main` for each repository and the merge sync targets each configured working clone

#### Scenario: Leave the execution mode unset

- **WHEN** the local configuration names no fix execution mode
- **THEN** fixes run in the sandbox exactly as before this change

#### Scenario: Leave the eval execution mode unset

- **WHEN** the local configuration names no eval execution mode
- **THEN** evals run under Docker exactly as before this change and no Fly setting is required

#### Scenario: Configure host execution for evals

- **WHEN** the local configuration requests host execution for the eval kind
- **THEN** the factory reports the unsupported setting at startup and in doctor and admits no eval

#### Scenario: Configure Fly execution for evals

- **WHEN** the local configuration requests `fly` execution for the eval kind with an app, image, and deploy-token location and leaves the region, size, and grace unset
- **THEN** evals run in Fly Machines in `ewr` at shared 4 CPUs and 8 GiB with the default collection grace, and startup reports any missing required Fly setting

#### Scenario: Configure Docker execution for features

- **WHEN** the configuration selects Docker or Fly execution for the feature kind
- **THEN** configuration loading fails and names the unsupported mode

#### Scenario: Leave the feature kind unconfigured

- **WHEN** the configuration has no feature section
- **THEN** no Feature-typed issue is handed off or admitted and the other kinds behave as before

#### Scenario: Remove the feature section with feature claims in flight

- **WHEN** the feature section is removed while one feature attempt is running and another feature claim is settled with an open pull request
- **THEN** the running attempt is still supervised and its result reported, the settled claim still receives review rounds, merge sync, and cleanup, and no new feature is handed off or admitted

_From `specs/factory-feature-intake/spec.md` (ADDED Requirements)_

### Requirement: Recognize feature retry gestures

A feature claim that ended with `needs-input` is blocked. A blocked feature claim SHALL be re-admitted when an eligible comment newer than the stop appears on the issue, or when a human moves its card from Running to Ready; a claim blocked by a review round's `needs-input` SHALL instead be re-admitted by an eligible pull-request comment newer than the stop. The factory SHALL remove the `needs-input` label and start a new attempt on the same claim that resumes at the step that stopped, as defined by `factory-feature-execution`.

A feature claim that ended with a pull request, a `failed` outcome, or exhausted recovery is settled. Moving a settled feature card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and pull request history. When the most recent prior feature claim for the issue pushed a branch carrying a committed plan, the factory SHALL supply that branch to the new claim, which continues from its plan as defined by `factory-feature-execution`. A settled feature claim whose factory pull request is open SHALL be continued only through review rounds, as defined by `factory-pull-request-lifecycle`; issue comments on a settled claim SHALL NOT trigger an attempt. Moving such a card from Review back to Ready SHALL NOT create a claim or start an attempt: the factory SHALL restore the card to Review and comment once that the open pull request is continued by commenting on it. A blocked claim whose stop was `preflight` SHALL be re-admitted as a fresh definition rather than resumed.

Every resumed or continued attempt's input SHALL include the issue's eligible comments present at admission. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission.

#### Scenario: Answer a blocked feature

- **WHEN** a writer comments on a blocked feature after the stop comment
- **THEN** the next poll removes the `needs-input` label and admits a resumed attempt on the same claim when the feature slot and holds permit
- **AND** the attempt's input includes the writer's comment

#### Scenario: Drag a blocked feature to Ready

- **WHEN** a human moves a blocked feature card from Running to Ready without commenting
- **THEN** the next poll removes the `needs-input` label and admits a resumed attempt with the eligible comments already on the issue

#### Scenario: Drag a failed feature back to Ready

- **WHEN** a human moves a feature card whose claim ended `failed` after its plan commit from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits, keeps the earlier claim history, and supplies the prior claim's branch to the new claim

#### Scenario: Drag a failed feature with an open pull request to Ready

- **WHEN** a feature claim ended `failed` after CI stayed red, leaving its pull request open, and a human moves its card from Review to Ready
- **THEN** no claim is created and no attempt starts
- **AND** the card returns to Review and the issue receives one comment explaining that commenting on the pull request continues it

#### Scenario: Comment on the issue of a settled feature

- **WHEN** a writer comments on the issue, not the pull request, of a settled feature claim in Review
- **THEN** no attempt starts

#### Scenario: Receive a comment from a non-writer

- **WHEN** a user without write access comments on a blocked feature
- **THEN** the feature remains blocked and the comment is not supplied to any attempt

#### Scenario: Post a factory comment on a blocked feature

- **WHEN** the factory delivers one of its own comments to a blocked feature
- **THEN** the feature remains blocked and no attempt starts

_From `specs/factory-feature-intake/spec.md` (ADDED Requirements)_

### Requirement: Reconcile feature side effects before launching

Before launching any attempt for a feature, including a recovery retry, a resumed attempt, or a new claim, the factory SHALL look up the claim's feature branch and any open factory pull request referencing the issue. The claim's own pushed branch, and its own draft pull request left by an attempt that ended technically before finalization, SHALL be treated as the attempt's resume point rather than as a duplicate. An open factory pull request for the issue that is not a draft SHALL settle the claim as handed off rather than launch a duplicate. When the factory cannot establish whether a prior attempt pushed a branch or opened a pull request, it SHALL hold the claim, report the ambiguity on the issue and in status, and SHALL NOT launch.

#### Scenario: Recover from the claim's draft pull request

- **WHEN** an attempt opened its draft pull request and then failed technically before finalization
- **THEN** reconciliation identifies the branch and draft pull request as the resume point and launches the recovery retry

#### Scenario: Find a ready pull request from a crashed attempt

- **WHEN** an attempt marked its pull request ready but failed before its outcome was recorded
- **THEN** reconciliation reports that pull request as the attempt's result without launching the retry

#### Scenario: Fail to reach GitHub before launch

- **WHEN** the reconciliation lookup fails
- **THEN** the factory does not launch and retries reconciliation on a later poll

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Resume and continue feature work

A resumed attempt on a blocked claim SHALL check out the claim's branch and continue at the definition step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target's recorded commit, and continue at implementation with the prior committed plan. An attempt resumed after a definition stop, or a new claim continuing a prior claim, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A technical recovery retry SHALL NOT revise existing artifacts. When the resume point is missing or cannot be used, including an unresolvable merge, the workflow SHALL start a fresh definition and state in its outcome that it did so. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Resume after answering a definition question

- **WHEN** a claim stopped during design and a writer answers the question on the issue
- **THEN** the resumed attempt checks out the claim's branch, revises the proposal or specifications where the answer conflicts with them, and continues at design

#### Scenario: Continue a failed feature on a new claim

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after its plan commit
- **THEN** the workflow merges the target's recorded commit into that branch and starts at implementation with the prior plan
- **AND** it revises the plan first when the current issue or eligible comments warrant it

#### Scenario: Continue when the prior branch is gone

- **WHEN** a new claim is supplied with a prior branch that no longer exists
- **THEN** the workflow starts a fresh definition and states in its outcome that the prior branch was unavailable

#### Scenario: Recover after verification began

- **WHEN** an attempt fails technically after pushing its `archived` checkpoint
- **THEN** the recovery retry continues at verification from the pushed branch and does not revise the plan or its artifacts

#### Scenario: Crash before a checkpoint is pushed

- **WHEN** an attempt fails technically after committing its implementation but before pushing the `implemented` checkpoint
- **THEN** the recovery retry finds `planned` as the last pushed phase and redoes implementation

#### Scenario: Recover after a crash during implementation

- **WHEN** an attempt fails technically after pushing its plan commit
- **THEN** the recovery retry starts from fresh clones, checks out the pushed branch, and continues at implementation

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Require an OpenSpec repository

Before defining a change, the feature workflow SHALL check that the target clone contains an `openspec/` directory. When it does not, the workflow SHALL return `needs-input` stating that the repository is not initialized for OpenSpec, with the stopped step recorded as `preflight`, and SHALL push no branch and open no pull request. An attempt re-admitted after a `preflight` stop SHALL start a fresh definition.

#### Scenario: Hand off a feature in a repository without OpenSpec

- **WHEN** a feature is admitted for a configured target that has no `openspec/` directory
- **THEN** the workflow returns `needs-input` naming the missing OpenSpec initialization with stopped step `preflight`
- **AND** it pushes no branch and opens no pull request

#### Scenario: Re-admit after OpenSpec is initialized

- **WHEN** a feature stopped at `preflight` and, after the repository was initialized for OpenSpec, a writer comments on the issue
- **THEN** the re-admitted attempt starts a fresh definition

_From `specs/factory-claim-lifecycle/spec.md` (MODIFIED Requirements)_

### Requirement: Correct status edits that contradict factory execution

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. A fix claim blocked by bug triage, or a feature claim blocked during definition, SHALL be an exception: its card remains in Running with the `needs-input` label without execution and SHALL NOT be moved to a queued status; such a blocked card moved to Review or Done SHALL be restored to Running, while a blocked card moved to Ready SHALL be treated as its handler's unblock gesture. A fix or feature claim blocked by a review round's `needs-input` SHALL instead remain in Review with the `needs-input` label; moved to Running or Done it SHALL be restored to Review, and moved to Ready it SHALL be treated as the fresh-claim gesture. Moving a request with verified active execution to Ready, Review, or Done SHALL restore Running and continue that same execution without restarting it or admitting overlapping work. Such status changes SHALL NOT cancel execution. Issue closure SHALL retain its defined cancellation behavior and take precedence over restoring Running. A drag that the request's work-kind handler recognizes as a fresh-attempt gesture SHALL be honored rather than corrected.

The factory SHALL leave status edits on cards without factory ownership alone. Corrective updates SHALL include a brief issue comment explaining the actual state and correction, using durable reporting to avoid repeating the same correction comment on each poll. The default correction interval SHALL be the normal five-minute poll; unavailable GitHub access SHALL delay the correction rather than change execution state based on an unverified board observation.

#### Scenario: Move an idle request to Running

- **WHEN** a human moves a factory-owned queued request to Running while it has no active execution
- **THEN** the next successful poll restores its appropriate queued status
- **AND** the drag itself creates no execution attempt and bypasses no admission control
- **AND** the factory briefly explains the correction on the issue

#### Scenario: Move a running request to Ready

- **WHEN** a human moves a factory-owned request with verified active execution to Ready
- **THEN** the next successful poll restores Running and continues the existing execution
- **AND** no fresh claim, duplicate attempt, restart, or recovery retry results from the status edit

#### Scenario: Move a running request to a handoff or completion column

- **WHEN** a human moves a factory-owned request with verified active execution to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running without cancelling execution or reporting it as complete

#### Scenario: Close an issue whose status also changed

- **WHEN** the factory observes that an active request's issue is closed and its card has moved out of Running
- **THEN** it follows the issue-closure cancellation policy rather than restoring Running

#### Scenario: Change a card that is not factory-owned

- **WHEN** a human changes the Status of a card without factory ownership
- **THEN** the factory does not correct that card or infer an execution request from the drag

#### Scenario: Retry delivery of a status correction

- **WHEN** a corrective update or its explanatory comment needs delivery again after a poll or controller restart
- **THEN** the factory reconciles current execution and board state before retrying delivery
- **AND** it does not duplicate a correction comment already delivered

#### Scenario: Leave a blocked fix in Running

- **WHEN** a fix claim is blocked awaiting input and its card sits in Running with the `needs-input` label
- **THEN** the poll does not move the card or post a correction

#### Scenario: Move a blocked fix to Review

- **WHEN** a human moves a blocked fix card from Running to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running with the label intact and explains the correction once

#### Scenario: Move a fix blocked by a review round

- **WHEN** a human moves a fix card blocked by a review round's `needs-input` from Review to Running or Done while its issue remains open
- **THEN** the factory restores Review, keeps the `needs-input` label, and comments once on the correction

#### Scenario: Leave a blocked feature in Running

- **WHEN** a feature claim stopped during definition and its card sits in Running with the `needs-input` label
- **THEN** the poll does not move the card or post a correction
- **AND** a human moving the card to Ready is treated as the unblock gesture

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

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Comment on feature activity

The factory SHALL comment on the issue when it admits a feature attempt, when it admits a review round (naming the pull request and the comments it will address), when an attempt stops with `needs-input`, fails, is retried, is cancelled, or produces a pull request, and when a review round completes (linking the pull request and summarizing what was changed and answered). The admission comment SHALL include the resolved refs and attempt number and SHALL state whether the attempt starts fresh, resumes at a named step, or continues a prior claim's branch, and SHALL state when a resume point was unavailable and the attempt started fresh. The comment reporting a `needs-input` stop SHALL list the specific questions, summarize the direction drafted so far, and link the pushed branch; for a `preflight` stop it SHALL state that no branch was created and that the next attempt starts fresh. The comment linking a produced pull request SHALL state the number of red, orange, and yellow items of the final classification defined by `factory-feature-execution`, matching the pull request description. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a feature claim's inputs are accepted SHALL read `Feature inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a resumed attempt

- **WHEN** the factory admits a resumed attempt for a claim that stopped during design
- **THEN** the issue receives one comment naming the refs, the attempt number, and that the attempt resumes at design

#### Scenario: Report a definition stop

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the issue receives one comment listing the questions, summarizing the drafted direction, and linking the pushed branch

#### Scenario: Report flag counts with the pull request

- **WHEN** a feature attempt returns `pull-request` with two red items, three orange items, and twelve yellow items
- **THEN** the comment linking the pull request states two red flags, three orange flags, and twelve yellow items

#### Scenario: Report a fresh start after an unavailable resume point

- **WHEN** a continuing claim's prior branch no longer exists
- **THEN** the outcome comment states that the prior branch was unavailable and the attempt started fresh

#### Scenario: Accept a feature claim's inputs

- **WHEN** the factory accepts and freezes a feature claim's inputs
- **THEN** the acceptance comment reads `Feature inputs accepted and frozen.`

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Map feature outcomes to the board

On `pull-request`, the factory SHALL link the pull request in a comment, move the card to Review, and set `Verdict=pending-human-review`. On `needs-input` from a feature attempt's definition, it SHALL post the reasons, apply the `needs-input` label, leave the card in Running, and release the feature slot. On `needs-input` from a review attempt, it SHALL post the reasons on the issue, apply the `needs-input` label, return the card to Review, restore the verdict it held when the round was admitted, and release the feature slot. On `failed`, it SHALL post the reasons, link the pushed branch or open pull request, move the card to Review, and set `Verdict=failed`. On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation. While a review attempt runs, the card SHALL be in Running with its verdict cleared. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a feature pull request

- **WHEN** a feature attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the pull request link is on the issue

#### Scenario: Park a feature stopped during definition

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the card stays in Running with the `needs-input` label, and the reasons and pushed branch link are on the issue
- **AND** another eligible feature can be admitted to the feature slot

#### Scenario: Report a failed feature without a pull request

- **WHEN** a feature attempt returns `failed` after the validator stayed red
- **THEN** the card moves to Review with `failed`, and the reasons and the pushed branch link are on the issue

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Annotate the feature pull request by review attention

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a "Review first" section that lists every red item, then every orange item, then every yellow item, one line each, from the classification defined by `factory-feature-execution`, each linking to its detail. Red, orange, and yellow SHALL be visually distinct and SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference without a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, the full assumptions ledger with each assumption's resolution from assumption review, and the acceptance evidence, with white items and the full ledger collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits from the finalization loop exist, SHALL list them. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange and yellow item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the "Review first" section states that the red and orange tiers are empty and lists the yellow assumptions
- **AND** the passed criteria and their evidence are collapsed below it

#### Scenario: Repair CI after acceptance

- **WHEN** the finalization loop pushes commits to fix CI after acceptance ran
- **THEN** those commits appear as an orange item and the evidence names the accepted commit and lists the later commits
- **AND** the orange count in the description and in the issue comment includes that item

#### Scenario: Complete a review round on a feature

- **WHEN** a review round on a feature pull request completes
- **THEN** the acceptance evidence still names the commit it describes and the completion comment states that acceptance was not re-run

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix and feature claims, settled fix and feature claims with eligible review comments waiting for their kind's slot, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's state including whether it is stopped for a quota hold, and the attempt's recorded deadline; it SHALL also list Machines that reconciliation reported as unknown to the store or as failed cleanup. It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a merge sync, or holding a recorded cleanup failure; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a waiting review round, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its kind's slot is busy
- **THEN** status names the claim, the PR, and that it waits for the slot

#### Scenario: Inspect an installation with history

- **WHEN** the database holds many Done and superseded claims and one running claim
- **THEN** status lists the running claim and none of the settled ones
- **AND** `status --all` lists every saved claim

#### Scenario: Inspect a Fly attempt

- **WHEN** the operator requests status while an eval repetition runs in, or is stopped in, a Fly Machine
- **THEN** status shows the Machine identity, whether it is running or stopped for a quota hold, and the attempt's deadline

#### Scenario: Inspect a reconciliation finding

- **WHEN** reconciliation has reported a Machine unknown to the store or a cleanup that could not be verified
- **THEN** status lists that Machine and the reported reason until it is resolved

#### Scenario: Inspect a blocked feature

- **WHEN** a feature claim stopped during definition
- **THEN** status shows the feature slot as free and the blocked feature with its stop reason and pushed branch


Portion for this task:

- "Resume and continue feature work" and "Require an OpenSpec repository": the factory
  side — choosing `resume_from` and `prior_branch`, reconciliation, re-admission after a
  `preflight` stop as a fresh definition, and the session-report copy. The workflow's
  branch handling, artifact revision, and fallback recording already exist.
- "Comment on feature activity" and "Map feature outcomes to the board": resumed and
  continued admissions, fallback-to-fresh reporting, stops, review-round admission and
  completion, and review-round `needs-input`.
- "Annotate the feature pull request by review attention": only the scenario "Complete a
  review round on a feature" (the completion comment states that acceptance was not
  re-run).
- "Configure deployment without Codagent-specific controller code": the scenario "Supply
  the Codagent deployment" (the `[feature]` section in `config/codagent.toml`) and the
  review-round part of "Remove the feature section with feature claims in flight" (a
  settled feature claim still receives review rounds after `[feature]` is removed). The
  configuration schema, host-only rejection, and the other scenarios already hold.
- "Expose current operational status": the "Inspect a blocked feature" and "Inspect a
  waiting review round" scenarios for feature claims.

## Test Plan

- `INT-006` — stops, resumes, continuations, and review rounds at admission. Boundary:
  controller, pull-request handler, blocked and review processing, stub GitHub, with
  stubbed launch capture (no workflow execution). Setup: feature claims whose recorded
  outcomes are `needs-input` with `stopped_step=design`, `needs-input` with
  `stopped_step=preflight`, `failed` after the plan checkpoint, `failed` after CI with its
  pull request left open, a technical failure whose pushed branch (in a real bare remote)
  carries a `Factory-Checkpoint: archived` trailer, a technical failure whose branch carries
  only `planned`, and `pull-request` with an open pull request; a fix attempt holding the
  fix slot. Action: post a writer comment, a non-writer comment, and a factory comment; drag
  cards Running → Ready and Review → Ready; add a writer pull-request comment; run cycles.
  Assertions: the blocked claim resumes on the same claim and branch with
  `resume_from=design` and the writer comment in its input; non-writer and factory comments
  start nothing; the failed claim yields a new claim with re-resolved commits,
  `prior_branch` set, and `resume_from=implement`; the preflight stop is re-admitted as a
  fresh definition; the recovery of the `archived` branch launches with
  `resume_from=verify`, the prior attempt's session reports copied into the new session
  directory, and no artifact reconciliation (empty `prior_branch`) — or, when the Runner
  refuses a pre-populated session directory (see the session-report fallback above), launches
  with `resume_from=implement`, copies no session reports, and still runs no artifact
  reconciliation; the recovery of the
  `planned`-only branch launches with `resume_from=implement`; dragging the failed claim
  whose pull request is open to Ready creates no claim, restores Review, and posts one
  explanation; the review round is admitted through the feature slot under feature limits
  ahead of a new Ready feature while the fix slot is busy; a stopped feature stays in
  Running with `needs-input` and releases the feature slot; an open non-draft factory pull
  request settles a claim as handed off, while the claim's own draft is treated as the
  resume point. File: `tests/integration/test_feature_gestures.py`.
- `INT-004` (review-round portion) — feature configuration reaches runtime. Setup: a
  feature claim settled with an open pull request and an eligible writer pull-request
  comment. Action: remove `[feature]` from the configuration and run ticks. Assertion: the
  settled claim still gets review-round admission through the feature slot while no new
  Feature card is handed off or admitted. File: extend
  `tests/integration/test_feature_config.py`, which already covers the rest of INT-004.

## Done When

- `resume_from` and `prior_branch` are computed at admission from the recorded outcome and
  the pushed branch's `Factory-Checkpoint` trailers, and passed to the host plan; session
  reports are copied for resumes at `archive` or later.
- The `RESUME_FROM_OWN_BRANCH` reconciliation, the feature gestures, the open-pull-request
  refusal with its one-time explanation, and feature review rounds through the feature slot
  behave as specified.
- Stop, resume, continuation, fallback, and review-round comments and board mapping are
  delivered once through durable reporting.
- INT-006 and the review-round portion of INT-004 pass. If
  `tests/integration/test_feature_workflow_catalog.py` contains
  `test_runner_refuses_prepopulated_session_dir`, INT-006 asserts the fallback
  (`resume_from=implement`, no session-report copy) instead of `resume_from=verify`, and the
  completion report states that this closes the open INT-009 condition, and the existing fix gesture, review, and blocked-claim suites
  (`tests/integration/test_fix_gestures.py`, `tests/integration/test_review_contract.py`,
  `tests/integration/test_status_repair_reporting.py`) still pass.
- `docs/operations.md` documents the feature retry gestures.
- `config/codagent.toml` carries the `[feature]` section in the task's last commit, and the
  full suite passes with it present.
- `uv run pytest`, `uv run ruff format --check . && uv run ruff check .`, and `uv run pyright`
  pass.
