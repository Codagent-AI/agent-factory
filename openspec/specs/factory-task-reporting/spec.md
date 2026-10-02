# factory-task-reporting Specification

## Purpose
TBD - created by archiving change feature-76-691215c4. Update Purpose after archive.
## Requirements
### Requirement: Comment on task activity

The factory SHALL comment on the issue at each of these events:

- it admits a task attempt, with the resolved refs and the attempt number;
- it admits a review round, naming the pull request and the comments the round will address;
- an attempt is declined, fails, is retried, is cancelled, or produces a pull request;
- a review round completes, linking the pull request and summarizing what was changed and answered;
- a post-merge sync succeeds or is blocked.

The comment reporting a task attempt's `needs-input`, whether triage declined or the pre-push boundary check stopped the attempt, SHALL list the reasons and the decision needed, including any route to a Bug or Feature, and SHALL state that no branch was pushed. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a task claim's inputs are accepted SHALL read `Task inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a task

- **WHEN** the factory admits a task
- **THEN** the issue receives one comment naming the target, Runner, and Skills commits and the attempt number

#### Scenario: Report a triage decline

- **WHEN** a task attempt returns `needs-input` from triage
- **THEN** the issue receives one comment listing the reasons and the decision needed, and stating that no branch was pushed

#### Scenario: Report a task pull request

- **WHEN** a task attempt returns `pull-request`
- **THEN** the issue receives one comment linking the `chore:` pull request and stating that the attempt ran on the host

#### Scenario: Accept a task claim's inputs

- **WHEN** the factory accepts and freezes a task claim's inputs
- **THEN** the acceptance comment reads `Task inputs accepted and frozen.`

### Requirement: Map task outcomes to the board

The factory SHALL map task outcomes to the board as follows:

- On `pull-request`, it SHALL link the pull request in a comment, move the card to Review, and set `Verdict=pending-human-review`.
- On `needs-input` from a task attempt, it SHALL post the reasons, apply the red `needs-input` label, leave the card in Running, and release the task slot.
- On `needs-input` from a review attempt, it SHALL post the reasons on the issue, apply the `needs-input` label, return the card to Review, restore the verdict the card held when the round was admitted, and release the task slot.
- On `failed`, it SHALL post the reasons, link any pull request, move the card to Review, and set `Verdict=failed`.
- On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation.

While a review attempt runs, the card SHALL be in Running with its verdict cleared. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a task pull request

- **WHEN** a task attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the pull request link is on the issue

#### Scenario: Park a declined task

- **WHEN** a task attempt returns `needs-input` from triage
- **THEN** the card stays in Running with the `needs-input` label and the reasons are on the issue
- **AND** another eligible task can be admitted to the task slot

#### Scenario: Park a review round that left task scope

- **WHEN** a review round on a task pull request returns `needs-input` because a requested change is outside task scope
- **THEN** the card returns to Review with the `needs-input` label, the reasons and route are on the issue, and the pull request stays open

#### Scenario: Report a failed task

- **WHEN** a task attempt returns `failed` with an open pull request
- **THEN** the card moves to Review with `failed`, the reasons and pull request link are on the issue, and the pull request remains open

### Requirement: Deliver task reports durably without duplicates

Task reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as fix reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a pull request comment response

- **WHEN** GitHub accepts the comment linking the task pull request but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment

