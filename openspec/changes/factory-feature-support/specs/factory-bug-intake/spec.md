## RENAMED Requirements

- FROM: `### Requirement: Select eligible bugs in board order`
- TO: `### Requirement: Select eligible bugs by Priority`

## MODIFIED Requirements

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
