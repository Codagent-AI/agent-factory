# factory-review-intake Specification

## Purpose
TBD - created by archiving change code-review. Update Purpose after archive.
## Requirements
### Requirement: Detect eligible review comments on a settled fix claim

For each fix claim that is settled, or blocked by a review round's `needs-input`, whose recorded factory pull request is open, the factory SHALL read on each successful poll the pull request's reviews, inline review threads (with their resolution state), and conversation comments. A comment is eligible when its author has write, maintain, or admin access to the repository, it is not authored by the factory's own identity, it has a non-empty body, and it is newer than the claim's review checkpoint. A review with state `APPROVED` and an empty body SHALL NOT be eligible. Comments in resolved threads SHALL be read but SHALL NOT be eligible. The review checkpoint SHALL be initialised, when absent, to the recorded completion time of the claim's latest attempt, SHALL be advanced to the reservation time of each review attempt so the same comment never launches two rounds, and SHALL be advanced to the decline time when a review attempt returns `needs-input`. A claim whose pull request is merged or closed SHALL NOT be scanned.

#### Scenario: Reviewer requests a change

- **WHEN** a writer submits a review with state `CHANGES_REQUESTED` and an inline comment on a factory PR whose claim is settled in Review
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

### Requirement: Re-admit a review round through the fix slot

An eligible review round SHALL be admitted only when the factory is not paused, the fix slot is free, the fix window is open, memory headroom is available, and no applicable quota or readiness hold is active. Review rounds SHALL be considered before new Ready bugs in the same cycle. Admission SHALL reconcile side effects, verify the recorded PR is still open and read its head commit, cut fresh clones with the target checked out on the PR branch at that head, reserve a run with reason `review` and the claim's recorded Runner and Skills commits, set the claim lifecycle to active, and comment on the issue that a review round started, naming the comments it will address. When the recorded PR is no longer open at admission, the factory SHALL record why and SHALL NOT launch.

#### Scenario: Review round beats a new bug

- **WHEN** the fix slot is free, a settled claim has an eligible review comment, and another bug sits in Ready
- **THEN** the review round is admitted first and the new bug waits for the next free slot

#### Scenario: PR closed before admission

- **WHEN** the reviewer closed the PR before the poll admits the round
- **THEN** no attempt starts, the checkpoint is not advanced, and the reason is recorded on the claim

#### Scenario: Slot busy

- **WHEN** another fix attempt holds the slot
- **THEN** the review round waits and is admitted on a later poll while its comments remain eligible

