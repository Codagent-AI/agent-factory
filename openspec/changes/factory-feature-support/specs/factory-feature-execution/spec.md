## ADDED Requirements

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

A resumed attempt on a blocked claim SHALL check out the claim's branch and continue at the definition step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target's recorded commit, and continue at implementation with the prior committed plan; when the prior branch's last pushed phase is `archived`, the new claim SHALL instead continue at verification of the archived change. An attempt resumed after a definition stop, or a new claim continuing a prior claim at implementation, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A new claim continuing at verification SHALL NOT revise the archived artifacts. A technical recovery retry SHALL NOT revise existing artifacts. When the resume point is missing or cannot be used, including an unresolvable merge, the workflow SHALL start a fresh definition and state in its outcome that it did so. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Resume after answering a definition question

- **WHEN** a claim stopped during design and a writer answers the question on the issue
- **THEN** the resumed attempt checks out the claim's branch, revises the proposal or specifications where the answer conflicts with them, and continues at design

#### Scenario: Continue a failed feature on a new claim

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after its plan commit
- **THEN** the workflow merges the target's recorded commit into that branch and starts at implementation with the prior plan
- **AND** it revises the plan first when the current issue or eligible comments warrant it

#### Scenario: Continue a feature whose prior claim failed after archival

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after pushing its `archived` checkpoint
- **THEN** the workflow merges the target's recorded commit into that branch and continues at verification of the archived change
- **AND** it does not revise the archived plan or its artifacts

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

### Requirement: Implement the change as one task

After the plan commit the feature workflow SHALL implement the whole change as a single task through the Runner's `core/implement-task` builtin workflow with a session report, following the repository's conventions for tests.

#### Scenario: Implement the planned change

- **WHEN** the plan is committed
- **THEN** the workflow implements the single task with tests and records the implementation session report under the Runner session directory

### Requirement: Archive the change before verification

After implementation the feature workflow SHALL archive the OpenSpec change, applying its specification deltas to the repository's specifications, and commit the result before any verification, draft pull request, or acceptance runs, so that verification and acceptance evidence describe the tree the pull request carries.

#### Scenario: Verify against the archived tree

- **WHEN** implementation completes
- **THEN** the change is archived and committed
- **AND** assumption review, the validator, and acceptance run against the archived tree

### Requirement: Verify the change and open a draft pull request

After archiving, the feature workflow SHALL run the Runner's `core/verify-change` builtin workflow given the archived change directory: assumption review, simplify, the validator, the clean-tree check, a draft pull request, and acceptance preparation against the test plan. A validator that remains red after its bounded repair SHALL return `failed` with reasons; the branch SHALL be pushed and no pull request opened.

#### Scenario: Open a draft pull request

- **WHEN** the validator passes after simplify
- **THEN** the workflow pushes the branch, opens a draft pull request, and prepares acceptance evidence against the test plan

#### Scenario: Stay red after validator repair

- **WHEN** the validator remains red after its bounded repair
- **THEN** the workflow pushes the branch, opens no pull request, and returns `failed` with the failing checks

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. Before finalizing, it SHALL classify every item a reviewer may need to examine into exactly one tier:

- red: an acceptance criterion that failed or could not be verified, acceptance that did not complete, a validator that stayed red after an acceptance fix, any known deviation from the specifications, and a resume or continuation that fell back to a fresh start;
- orange: decision-bearing assumptions, whose alternative a reasonable reviewer might choose and which shape behavior; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop adds after classification, which acceptance evidence does not cover;
- yellow: every other recorded assumption;
- white: acceptance criteria that passed, with their evidence.

The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. After finalization, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

#### Scenario: Acceptance does not converge

- **WHEN** acceptance preparation ends without completing
- **THEN** the workflow continues to finalization and classifies the unmet criteria and the incomplete acceptance as red

#### Scenario: Validator stays red after an acceptance fix

- **WHEN** the validator remains red after its bounded repair in an acceptance round
- **THEN** acceptance and finalization continue, and the pull request lists the red validator, with its failing checks, as a red item

#### Scenario: A decision-bearing assumption remains

- **WHEN** assumption review leaves a decision-bearing assumption unresolved
- **THEN** the workflow continues to finalization and classifies the assumption as orange

#### Scenario: Everything passes

- **WHEN** every acceptance criterion passes and no assumption is decision-bearing
- **THEN** the red and orange tiers are empty, the assumptions are yellow, and the criteria are white

### Requirement: Finalize the feature pull request

After classification, the feature workflow SHALL reuse the Runner's generic finalization workflow to mark the pull request ready, wait for CI, and address failures within its bounded loop. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue without a closing keyword and SHALL identify the factory claim in a stable marker. The workflow SHALL return `pull-request` with the pull request reference when CI passes.

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
