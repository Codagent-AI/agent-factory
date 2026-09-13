# factory-bug-intake Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
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

### Requirement: Recognize fix retry gestures

A fix claim that ended with a pull request, a failed outcome, or exhausted recovery is settled. Moving a settled fix card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and PR history; the new attempt's input SHALL include the issue's eligible comments and any prior factory PR. A blocked fix claim SHALL be re-admitted when an eligible comment newer than the decline appears on the issue, or when a human moves its card from Running to Ready; in either case the factory SHALL remove the `needs-input` label and start a new attempt whose input includes the eligible comments present at that time. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission. The eval fresh-request gesture is unchanged and separate.

#### Scenario: Drag a settled fix back to Ready

- **WHEN** a human moves a fix card whose claim is settled from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits, keeps the earlier claim history, and does not treat the drag as a contradictory status edit

#### Scenario: Answer a blocked bug

- **WHEN** a writer comments on a blocked bug after the decline comment
- **THEN** the next poll removes the `needs-input` label and admits a new attempt when the fix slot and holds permit
- **AND** the new attempt's input includes the writer's comment

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

### Requirement: Reconcile side effects before launching

Before launching any attempt for a bug, including a recovery retry or a fresh claim, the factory SHALL check the target repository for an existing factory branch for that issue and an open factory pull request referencing it. An existing open factory PR SHALL settle the claim as handed off rather than launch a duplicate. When the factory cannot establish whether a prior attempt pushed a branch or opened a PR, it SHALL hold the claim, report the ambiguity on the issue and in status, and SHALL NOT launch.

#### Scenario: Find a PR from a crashed attempt

- **WHEN** an attempt opened a PR but failed before its outcome was recorded
- **THEN** reconciliation finds the open factory PR for the issue and reports it as the attempt's result without launching the retry

#### Scenario: Fail to reach GitHub before relaunch

- **WHEN** the reconciliation lookup fails
- **THEN** the factory does not launch and retries reconciliation on a later poll

