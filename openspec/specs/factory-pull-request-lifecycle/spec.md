# factory-pull-request-lifecycle Specification

## Purpose
TBD - created by archiving change factory-feature-support. Update Purpose after archive.
## Requirements
### Requirement: Detect eligible review comments on a settled pull-request claim

For each pull-request claim, fix, feature, or task, that is settled, or blocked by a review round's `needs-input`, whose recorded factory pull request is open, the factory SHALL read on each successful poll the pull request's reviews, inline review threads (with their resolution state), and conversation comments. A comment is eligible when its author has write, maintain, or admin access to the repository, it is not authored by the factory's own identity, it has a non-empty body, and it is newer than the claim's review checkpoint. A review with state `APPROVED` and an empty body SHALL NOT be eligible. Comments in resolved threads SHALL be read but SHALL NOT be eligible. The review checkpoint SHALL be initialised, when absent, to the recorded completion time of the claim's latest attempt, SHALL be advanced to the reservation time of each review attempt so the same comment never launches two rounds, and SHALL be advanced to the decline time when a review attempt returns `needs-input`. A claim whose pull request is merged or closed SHALL NOT be scanned.

#### Scenario: Reviewer requests a change

- **WHEN** a writer submits a review with state `CHANGES_REQUESTED` and an inline comment on a factory PR whose fix, feature, or task claim is settled in Review
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

### Requirement: Re-admit a review round through the claim's kind slot

An eligible review round SHALL be admitted through the execution slot, window, and limits of the claim's own work kind: a fix claim's round through the fix slot and fix window, a feature claim's round through the feature slot and feature window, and a task claim's round through the task slot and task window. A review round SHALL be a new start in the lane of the card's current Priority (see `factory-claim-lifecycle`). It SHALL be admitted only when all of these hold: the factory is not paused; that lane is free; no attempt of the claim's kind occupies a higher lane; that window is open; memory headroom is available; and no applicable quota or readiness hold is active. Within a kind, the factory SHALL consider review rounds, blocked-claim resumes, and new Ready work in Priority order. A review round SHALL be considered before new Ready work of the same kind and the same Priority in the same cycle. An attempt reserved earlier in the same cycle SHALL count as occupying its lane. A review round SHALL NOT compete with work of another kind. Admission SHALL reconcile side effects, verify the recorded PR is still open and read its head commit, cut fresh clones with the target checked out on the PR branch at that head, reserve a run with reason `review` and the claim's recorded Runner and Skills commits, set the claim lifecycle to active, and comment on the issue that a review round started, naming the comments it will address. When the recorded PR is no longer open at admission, the factory SHALL record why and SHALL NOT launch.

#### Scenario: Review round beats a new bug

- **WHEN** the fix lanes are free, a settled Medium fix claim has an eligible review comment, and another Medium bug sits in Ready
- **THEN** the review round is admitted first and the new bug waits for the Medium fix lane

#### Scenario: Feature review round uses the feature slot

- **WHEN** a settled feature claim has an eligible review comment, its feature lane and every higher feature lane are free, and a fix attempt holds a fix lane
- **THEN** the feature review round is admitted through its feature lane under the feature limits
- **AND** it is admitted before any new Ready feature of the same Priority in the same cycle

#### Scenario: PR closed before admission

- **WHEN** the reviewer closed the PR before the poll admits the round
- **THEN** no attempt starts, the checkpoint is not advanced, and the reason is recorded on the claim

#### Scenario: Slot busy

- **WHEN** another attempt of the same kind holds the review round's lane, or a higher lane of that kind
- **THEN** the review round waits and is admitted on a later poll while its comments remain eligible

#### Scenario: Task review round uses the task slot

- **WHEN** a settled task claim has an eligible review comment, its task lane and every higher task lane are free, and a fix attempt holds a fix lane
- **THEN** the task review round is admitted through its task lane under the task limits
- **AND** it is admitted before any new Ready task of the same Priority in the same cycle

#### Scenario: A higher Ready bug beats a lower review round

- **WHEN** the fix lanes are free, a settled Low fix claim has an eligible review comment, and an eligible High bug sits in Ready in the same cycle
- **THEN** the High bug is admitted, and the Low review round waits until no fix attempt occupies a higher lane

#### Scenario: A higher review round starts beside a lower fix

- **WHEN** a Low fix attempt is running and a settled High fix claim has an eligible review comment
- **THEN** the High review round is admitted into the High fix lane, and the Low attempt continues

### Requirement: Sync the working clone and close the issue after merge

For a settled pull-request claim, fix, feature, or task, with a recorded factory PR that has been merged and whose sync has not completed, regardless of the card's current column, the factory SHALL update the operator's configured working clone of that repository on the next successful poll: verify the working tree and index have no changes to tracked files, fetch `main` from the remote into the local `main` branch (refusing when local `main` has diverged or is checked out in any worktree of that clone), verify by dry run that merging `main` into the currently checked-out branch produces no conflicts, and then perform that merge. On success it SHALL close the issue when it is still open so closure automation moves the card to Done. On tracked changes, a diverged local `main`, `main` checked out elsewhere, a predicted conflict, a detached HEAD, or an unreachable clone, it SHALL leave the card where it is, apply the `needs-input` label when the card is not yet Done, comment with the reason, and retry on later polls, clearing the label when the sync succeeds. Apart from fetched refs and objects, it SHALL NOT change the clone's working tree, index, or checked-out branch except by the merge itself. A merged PR whose issue a human already closed SHALL still receive its one sync attempt sequence.

#### Scenario: Merge a fix or feature while the clone is on dev

- **WHEN** a factory PR for a fix or feature claim merges while the working clone has branch `dev` checked out and a clean tree
- **THEN** the factory updates local `main`, merges it into `dev`, closes the issue, and the card moves to Done

#### Scenario: Merge a task while the clone is on dev

- **WHEN** a factory PR for a task claim merges while the working clone has branch `dev` checked out and a clean tree
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

