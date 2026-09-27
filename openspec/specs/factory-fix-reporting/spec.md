# factory-fix-reporting Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
### Requirement: Comment on fix activity

The factory SHALL comment on the issue when it admits a bug (including the resolved refs and attempt number), when it admits a review round (naming the PR and the comments it will address), when an attempt is declined, fails, is retried, is cancelled, or produces a PR, when a review round completes (linking the PR and summarizing what was changed and answered), and when a post-merge sync succeeds or is blocked. Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state. When the attempt being reported ran in host mode, the comment reporting its outcome (`pull-request`, `needs-input`, `failed`, or exhausted recovery) SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a fix claim's inputs are accepted SHALL read `Fix inputs accepted and frozen.` rather than the eval wording, and SHALL keep its existing stable marker; the admission comment's content SHALL otherwise be unchanged.

#### Scenario: Admit a bug

- **WHEN** the factory admits a bug
- **THEN** the issue receives one comment naming the target, Runner, and Skills commits and the attempt number

#### Scenario: Complete a review round

- **WHEN** a review attempt returns `pull-request`
- **THEN** the issue receives one comment linking the PR and listing what was changed and what was answered

#### Scenario: Report a host-mode PR

- **WHEN** a host-mode attempt returns `pull-request`
- **THEN** the PR comment links the PR and states that the attempt ran on the host with the operator's installed Runner and Skills rather than the recorded commits

#### Scenario: Report a Docker-mode outcome

- **WHEN** a Docker-mode attempt returns any outcome
- **THEN** its outcome comment carries no host note

#### Scenario: Accept a fix claim's inputs

- **WHEN** the factory accepts and freezes a bug fix claim's inputs
- **THEN** the acceptance comment reads `Fix inputs accepted and frozen.` and never `Evaluation inputs accepted and frozen.`
- **AND** an eval claim's acceptance comment still reads `Evaluation inputs accepted and frozen.`

### Requirement: Map fix outcomes to the board

On `pull-request`, the factory SHALL link the PR in a comment, move the card to Review, and set `Verdict=pending-human-review`. On `needs-input` from a fix attempt, it SHALL post the agent's reasons, apply the red `needs-input` label, leave the card in Running, and release the fix slot. On `needs-input` from a review attempt, it SHALL post the reasons on the issue, apply the `needs-input` label, return the card to Review, restore the verdict it held when the round was admitted, and release the fix slot. On `failed`, it SHALL post the reasons, link any PR, move the card to Review, and set `Verdict=failed`. On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation. While a review attempt runs, the card SHALL be in Running with its verdict cleared. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a PR

- **WHEN** an attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the PR link is on the issue

#### Scenario: Park a declined bug

- **WHEN** a fix attempt returns `needs-input`
- **THEN** the card stays in Running with the `needs-input` label and the reasons are on the issue
- **AND** another eligible bug can be admitted to the fix slot

#### Scenario: Park a declined review round

- **WHEN** a review attempt returns `needs-input`
- **THEN** the card returns to Review with the `needs-input` label, the reasons are on the issue, and the PR stays open

#### Scenario: Report a failed fix

- **WHEN** an attempt returns `failed` with an open PR
- **THEN** the card moves to Review with `failed`, the reasons and PR link are on the issue, and the PR remains open

#### Scenario: Run a review round

- **WHEN** a review attempt is running
- **THEN** the card is in Running and returns to Review with `pending-human-review` when the attempt returns `pull-request`

### Requirement: Deliver fix reports durably without duplicates

Fix reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as eval reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a PR comment response

- **WHEN** GitHub accepts the PR-link comment but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment

