## ADDED Requirements

### Requirement: Implement the change with the Runner's implement-change

After the plan commit the feature workflow SHALL implement the change through the Runner's `core/implement-change` builtin workflow on the change's directory, using the configured role profiles: the lead and the implementor each write a checklist of how every specified behavior is verified, the crosscheck merges them, the implementor implements against the merged checklist and records evidence for each row, the crosscheck audits the result by inspection alone, and the implementor resolves its findings, before the Runner's verification runs. Before it runs, the workflow SHALL append the factory's implementation constraints to the change's `design.md` once. The constraints SHALL tell the agents to decide and record rather than ask, to avoid test-driven development and manual testing by the implementor, to fix code rather than loosen a test timeout, weaken a test, or change a dependency to pass a check, and to leave a check that fails the same way at the merge base, or whose only remedy is such a change, failing and reported for a human. When the lead rules that no compliant implementation is possible without the user, the workflow SHALL return `needs-input` with stopped step `implement`, push the branch, and open no pull request. A failure before verification starts SHALL end the attempt without an outcome, as a technical failure. When the builtin succeeds the workflow SHALL push an `implemented` checkpoint that records the change as verified.

#### Scenario: Implement and audit a planned change

- **WHEN** the plan is committed
- **THEN** the workflow runs the Runner's `implement-change` on the change, whose crosscheck audit reads the code and the evidence, and whose verification runs the validator once before the draft pull request
- **AND** it pushes an `implemented` checkpoint with the `Factory-Verified: true` trailer

#### Scenario: Constrain the agents once

- **WHEN** the workflow starts implementation on a plan that already has the constraints in its design
- **THEN** it adds them no second time

#### Scenario: Implementation needs the user

- **WHEN** the lead rules that no compliant implementation is possible without a decision from the user
- **THEN** the workflow pushes the branch and returns `needs-input` with stopped step `implement` and the recorded question
- **AND** the next attempt after a writer answers resumes at implementation

#### Scenario: A step before verification fails

- **WHEN** the audit, or any step before verification, fails after its bounded repair
- **THEN** the attempt ends without an outcome and the factory applies its technical failure policy

### Requirement: Archive the change after verification

After implementation and verification the feature workflow SHALL archive the OpenSpec change, applying its specification deltas to the repository's specifications, and commit the result, as the Runner's change workflow does. Verification and acceptance therefore describe the change in place, and the archive commit and the phase checkpoints are the only commits after the head acceptance tested; they touch OpenSpec files only. The archive is skipped when the change directory no longer exists. If archive repair declares `REPAIR_BLOCKED`, the workflow SHALL retain the pushed implemented branch and return `needs-input` with the archive explanation and an archive resume point. The outcome's direction summary SHALL tell the operator that the cause can be fixed on the target branch, or committed to the claim's branch, and that commenting on the issue resumes the claim at archive with the target branch merged in.

#### Scenario: Archive after acceptance

- **WHEN** implementation and verification complete
- **THEN** the change is archived and committed, and an `archived` checkpoint is pushed
- **AND** the validator ran before the archive and does not run after it

#### Scenario: Archive repair is blocked

- **WHEN** the archive step stops with a `REPAIR_BLOCKED` declaration
- **THEN** the workflow returns `needs-input` with stopped step `archive` and the agent's explanation
- **AND** the direction summary says that a fix merged to the target branch reaches the claim when it resumes
- **AND** the pushed implemented branch is retained
- **AND** the next attempt resumes at archive

#### Scenario: Unblock archive after a fix lands on the target branch

- **WHEN** a claim stopped at archive because of a problem in a file on the target branch, a fix to that file is merged to the target branch, and a writer comments on the issue
- **THEN** the resumed attempt merges the target branch's current head into the claim's branch and the archive succeeds
- **AND** the attempt verifies the merged change before finalizing

## MODIFIED Requirements

### Requirement: Invoke the versioned feature workflow

The factory SHALL run the packaged `factory-feature` workflow, with its `factory-define` sub-workflow, through the operator's installed Agent Runner, passing the configured feature role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, the attempt's artifact directory, and the attempt's resume point when one exists. The resume point SHALL be the claim's own pushed branch, or, for a new claim continuing a settled prior claim, the prior claim's branch supplied at admission. The factory SHALL ship the feature workflows beside the fix, review, and shared implementation workflows and stage all of them into the Runner catalog the attempt uses. The workflow contract SHALL be versioned as `factory-feature/1`. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the installed Runner does not provide what the workflow requires, including the `core/implement-change` and `core/verify-change` builtin workflows, and SHALL hold the claim and report the problem in status and doctor. The workflow SHALL write exactly one structured outcome to `feature-outcome.json` in the attempt's artifact directory, declaring its contract version: `pull-request` with the pull request reference; `needs-input` with reasons; or `failed` with reasons. Absence of a structured outcome SHALL be treated as a technical failure. A feature attempt SHALL work on a deterministic branch named from the issue and claim. The packaged workflow files are versioned `-v2.0`; the contract and the outcome file are unchanged.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged feature workflow declares a compatible contract version and the installed Runner provides `core/implement-change` and `core/verify-change`
- **THEN** the attempt starts under its own supervisor on the host with the configured feature roles
- **AND** the staged workflow directory holds the feature, define, fix, review, and implementation workflow files

#### Scenario: Launch against a Runner without verify-change

- **WHEN** the installed Runner lacks the `core/implement-change` or the `core/verify-change` builtin workflow
- **THEN** no attempt is recorded, the feature is held, and status and doctor name the missing workflow

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `feature-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy

### Requirement: Define the change autonomously

The feature workflow SHALL create an OpenSpec change for the issue and define it without human interaction, in this order: proposal; an adversarial proposal review by a fresh crosscheck agent; specifications; design; test plan; an approach review of all definition artifacts together by a fresh crosscheck agent; OpenSpec validation with bounded mechanical repair; the planning-artifact check requiring the proposal, specifications, design, and test plan; and a commit of the plan. There SHALL be no `tasks.md`, no task planning, and no task review. When a definition step reaches a decision the issue and eligible comments do not answer, the workflow SHALL decide it and record the decision as an explicit assumption in the relevant artifact, unless the decision is direction-level. A decision is direction-level when it contradicts the issue, when the issue admits materially different readings, when it would break a public interface or persisted data format, or when it requires a decision or change outside the target repository. The lead SHALL apply review findings it judges correct without human confirmation, unless a finding raises a direction-level decision. Each review SHALL run as its own workflow step in a fresh reviewer session using the configured crosscheck role, in a single pass. Every recorded decision, review-finding disposition, and plan revision SHALL be appended to a decision log inside the change directory, which is archived with the change. After the plan commit the workflow SHALL push the branch without opening a pull request. The workflow SHALL also push the branch after implementation and after archival, so that each completed phase survives a technical failure. Each checkpoint commit SHALL identify its phase (`planned`, `implemented`, or `archived`) in the commit itself, so the last completed phase is determined from the pushed branch rather than from local attempt evidence. A checkpoint made after the change's validator run and acceptance SHALL also carry a `Factory-Verified: true` trailer.

#### Scenario: Define a well-described feature

- **WHEN** the issue describes a feature whose open decisions are all below direction level
- **THEN** the workflow produces and commits the proposal, specifications, design, and test plan, each open decision recorded as an assumption
- **AND** it pushes the branch and continues to implementation without opening a pull request

#### Scenario: Apply a review finding

- **WHEN** the approach review reports a consequential gap that does not raise a direction-level decision
- **THEN** the lead revises the affected artifacts and continues without stopping

#### Scenario: Fail validation after repair

- **WHEN** OpenSpec validation still fails after its bounded mechanical repair
- **THEN** the workflow returns `failed` with the validation errors

### Requirement: Resume and continue feature work

A resumed attempt on a blocked claim SHALL check out the claim's branch, bring it up to date with the target branch as `Merge the target branch into the claim's branch on every resume` requires, and continue at the step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target branch's current head, carry the prior committed plan over to its own change name, and continue at implementation with that plan; when the prior branch's last pushed phase is `archived`, the new claim SHALL instead continue after the archive: at verification of the archived change when no checkpoint records the change as verified, and at finalization when one does and the merge added no commits. An attempt resumed after a definition stop, or a new claim continuing a prior claim at implementation, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A new claim continuing at verification SHALL NOT revise the archived artifacts. A technical recovery retry SHALL NOT revise existing artifacts. When neither the prior branch nor the claim's own branch exists, the workflow SHALL start a fresh definition and state in its outcome that it did so. If the prior branch is unavailable but the claim's own branch exists, the workflow SHALL resume from its own branch. A merge conflict SHALL NOT cause a fresh start. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. The last completed phase SHALL be read from the claim's own branch history only, so checkpoints that reach the branch through a merge of the target branch SHALL NOT count as the claim's progress, and a merge SHALL NOT change which step an attempt resumes at. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

When a new claim continuing a prior claim's branch opens its pull request, the factory SHALL close any open pull request on a prior claim's branch for that issue and comment on it with a pointer to the new pull request.

#### Scenario: Close the prior claim's pull request when a continuation opens its own

- **WHEN** a continued feature claim opens a pull request on its own claim branch and a prior claim's branch for the issue has an open pull request
- **THEN** the factory comments on the prior pull request with a link to the new pull request and closes the prior pull request

#### Scenario: Resume after answering a definition question

- **WHEN** a claim stopped during design and a writer answers the question on the issue
- **THEN** the resumed attempt checks out the claim's branch, merges the target branch's current head into it, revises the proposal or specifications where the answer conflicts with them, and continues at design

#### Scenario: Continue a failed feature on a new claim

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after its plan commit
- **THEN** the workflow merges the target branch's current head into that branch and starts at implementation with the prior plan under the new claim's change name
- **AND** it revises the plan first when the current issue or eligible comments warrant it

#### Scenario: Continue a feature whose prior claim failed after archival

- **WHEN** a new claim is supplied with the branch of a prior claim that failed after pushing its `archived` checkpoint
- **THEN** the workflow merges the target branch's current head into that branch and continues under the new claim's change name at verification of the archived change when the checkpoint does not record it as verified, and at finalization when it does and the merge added no commits
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
- **THEN** the recovery retry continues from the pushed branch at finalization when the checkpoint records the change as verified, and at verification when it does not, and does not revise the plan or its artifacts

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

#### Scenario: Recover after a crash inside implementation with a draft pull request

- **WHEN** an attempt fails technically inside the Runner's `implement-change` after verification opened a draft pull request, before any `implemented` checkpoint
- **THEN** the recovery retry resumes at verification: it archives the implemented change and verifies it on its own before finalizing, and does not skip verification

#### Scenario: Resume a plan implemented under the earlier workflow

- **WHEN** a claim's branch holds `implemented` or `archived` checkpoints without the `Factory-Verified` trailer, and a `tasks.md` the Runner's `implement-change` never reads
- **THEN** the attempt archives the change if it is not archived, runs `core/verify-change` on the archived change, pushes a verified checkpoint, and continues to finalization

### Requirement: Verify the change and open a draft pull request

Verification SHALL be the Runner's `core/verify-change` builtin workflow, which `core/implement-change` runs on the implemented change: the validator once with its bounded repair, the clean-tree check, a draft pull request, and acceptance preparation against the test plan. The lead's decision record and the implementor's maintainability pass inside `core/implement-change` replace assumption review and simplify. When an attempt resumes after implementation without a checkpoint that records the change as verified, such as from a draft pull request left by a crash, the workflow SHALL archive the change and run `core/verify-change` on the archived change on its own, with assumption review and simplify. A validator that remains red after its bounded repair SHALL return `failed` with reasons; the branch SHALL be pushed and no pull request opened. When verify fails at a step other than the validator, the `failed` outcome SHALL name that step and its error, report the validator's actual result, and skip pull request annotation.

When the failed verify leaf has a non-empty `repair_blocked` response, its reason SHALL append that response after the error, removing a trailing `REPAIR_BLOCKED` line and keeping the start of the response, cut to 1500 characters with a trailing `…` when longer. A successful end or a new attempt of that step SHALL clear its prior repair response. Missing, malformed, or empty responses SHALL leave the error reason unchanged. When a draft pull request is already open, the failed verify outcome SHALL retain its pull request reference.

#### Scenario: Open a draft pull request

- **WHEN** the validator passes
- **THEN** the workflow pushes the branch, opens a draft pull request, and prepares acceptance evidence against the test plan

#### Scenario: Stay red after validator repair

- **WHEN** the validator remains red after its bounded repair
- **THEN** the workflow pushes the branch, opens no pull request, and returns `failed` with the failing checks

#### Scenario: Acceptance push fails after the validator passes

- **WHEN** the validator passes but verify fails at the acceptance push with a remote rejection
- **THEN** the outcome is `failed`, names the acceptance-push step and rejection, reports `validator.checks` as `passed` and `validator.status` as `incomplete`, preserves the branch, and skips pull request annotation
- **AND** the outcome retains the draft pull request reference when one exists

#### Scenario: Acceptance push repair changes the branch before blocking

- **WHEN** acceptance push fails with a remote rejection and its repair pushes a lint fix, saves the ci.yml change as a patch, and reports `repair_blocked`
- **THEN** the failure reason includes both the rejection and the repair's leading summary without its trailing `REPAIR_BLOCKED` marker

#### Scenario: Validator retry recovers before acceptance push fails

- **WHEN** a validator attempt fails, a later retry passes, and acceptance push fails
- **THEN** the failed outcome names the acceptance-push step and error rather than the recovered validator attempt, and reports validator checks as `passed`

#### Scenario: Verify fails before running the validator

- **WHEN** a verification step before the validator fails, before verify records a validator result
- **THEN** the failed outcome names that step and its error instead of claiming the validator exhausted its repair cycles, and skips pull request annotation

### Requirement: Classify review attention without blocking

After the plan commit the feature workflow SHALL NOT stop for human input. Whether or not acceptance completed, and whether or not assumption review left decision-bearing assumptions, the workflow SHALL continue to finalization. Before finalizing, it SHALL classify every item a reviewer may need to examine into exactly one tier, making items that share one root cause a single item in the highest tier any of them reaches:

- red: an acceptance criterion that failed, or that could not be verified and that no automated test covers, acceptance that did not complete, a validator that stayed red after an acceptance fix, any known deviation from the specifications or from a decision the issue settled, and a resume or continuation that fell back to a fresh start;
- orange: decision-bearing assumptions, which settle a choice the issue left open in a way that changes scope, weakens a guarantee, affects other callers, or is costly to reverse; plan revisions made in response to human comments; and commits added after acceptance ran, including those the finalization loop adds after classification, which acceptance evidence does not cover, where the classification's item for commits it saw states their diff size and whether tests cover them. The archive of the accepted change and the workflow's phase checkpoints follow acceptance on every feature and touch only OpenSpec files, so they are not commits added after acceptance for this purpose;
- yellow: an acceptance criterion that acceptance did not exercise but that a named automated test covers, naming that test; an item that only follows a decision the issue settled or an acceptance criterion, which SHALL NOT be ranked higher; and every other recorded assumption or decision;
- white: acceptance criteria that passed, with their evidence.

Related orange assumptions SHALL be grouped into one item per topic that cites the decisions behind it, and orange items SHALL be ordered most important first. Each item SHALL cite the committed file, and the line or range within it where one applies, or the evidence that shows it. The classification SHALL be recorded in the attempt's evidence and used by `factory-feature-reporting`. After finalization, the workflow SHALL add an orange item for any commits made after acceptance that the classification did not cover, and the tier counts in the outcome SHALL be those of the final classification.

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

#### Scenario: The archive and checkpoints follow acceptance

- **WHEN** the only commits after the accepted head are the archive of the change and the workflow's phase checkpoints
- **THEN** the classification has no orange item for commits after acceptance, and the workflow's classification check does not ask for one

### Requirement: Merge the target branch into the claim's branch on every resume

Every feature attempt that resumes or continues from an existing branch SHALL merge the current head of the claim's target branch into the claim's branch before the step it resumes at runs. This covers a resume after a `needs-input` stop, a resume from an open draft pull request, a technical recovery retry, and a new claim continuing a prior claim's branch. A claim's first attempt that starts fresh from the target SHALL NOT need a merge. The factory SHALL fetch the target repository and resolve the target branch's head when it prepares the attempt, rather than reuse the commit recorded at admission. The claim's recorded target, Agent Runner, and Agent Skills revisions SHALL remain those frozen at admission. The attempt's provenance SHALL record both the target commit recorded at admission and the target-branch head merged by this attempt.

A merge that completes without conflicts SHALL be committed on the claim's branch as a merge commit that is not a phase checkpoint. When the target branch's head is already contained in the claim's branch, the attempt SHALL add no commit. When the merge conflicts, an agent step SHALL resolve the conflicts before the resumed step runs, preserving the intent of both the claim's changes and the target branch's changes. It SHALL commit the resolution and SHALL NOT run the validator, because the attempt's single validator run covers the merged tree. The resolution SHALL keep the claim's pre-merge history and the target branch's head as its parents, and SHALL change by hand only the files that conflicted. A resolution that drops the claim's commits, or changes the claim's or the target branch's non-conflicting files away from the automatic merge result, SHALL fail the attempt as a technical failure rather than be pushed. When the agent cannot resolve the conflicts confidently, the attempt SHALL return `needs-input` listing the conflicting files and the decision it needs. Its stopped step SHALL be the step the attempt was going to resume at. It SHALL NOT commit or push a partially merged tree, and it SHALL leave the claim's pushed branch holding the work it held before the merge. On a new claim continuing a prior branch whose own branch was never pushed, the claim's branch SHALL be pushed with the prior branch's work unchanged, so the next attempt resumes from it rather than starting fresh. The next attempt SHALL merge the target branch's then-current head again, with the writer's answer among the eligible comments, and continue at the recorded step.

When the merge added commits to the claim's branch, the attempt SHALL NOT rely on validation results from before the merge. An attempt that would otherwise skip verification SHALL run verification before finalizing the pull request. A resume at archive that merged commits verifies after the archive; attempts that resume before archive verify inside implementation.

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
- **THEN** the attempt commits a merge that resolves every conflict and then continues at its resume point, where the attempt's validator run covers the merged tree
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

### Requirement: Qualify the validator status in the feature outcome

The `feature-outcome.json` the workflow writes SHALL NOT report a validator pass for a change whose verification did not complete. For every outcome that carries a validator status, `validator.checks` SHALL hold the status of the validator run itself, and `validator.status` SHALL be:

- `passed` when verification completed and the validator passed;
- `incomplete` when the validator passed but a later verification step failed, such as the acceptance push;
- `failed` when the validator failed, as before.

The outcome SHALL carry no `task_compliance` object. The contract version SHALL remain `factory-feature/1`, and outcomes written before verification runs, such as definition stops, SHALL keep their current shape.

#### Scenario: The validator passes and verification completes

- **WHEN** an attempt returns `pull-request` after the validator passed and verification completed
- **THEN** the outcome's `validator.status` and `validator.checks` are both `passed` and the outcome has no `task_compliance`

#### Scenario: A later verification step fails after the validator passed

- **WHEN** the validator passed but the acceptance push was rejected
- **THEN** the outcome is `failed`, `validator.checks` is `passed`, and `validator.status` is `incomplete`

### Requirement: Validator repair reports out-of-scope CHECK failures

The requirement covers the implementation constraints the workflow appends to the change's design, which bind the Runner's implementation, audit-resolution, and validator-repair steps, and the review rounds' implementation. Validator repair SHALL be instructed not to remediate a CHECK failure when the same check fails with the same error at the merge base with the target branch (`origin/<target branch>`, never a local branch), or when its only remedy replaces or redirects a dependency source. Each condition SHALL apply independently. Repair SHALL confirm a pre-existing failure by running the check on a temporary worktree of that merge base; for a dependency audit, unchanged lockfile entries for the flagged package and unchanged check definition and configuration suffice. A newly published advisory that also fails at the merge base counts as pre-existing. An unconfirmed failure SHALL be treated as branch-caused and fixed, even if the failing file was untouched. A branch change to the check or its policy SHALL count as branch-caused. For an out-of-scope failure, repair SHALL leave the check failing and report the failure, why it is out of scope, and the proposed remedy for a human decision.

#### Scenario: Unrelated advisory requires an unvetted dependency source

- **WHEN** a dependency audit fails on a newly published advisory affecting an unchanged dependency and the available remedy redirects it to an unvetted fork
- **THEN** validator repair leaves the check failing, commits no override, and reports the failure and proposed remedy for a human decision

#### Scenario: Branch-caused failure requires an unvetted dependency source

- **WHEN** a CHECK failure is caused by this branch but the only available remedy redirects a dependency to an unvetted source
- **THEN** validator repair leaves the check failing, commits no override, and reports the proposed remedy for a human decision

#### Scenario: Indirect regression in an untouched file

- **WHEN** the branch changes a function signature and a check fails in an untouched file with unchanged configuration and no such error at the merge base
- **THEN** validator repair treats the failure as branch-caused and fixes it

## REMOVED Requirements

### Requirement: Implement the change as one task
**Reason**: Change v3 plans no tasks. The Runner's `implement-change` implements the whole change against a checklist and replaces the single task.
**Migration**: See "Implement the change with the Runner's implement-change".

### Requirement: Archive the change before verification
**Reason**: The Runner's `implement-change` verifies inside itself, and change v3 archives after verification and acceptance.
**Migration**: See "Archive the change after verification".

### Requirement: Gate the feature on a bound task-compliance verdict
**Reason**: Change v3 plans no tasks, so there is no `tasks.md` to bind a verdict to, and task-compliance is opt-in in repository Validator configurations rather than part of the main workflow.
**Migration**: The change's single validator run in `core/verify-change` is the review; a repository that wants task-compliance enables it in its own configuration.
