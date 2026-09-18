## MODIFIED Requirements

### Requirement: Recognize fix retry gestures

A fix claim that ended with a pull request, a failed outcome, or exhausted recovery is settled. Moving a settled fix card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and PR history; the new attempt's input SHALL include the issue's eligible comments and any prior factory PR. A settled fix claim whose factory pull request is open SHALL additionally be re-admitted for a review round when an eligible PR-side comment newer than its review checkpoint appears, as defined by `factory-review-intake`; issue comments on a settled claim SHALL NOT trigger a round. A blocked fix claim SHALL be re-admitted when an eligible comment newer than the decline appears on the issue, or when a human moves its card from Running to Ready; a claim blocked by a review round's `needs-input` SHALL instead be re-admitted by an eligible PR-side comment newer than the decline. In each of these cases the factory SHALL remove the `needs-input` label and start a new attempt whose input includes the eligible comments present at that time. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission. The eval fresh-request gesture is unchanged and separate.

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
