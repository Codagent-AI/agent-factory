## MODIFIED Requirements

### Requirement: Resume and continue feature work

A resumed attempt on a blocked claim SHALL check out the claim's branch, bring it up to date with the target branch as `Merge the target branch into the claim's branch on every resume` requires, and continue at the step that stopped. A new claim supplied with a prior claim's branch SHALL start its branch from the prior branch, merge in the target branch's current head, carry the prior committed plan over to its own change name, and continue at implementation with that plan; when the prior branch's last pushed phase is `archived`, the new claim SHALL instead continue at verification of the archived change. An attempt resumed after a definition stop, or a new claim continuing a prior claim at implementation, SHALL first check the existing artifacts against the current issue and the eligible comments and revise them where that input warrants, recording each revision; because such an attempt resumes at or before implementation, every revision is implemented, archived, and verified. A new claim continuing at verification SHALL NOT revise the archived artifacts. A technical recovery retry SHALL NOT revise existing artifacts. When the branch to resume or continue from no longer exists, the workflow SHALL start a fresh definition and state in its outcome that it did so. A merge conflict SHALL NOT cause a fresh start. A technical failure SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation, which continues after the last completed phase found on the claim's pushed branch, and from its draft pull request when one is open; a phase whose checkpoint commit was not pushed SHALL be redone. The last completed phase SHALL be read from the claim's own branch history only, so checkpoints that reach the branch through a merge of the target branch SHALL NOT count as the claim's progress, and a merge SHALL NOT change which step an attempt resumes at. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

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

## ADDED Requirements

### Requirement: Merge the target branch into the claim's branch on every resume

Every feature attempt that resumes or continues from an existing branch SHALL merge the current head of the claim's target branch into the claim's branch before the step it resumes at runs. This covers a resume after a `needs-input` stop, a resume from an open draft pull request, a technical recovery retry, and a new claim continuing a prior claim's branch. A claim's first attempt that starts fresh from the target SHALL NOT need a merge. The factory SHALL fetch the target repository and resolve the target branch's head when it prepares the attempt, rather than reuse the commit recorded at admission. The claim's recorded target, Agent Runner, and Agent Skills revisions SHALL remain those frozen at admission. The attempt's provenance SHALL record both the target commit recorded at admission and the target-branch head merged by this attempt.

A merge that completes without conflicts SHALL be committed on the claim's branch as a merge commit that is not a phase checkpoint. When the target branch's head is already contained in the claim's branch, the attempt SHALL add no commit. When the merge conflicts, an agent step SHALL resolve the conflicts before the resumed step runs, preserving the intent of both the claim's changes and the target branch's changes. It SHALL run the repository's validator on the result and commit the resolution. The resolution SHALL keep the claim's pre-merge history and the target branch's head as its parents, and SHALL change by hand only the files that conflicted. A resolution that drops the claim's commits, or changes the claim's or the target branch's non-conflicting files away from the automatic merge result, SHALL fail the attempt as a technical failure rather than be pushed. When the agent cannot resolve the conflicts confidently, the attempt SHALL return `needs-input` listing the conflicting files and the decision it needs. Its stopped step SHALL be the step the attempt was going to resume at. It SHALL NOT commit or push a partially merged tree, and it SHALL leave the claim's pushed branch holding the work it held before the merge. On a new claim continuing a prior branch whose own branch was never pushed, the claim's branch SHALL be pushed with the prior branch's work unchanged, so the next attempt resumes from it rather than starting fresh. The next attempt SHALL merge the target branch's then-current head again, with the writer's answer among the eligible comments, and continue at the recorded step.

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
