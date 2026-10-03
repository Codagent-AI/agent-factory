# factory-feature-execution Specification

## Purpose
TBD - created by archiving change factory-feature-support. Update Purpose after archive.
## Requirements
### Requirement: Invoke the versioned feature workflow

The factory SHALL run the packaged `factory-feature` workflow, with its `factory-define` sub-workflow, through the operator's installed Agent Runner, passing the configured feature role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, the attempt's artifact directory, and the attempt's resume point when one exists. The resume point SHALL be the claim's own pushed branch, or, for a new claim continuing a settled prior claim, the prior claim's branch supplied at admission. The factory SHALL ship the feature workflows beside the fix, review, and shared implementation workflows and stage all of them into the Runner catalog the attempt uses. The workflow contract SHALL be versioned as `factory-feature/1`. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the installed Runner does not provide what the workflow requires, including the `core/verify-change` builtin workflow, and SHALL hold the claim and report the problem in status and doctor. The workflow SHALL write exactly one structured outcome to `feature-outcome.json` in the attempt's artifact directory, declaring its contract version: `pull-request` with the pull request reference; `needs-input` with reasons; or `failed` with reasons. Absence of a structured outcome SHALL be treated as a technical failure. A feature attempt SHALL work on a deterministic branch named from the issue and claim.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged feature workflow declares a compatible contract version and the installed Runner provides `core/verify-change`
- **THEN** the attempt starts under its own supervisor on the host with the configured feature roles
- **AND** the staged workflow directory holds the feature, define, fix, review, and implementation workflow files

#### Scenario: Launch against a Runner without verify-change

- **WHEN** the installed Runner lacks the `core/verify-change` builtin workflow
- **THEN** no attempt is recorded, the feature is held, and status and doctor name the missing workflow

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `feature-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy

### Requirement: Require an OpenSpec repository

Before defining a change, the feature workflow SHALL check that the target clone contains an `openspec/` directory and an Agent Validator configuration (`.validator/config.yml`). When either is missing, the workflow SHALL return `needs-input` naming what is missing (OpenSpec initialization or Agent Validator configuration), with the stopped step recorded as `preflight`, and SHALL push no branch and open no pull request. An attempt re-admitted after a `preflight` stop SHALL start a fresh definition.

#### Scenario: Hand off a feature in a repository without OpenSpec

- **WHEN** a feature is admitted for a configured target that has no `openspec/` directory
- **THEN** the workflow returns `needs-input` naming the missing OpenSpec initialization with stopped step `preflight`
- **AND** it pushes no branch and opens no pull request

#### Scenario: Hand off a feature in a repository without Agent Validator configuration

- **WHEN** a feature is admitted for a configured target that has `openspec/` but no `.validator/config.yml`
- **THEN** the workflow returns `needs-input` naming the missing Agent Validator configuration with stopped step `preflight`
- **AND** it pushes no branch and opens no pull request

#### Scenario: Re-admit after OpenSpec is initialized

- **WHEN** a feature stopped at `preflight` and, after the repository was initialized for OpenSpec, a writer comments on the issue
- **THEN** the re-admitted attempt starts a fresh definition

### Requirement: Define the change autonomously

The feature workflow SHALL create an OpenSpec change for the issue and define it without human interaction, in this order: proposal; an adversarial proposal review by a fresh crosscheck agent; specifications; design; test plan; an approach review of all definition artifacts together by a fresh crosscheck agent; a `tasks.md` with a single task covering the whole change; OpenSpec validation with bounded mechanical repair; the planning-artifact check requiring the proposal, specifications, design, test plan, and tasks; and a commit of the plan. There SHALL be no task planning and no task review. When a definition step reaches a decision the issue and eligible comments do not answer, the workflow SHALL decide it and record the decision as an explicit assumption in the relevant artifact, unless the decision is direction-level. A decision is direction-level when it contradicts the issue, when the issue admits materially different readings, when it would break a public interface or persisted data format, or when it requires a decision or change outside the target repository. The lead SHALL apply review findings it judges correct without human confirmation, unless a finding raises a direction-level decision. Each review SHALL run as its own workflow step in a fresh reviewer session using the configured crosscheck role, in a single pass. Every recorded decision, review-finding disposition, and plan revision SHALL be appended to a decision log inside the change directory, which is archived with the change. After the plan commit the workflow SHALL push the branch without opening a pull request. The workflow SHALL also push the branch after implementation and after archival, so that each completed phase survives a technical failure. Each checkpoint commit SHALL identify its phase (`planned`, `implemented`, or `archived`) in the commit itself, so the last completed phase is determined from the pushed branch rather than from local attempt evidence.

#### Scenario: Define a well-described feature

- **WHEN** the issue describes a feature whose open decisions are all below direction level
- **THEN** the workflow produces and commits the proposal, specifications, design, test plan, and single-task `tasks.md`, each open decision recorded as an assumption
- **AND** it pushes the branch and continues to implementation without opening a pull request

#### Scenario: Apply a review finding

- **WHEN** the approach review reports a consequential gap that does not raise a direction-level decision
- **THEN** the lead revises the affected artifacts and continues without stopping

#### Scenario: Fail validation after repair

- **WHEN** OpenSpec validation still fails after its bounded mechanical repair
- **THEN** the workflow returns `failed` with the validation errors

### Requirement: Stop definition for a direction-level decision

Any definition step, from the proposal through the approach review, SHALL stop the run with `needs-input` when it reaches a direction-level decision. The proposal step SHALL also stop the run with `needs-input` when it judges the feature should not be built as described, stating why. On a stop the workflow SHALL commit the artifacts drafted so far, push the branch, and open no pull request. The outcome SHALL name the specific questions or objections, summarize the direction drafted so far, and identify the pushed branch.

#### Scenario: Stop at the proposal

- **WHEN** the proposal step finds that the issue admits two materially different features
- **THEN** the workflow commits the draft proposal, pushes the branch, and returns `needs-input` naming both readings and what must be decided
- **AND** it opens no pull request

#### Scenario: Stop during design

- **WHEN** the design step finds that the specified behavior requires breaking a public interface
- **THEN** the workflow commits the proposal, specifications, and draft design, pushes the branch, and returns `needs-input` naming the interface and the decision required

### Requirement: Resume and continue feature work

A resumed attempt on a blocked claim SHALL check out the claim's branch, bring it up to date with the target branch as `Merge the target branch into the claim's branch on every resume` requires, and continue at the step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target branch's current head, carry the prior committed plan over to its own change name, and continue at implementation with that plan; when the prior branch's last pushed phase is `archived`, the new claim SHALL instead continue at verification of the archived change. An attempt resumed after a definition stop, or a new claim continuing a prior claim at implementation, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A new claim continuing at verification SHALL NOT revise the archived artifacts. A technical recovery retry SHALL NOT revise existing artifacts. When neither the prior branch nor the claim's own branch exists, the workflow SHALL start a fresh definition and state in its outcome that it did so. If the prior branch is unavailable but the claim's own branch exists, the workflow SHALL resume from its own branch. A merge conflict SHALL NOT cause a fresh start. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. The last completed phase SHALL be read from the claim's own branch history only, so checkpoints that reach the branch through a merge of the target branch SHALL NOT count as the claim's progress, and a merge SHALL NOT change which step an attempt resumes at. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Resume after answering a definition question

- **WHEN** a claim stopped during design and a writer answers the question on the issue
- **THEN** the resumed attempt checks out the claim's branch, merges the target branch's current head into it, revises the proposal or specifications where the answer conflicts with them, and continues at design

#### Scenario: Continue a failed feature on a new claim

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after its plan commit
- **THEN** the workflow merges the target branch's current head into that branch and starts at implementation with the prior plan under the new claim's change name
- **AND** it revises the plan first when the current issue or eligible comments warrant it

#### Scenario: Continue a feature whose prior claim failed after archival

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after pushing its `archived` checkpoint
- **THEN** the workflow merges the target branch's current head into that branch and continues at verification of the archived change under the new claim's change name
- **AND** it does not revise the archived plan or its artifacts

#### Scenario: Continue when the prior branch is gone

- **WHEN** a new claim is supplied with a prior branch that no longer exists
- **THEN** the workflow starts a fresh definition and states in its outcome that the prior branch was unavailable

#### Scenario: Continue a prior branch that conflicts with the target branch

- **WHEN** a new claim is supplied with a prior branch whose changes conflict with the target branch's current head
- **THEN** the attempt ends with either a resolved merge commit on the claim's branch that keeps the prior plan and implementation, or `needs-input` listing the conflicting files
- **AND** it never starts a fresh definition because of the conflict

#### Scenario: Recover after verification began

- **WHEN** an attempt fails technically after pushing its `archived` checkpoint
- **THEN** the recovery retry continues at verification from the pushed branch and does not revise the plan or its artifacts

#### Scenario: Crash before a checkpoint is pushed

- **WHEN** an attempt fails technically after committing its implementation but before pushing the `implemented` checkpoint
- **THEN** the recovery retry finds `planned` as the last pushed phase and redoes implementation

#### Scenario: Recover after a crash during implementation

- **WHEN** an attempt fails technically after pushing its plan commit
- **THEN** the recovery retry starts from fresh clones, checks out the pushed branch, and continues at implementation

#### Scenario: Resume point ignores checkpoints merged from the target branch

- **WHEN** a claim whose last pushed phase is `planned` resumes after the target branch gained merged feature pull requests whose commits carry `implemented` and `archived` checkpoints
- **THEN** after the merge the attempt still resumes at implementation
- **AND** a later attempt on the same claim also reads `planned` as the last completed phase until the claim pushes a checkpoint of its own

#### Scenario: Resume order is unchanged by a merge

- **WHEN** a claim with a recorded `needs-input` stop, an open draft pull request, or a prior-branch continuation resumes and the merge adds a merge commit to its branch
- **THEN** the attempt resumes at the same step it would have resumed at without the merge: the stopped step for `needs-input`, then verification for a draft, then the continuation's step

### Requirement: Implement the change as one task

After the plan commit the feature workflow SHALL implement the whole change as a single task through the Runner's `core/implement-task` builtin workflow with a session report, following the repository's conventions for tests.

#### Scenario: Implement the planned change

- **WHEN** the plan is committed
- **THEN** the workflow implements the single task with tests and records the implementation session report under the Runner session directory

### Requirement: Archive the change before verification

After implementation the feature workflow SHALL archive the OpenSpec change, applying its specification deltas to the repository's specifications, and commit the result before any verification, draft pull request, or acceptance runs, so that verification and acceptance evidence describe the tree the pull request carries.
If archive repair declares `REPAIR_BLOCKED`, the workflow SHALL retain the pushed implemented branch and return `needs-input` with the archive explanation and an archive resume point. The outcome's direction summary SHALL tell the operator that the cause can be fixed on the target branch, or committed to the claim's branch, and that commenting on the issue resumes the claim at archive with the target branch merged in.

#### Scenario: Verify against the archived tree

- **WHEN** implementation completes
- **THEN** the change is archived and committed
- **AND** assumption review, the validator, and acceptance run against the archived tree

#### Scenario: Archive repair is blocked

- **WHEN** the archive step stops with a `REPAIR_BLOCKED` declaration
- **THEN** the workflow returns `needs-input` with stopped step `archive` and the agent's explanation
- **AND** the direction summary says that a fix merged to the target branch reaches the claim when it resumes
- **AND** the pushed implemented branch is retained and no pull request is opened
- **AND** the next attempt resumes at archive

#### Scenario: Unblock archive after a fix lands on the target branch

- **WHEN** a claim stopped at archive because of a problem in a file on the target branch, a fix to that file is merged to the target branch, and a writer comments on the issue
- **THEN** the resumed attempt merges the target branch's current head into the claim's branch and the archive succeeds
- **AND** the attempt continues to verification

### Requirement: Verify the change and open a draft pull request

After archiving, the feature workflow SHALL run the Runner's `core/verify-change` builtin workflow given the archived change directory: assumption review, simplify, the validator, the clean-tree check, a draft pull request, and acceptance preparation against the test plan. A validator that remains red after its bounded repair SHALL return `failed` with reasons; the branch SHALL be pushed and no pull request opened.

#### Scenario: Open a draft pull request

- **WHEN** the validator passes after simplify
- **THEN** the workflow pushes the branch, opens a draft pull request, and prepares acceptance evidence against the test plan

#### Scenario: Stay red after validator repair

- **WHEN** the validator remains red after its bounded repair
- **THEN** the workflow pushes the branch, opens no pull request, and returns `failed` with the failing checks

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. Before finalizing, it SHALL classify every item a reviewer may need to examine into exactly one tier, making items that share one root cause a single item in the highest tier any of them reaches:

- red: an acceptance criterion that failed, or that could not be verified and that no automated test covers, acceptance that did not complete, a validator that stayed red after an acceptance fix, any known deviation from the specifications or from a decision the issue settled, a resume or continuation that fell back to a fresh start, and a declared task-compliance review that did not run or that stayed red after its bounded repair;
- orange: decision-bearing assumptions, which settle a choice the issue left open in a way that changes scope, weakens a guarantee, affects other callers, or is costly to reverse; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop adds after classification, which acceptance evidence does not cover, where the classification's item for commits it saw states their diff size and whether tests cover them, and the item names which of those commits come after the head the last task-compliance verdict reviewed and states that the verdict does not cover them;
- yellow: an acceptance criterion that acceptance did not exercise but that a named automated test covers, naming that test; an item that only follows a decision the issue settled or an acceptance criterion, which SHALL NOT be ranked higher; a target that does not declare a task-compliance review; and every other recorded assumption or decision;
- white: acceptance criteria that passed, with their evidence, and a task-compliance review that passed, naming the head it reviewed.

Related orange assumptions SHALL be grouped into one item per topic that cites the decisions behind it, and orange items SHALL be ordered most important first. Each item SHALL cite the committed file, and the line or range within it where one applies, or the evidence that shows it. The task-compliance items SHALL be added from the attempt's recorded task-compliance result, not from agent judgment, so the classifying agent cannot omit or re-tier them. The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. After finalization, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

#### Scenario: Acceptance does not converge

- **WHEN** acceptance preparation ends without completing
- **THEN** the workflow continues to finalization and classifies the unmet criteria and the incomplete acceptance as red

#### Scenario: Validator stays red after an acceptance fix

- **WHEN** the validator remains red after its bounded repair in an acceptance round
- **THEN** acceptance and finalization continue, and the pull request lists the red validator, with its failing checks, as a red item

#### Scenario: An automated test covers a criterion acceptance did not exercise

- **WHEN** acceptance did not exercise an acceptance criterion but a named automated test covers it
- **THEN** the workflow classifies the criterion as yellow, naming the covering test, rather than red

#### Scenario: The change deviates from a settled issue decision

- **WHEN** the implementation departs from a decision the issue settled
- **THEN** the workflow classifies the deviation as red

#### Scenario: An item only follows a settled decision

- **WHEN** an assumption or choice only restates a decision the issue settled or an acceptance criterion
- **THEN** the workflow classifies it as yellow at most

#### Scenario: Several failures share one root cause

- **WHEN** several failing criteria or findings come from one root cause
- **THEN** the workflow records them as one item in the highest tier any of them reaches, listing each symptom

#### Scenario: Commits follow acceptance

- **WHEN** commits were added after the accepted head
- **THEN** the orange item naming them states their diff size and whether tests cover them, and the workflow's classification check rejects an item that omits either

#### Scenario: A decision-bearing assumption remains

- **WHEN** assumption review leaves a decision-bearing assumption unresolved
- **THEN** the workflow continues to finalization and classifies the assumption as orange

#### Scenario: Everything passes

- **WHEN** every acceptance criterion passes and no assumption is decision-bearing
- **THEN** the red and orange tiers are empty, the assumptions are yellow, and the criteria are white

#### Scenario: Task-compliance did not run

- **WHEN** the target declares task-compliance and the attempt's task-compliance result is `not-run`
- **THEN** the classification has a red item naming the reason, such as `Trusted`, `no_applicable_gates`, `no_changes`, or a validator error
- **AND** the workflow continues to finalization

#### Scenario: Task-compliance stays red

- **WHEN** the attempt's task-compliance result is `failed` after its bounded repair
- **THEN** the classification has a red item listing the unresolved task-compliance violations
- **AND** the workflow continues to finalization

#### Scenario: The classifying agent omits the task-compliance item

- **WHEN** the agent's classification leaves out a `not-run` or `failed` task-compliance result, or ranks it below red
- **THEN** the classification used for reporting still lists it as red, exactly once

#### Scenario: Commits follow the last task-compliance review

- **WHEN** finalization adds commits after the head the last task-compliance verdict reviewed
- **THEN** the orange item naming commits after acceptance names those commits and states that the task-compliance verdict does not cover them

#### Scenario: Task-compliance is not declared

- **WHEN** the target's validator configuration declares no task-compliance review
- **THEN** the classification has a yellow item stating that task-compliance is not declared and did not run, and no red task-compliance item

### Requirement: Finalize the feature pull request

After classification, the feature workflow SHALL reuse the Runner's generic finalization workflow to mark the pull request ready, wait for CI, and address failures within its bounded loop. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue with a closing keyword, so GitHub links the pull request to the issue and closes the issue when the pull request merges, and SHALL identify the factory claim in a stable marker. The workflow SHALL return `pull-request` with the pull request reference when CI passes.

#### Scenario: Finalize a passing pull request

- **WHEN** classification completes and CI passes
- **THEN** the pull request is ready for review and the workflow returns `pull-request`, whatever the red and orange tiers contain

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the finalization loop
- **THEN** the workflow returns `failed` with the failing checks and leaves the pull request open

### Requirement: Apply feature-specific limits and window

Each feature attempt SHALL have configurable limits with defaults of 30 minutes without progress, six hours of execution, and eight hours of total elapsed time. Feature admission SHALL use its own configurable window, defaulting to always open, and SHALL honor pause, disk and memory admission checks, and provider quota holds for providers used by the feature roles. Feature admission SHALL NOT be bound to the eval or fix window. Exceeding a limit SHALL stop owned execution and be treated as a technical failure.

#### Scenario: Exceed the execution limit

- **WHEN** a feature attempt runs for six hours
- **THEN** the factory stops its owned execution and applies the recovery policy

#### Scenario: Admit a feature outside the eval window

- **WHEN** the eval window is closed and the feature window is open
- **THEN** an eligible feature is admitted

### Requirement: Execute features on the host only

Feature attempts SHALL run only in `host` execution mode. Configuration that selects Docker or Fly execution for the feature kind SHALL be rejected when configuration loads. A feature attempt SHALL follow the host execution rules of `factory-fix-execution` using the fix credential: clones from local mirrors at the recorded commits, the operator's HOME and installed Runner, process-local git settings, workflows supplied through the clone, a Runner session directory under the attempt's artifact directory, the credential kept out of persisted state, supervision by process, host provenance, and preserved evidence including the session directory.

#### Scenario: Configure Docker execution for features

- **WHEN** the configuration selects Docker or Fly execution for the feature kind
- **THEN** configuration loading fails and names the unsupported mode

#### Scenario: Inspect a feature attempt's run record

- **WHEN** a feature attempt has launched
- **THEN** its run record stores the host execution mode, the Runner path and version, and the session directory, and contains no credential

### Requirement: Merge the target branch into the claim's branch on every resume

Every feature attempt that resumes or continues from an existing branch SHALL merge the current head of the claim's target branch into the claim's branch before the step it resumes at runs. This covers a resume after a `needs-input` stop, a resume from an open draft pull request, a technical recovery retry, and a new claim continuing a prior claim's branch. A claim's first attempt that starts fresh from the target SHALL NOT need a merge. The factory SHALL fetch the target repository and resolve the target branch's head when it prepares the attempt, rather than reuse the commit recorded at admission. The claim's recorded target, Agent Runner, and Agent Skills revisions SHALL remain those frozen at admission. The attempt's provenance SHALL record both the target commit recorded at admission and the target-branch head merged by this attempt.

A merge that completes without conflicts SHALL be committed on the claim's branch as a merge commit that is not a phase checkpoint. When the target branch's head is already contained in the claim's branch, the attempt SHALL add no commit. When the merge conflicts, an agent step SHALL resolve the conflicts before the resumed step runs, preserving the intent of both the claim's changes and the target branch's changes. It SHALL commit the resolution first, then run the repository's validator and commit any necessary fixes separately as follow-up commits, which may change any file or add files. The resolution SHALL keep the claim's pre-merge history and the target branch's head as its parents, and SHALL change by hand only the files that conflicted. A resolution that drops the claim's commits, or changes the claim's or the target branch's non-conflicting files away from the automatic merge result, SHALL fail the attempt as a technical failure rather than be pushed. When the agent cannot resolve the conflicts confidently, the attempt SHALL return `needs-input` listing the conflicting files and the decision it needs. Its stopped step SHALL be the step the attempt was going to resume at. It SHALL NOT commit or push a partially merged tree, and it SHALL leave the claim's pushed branch holding the work it held before the merge. On a new claim continuing a prior branch whose own branch was never pushed, the claim's branch SHALL be pushed with the prior branch's work unchanged, so the next attempt resumes from it rather than starting fresh. The next attempt SHALL merge the target branch's then-current head again, with the writer's answer among the eligible comments, and continue at the recorded step.

When the merge added commits to the claim's branch, the attempt SHALL NOT rely on validation results from before the merge. An attempt that would otherwise skip verification SHALL run verification before finalizing the pull request. Attempts that resume at or before archive run verification as they already do.

#### Scenario: Resume a stopped claim after the target branch moved

- **WHEN** a claim stopped with `needs-input` at design, the target branch gained commits since admission, and a writer answers on the issue
- **THEN** the resumed attempt merges the target branch's current head into the claim's branch before continuing at design
- **AND** the attempt's provenance records the admission target commit and the merged head

#### Scenario: Recover from a technical failure after the target branch moved

- **WHEN** an attempt fails technically after pushing its `planned` checkpoint and the target branch gained commits before the recovery retry launches
- **THEN** the recovery retry merges the target branch's current head into the pushed branch and continues at implementation

#### Scenario: Resume when the target branch has not moved

- **WHEN** a claim resumes and its branch already contains the target branch's current head
- **THEN** no merge commit is added and the attempt continues at its resume point

#### Scenario: Resolve a conflicting merge

- **WHEN** a resumed claim's branch conflicts with the target branch's current head and the agent can reconcile both sides
- **THEN** the attempt commits a merge that resolves every conflict, runs the validator on it, and then continues at its resume point
- **AND** the branch keeps the claim's plan and implementation

#### Scenario: Reject a resolution that discards the claim's work

- **WHEN** the conflict-resolution agent resets the branch to the target branch's head, or commits a merge that drops the claim's non-conflicting changes
- **THEN** the attempt fails technically before anything is pushed, and the claim's pushed branch keeps its work

#### Scenario: Stop on a conflict the agent cannot resolve

- **WHEN** a resumed claim's branch conflicts with the target branch's current head and the agent cannot resolve the conflict confidently
- **THEN** the attempt returns `needs-input` listing the conflicting files and the decision it needs, with the step it was going to resume at as its stopped step
- **AND** the claim's pushed branch is unchanged and holds no conflict markers
- **AND** after a writer answers, the next attempt merges the target branch again and continues at that step

#### Scenario: Stop on a conflict while continuing a prior branch

- **WHEN** a new claim continuing a prior branch cannot resolve a conflict with the target branch's current head
- **THEN** the attempt returns `needs-input` listing the conflicting files, and the claim's branch is pushed with the prior branch's work unchanged
- **AND** after a writer answers, the next attempt resumes from the claim's branch rather than starting a fresh definition

#### Scenario: Re-verify after a merge brings in new commits

- **WHEN** an attempt resumes at a step after verification and the merge added commits from the target branch
- **THEN** the attempt runs verification on the merged tree before finalizing the pull request

#### Scenario: Keep frozen revisions across a resume

- **WHEN** a claim resumes after the target branch, Agent Runner, and Agent Skills all gained commits since admission
- **THEN** the attempt merges the target branch's current head into the claim's branch
- **AND** it uses the Agent Runner and Agent Skills commits recorded at admission, and the claim's recorded target commit is unchanged

### Requirement: Gate the feature on a bound task-compliance verdict

When the target's Agent Validator configuration declares a review named `task-compliance` under any entry point, the feature workflow SHALL obtain a task-compliance verdict for the change before it classifies review attention, and SHALL NOT treat a validator pass as one. A task-compliance verdict SHALL count only when it is bound to all of the following:

- the base: the merge base of the reviewed head and the target branch head the attempt started from or last merged, so that the reviewed diff is exactly the change the pull request carries, including every commit since the claim's branch left the target branch and no change the target branch brought in;
- the reviewed head and its tree;
- the content of the change's `tasks.md` given to the review as context, identified by its hash;
- the full diff from the base to the reviewed head, not only the commits of one step, with every changed path outside `openspec/` lying under an entry point whose task-compliance review was dispatched, except paths that entry point itself excludes;
- a task-compliance review that actually executed and reached a terminal pass or fail.

The target branch head in the base SHALL be the one the claim's branch actually contains: on a resume or continuation, the target head that the resume merged, or found already contained; on a fresh start, the target head the branch was created from. A validator result of `Trusted`, `no_applicable_gates`, `no_changes`, a skipped prior pass, an error, or a run whose base, scope, or task context cannot be shown SHALL NOT be a verdict. Evidence that a review was dispatched and passed SHALL be accepted even when the validator has cleaned or rotated its logs after the pass. A review that passed for some changed paths but did not see others SHALL be `not-run`, naming the paths it did not see.

The workflow SHALL apply this gate once after verification, which includes simplify, acceptance, and any acceptance repair, and before classification. No earlier verdict exists in the attempt, so it SHALL run regardless of the implementation step's validator status or trust history. It SHALL review the base-to-head diff with `tasks.md` from the archived change directory as context and refuse a verdict whose task content differs from the archived tasks. The implementation step's own task-compliance run remains an early repair opportunity but SHALL NOT count as the attempt's verdict. Violations SHALL be repaired and reviewed again within the bounded repair. These repairs are commits added after acceptance. Commits added after the last verdict, including those finalization adds, SHALL NOT be reviewed again in the attempt.

The workflow SHALL record one task-compliance result for the attempt in its evidence: `passed`, `failed` when violations remain after the bounded repair, `not-run` with the validator status or error that prevented a verdict, or `not-declared` when the target declares no task-compliance review. The record SHALL name the base, the reviewed head, the tasks hash, and the validator output it relied on. When the target does not declare task-compliance, the workflow SHALL run no task-compliance review and SHALL record `not-declared`. A `failed` or `not-run` result SHALL NOT stop the workflow, push a different outcome, or withhold the pull request.

Each task-compliance review the workflow runs SHALL be unaffected by the claim clone's validator trust and review history, so that a tree the validator already trusts, or a review it already ran once, is still reviewed. A review that ends in an error SHALL be tried once more before the result becomes `not-run`.

#### Scenario: The implementation step's validator returns Trusted

- **WHEN** the implementation step's validator run reports `Status: Trusted` and evaluates no gates for a target that declares task-compliance
- **THEN** the workflow runs a task-compliance review of the diff from the base to the current head with the archived tasks as context before classification
- **AND** the attempt's task-compliance result is that review's result, not the implementation step's pass

#### Scenario: The implementer already committed its work

- **WHEN** the implementation step's validator run reports `no_applicable_gates` because the implementer committed before validation
- **THEN** the workflow still reviews the full diff from the base to the current head for task compliance before classification

#### Scenario: The implementation step's review ran and passed

- **WHEN** the implementation step's validator ran task-compliance and passed, but its review record does not name the base, the head, or the tasks it reviewed
- **THEN** the workflow still runs its own task-compliance review of the diff from the base to the current head before classification, and the attempt's result is that review's result

#### Scenario: A review ends in an error

- **WHEN** the workflow's task-compliance review ends in a reviewer error
- **THEN** the workflow runs it once more, and records `not-run` with the error only when the second run also produces no verdict

#### Scenario: Task-compliance finds a gap

- **WHEN** the workflow's task-compliance review reports a violation before classification
- **THEN** the violation is repaired and the review runs again within the gate's bounded repair before classification

#### Scenario: Task-compliance stays red

- **WHEN** task-compliance violations remain after the bounded repair
- **THEN** the attempt's task-compliance result is `failed` with the unresolved violations
- **AND** the workflow continues to classification and finalization

#### Scenario: No verdict can be produced

- **WHEN** every task-compliance run the workflow makes ends without an executed review, for example with `no_applicable_gates` because the change touches only paths the declaring entry point excludes, or with a validator error
- **THEN** the attempt's task-compliance result is `not-run` with that status or error
- **AND** the workflow continues to finalization and the pull request opens

#### Scenario: Verification adds commits

- **WHEN** simplify or an acceptance repair commits changes after implementation
- **THEN** before classification the workflow reviews the diff from the base to the current head, using the archived tasks
- **AND** the attempt's task-compliance result names the reviewed head

#### Scenario: A resume merges the target branch

- **WHEN** a resumed attempt merges the target branch's head into the claim's branch and continues at verification or finalization
- **THEN** the current attempt has no earlier verdict, and the workflow reviews the diff from the new merge base to the head before classification
- **AND** changes the merge brought in from the target branch are not part of the reviewed diff

#### Scenario: Resume after implementation

- **WHEN** an attempt resumes at archive, verification, or finalization and so skips implementation
- **THEN** the workflow determines the base from the claim's head and the target branch head again and still produces a task-compliance result before classification

#### Scenario: The validator cleans its logs after a pass

- **WHEN** the workflow's task-compliance review passes and the validator moves or deletes its review logs as part of cleaning up after a passing run
- **THEN** the attempt's task-compliance result is `passed`, not `not-run`

#### Scenario: Task-compliance is declared only under a subdirectory

- **WHEN** the target declares task-compliance only on an entry point under `packages/app`, and the change touches files both there and under `packages/lib`
- **THEN** the attempt's task-compliance result is `not-run`, naming the `packages/lib` files the review did not see

#### Scenario: A resume merged a newer target head

- **WHEN** a resumed attempt merged a target head newer than the one recorded at admission
- **THEN** the reviewed diff starts at the merge base with the merged head, and contains no change that exists only on the target branch

#### Scenario: The tasks changed after the review

- **WHEN** a recorded verdict's tasks hash differs from the archived change's tasks
- **THEN** the workflow does not use that verdict and reviews again with the archived tasks

#### Scenario: The target does not declare task-compliance

- **WHEN** the target's `.validator/config.yml` declares no review named `task-compliance`
- **THEN** the workflow runs no task-compliance review and records `not-declared`

### Requirement: Qualify the validator status in the feature outcome

The `feature-outcome.json` the workflow writes SHALL NOT report a validator pass for a change whose declared task-compliance review did not pass. For every outcome that carries a validator status, `validator.checks` SHALL hold the status of the validator gates other than the workflow's task-compliance gate, and `validator.status` SHALL be:

- `passed` when the checks passed and the task-compliance result is `passed` or `not-declared`;
- `incomplete` when the checks passed and the task-compliance result is `not-run`;
- `review-failed` when the checks passed and the task-compliance result is `failed`;
- `failed` when the checks failed, as before.

An outcome written after the task-compliance gate SHALL also carry a `task_compliance` object with the result, the reason for `not-run`, the base, the reviewed head, and the tasks hash. When the checks passed but no task-compliance result was recorded, the outcome SHALL carry a `not-run` result with reason `no task-compliance record`, and `validator.status` SHALL be `incomplete`. The outcome value SHALL be unchanged by the task-compliance result: an attempt that produces a pull request with CI passing SHALL return `pull-request` whatever the task-compliance result is. The contract version SHALL remain `factory-feature/1`, and outcomes written before the gate runs, such as definition stops, SHALL keep their current shape.

#### Scenario: Skipped task-compliance is not a pass

- **WHEN** an attempt returns `pull-request` with every check passing and a task-compliance result of `not-run`
- **THEN** the outcome's `validator.status` is `incomplete`, `validator.checks` is `passed`, and `task_compliance` names the reason

#### Scenario: Task-compliance stays red in the outcome

- **WHEN** an attempt returns `pull-request` with a task-compliance result of `failed`
- **THEN** the outcome's `validator.status` is `review-failed` and the outcome is still `pull-request`

#### Scenario: Every gate passes

- **WHEN** the checks and the task-compliance review pass
- **THEN** the outcome's `validator.status` and `validator.checks` are both `passed` and `task_compliance` names the reviewed head

#### Scenario: The task-compliance record is missing

- **WHEN** the checks passed and the attempt reaches its outcome with no recorded task-compliance result
- **THEN** the outcome's `validator.status` is `incomplete`, `task_compliance` records `not-run` with reason `no task-compliance record`, and the issue comment says that task-compliance did not run

#### Scenario: Undeclared task-compliance

- **WHEN** the target does not declare task-compliance and the checks pass
- **THEN** the outcome's `validator.status` is `passed` and `task_compliance` records `not-declared`

