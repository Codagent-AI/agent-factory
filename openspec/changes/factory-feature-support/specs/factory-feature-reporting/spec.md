## ADDED Requirements

### Requirement: Comment on feature activity

The factory SHALL comment on the issue when it admits a feature attempt, when it admits a review round (naming the pull request and the comments it will address), when an attempt stops with `needs-input`, fails, is retried, is cancelled, or produces a pull request, and when a review round completes (linking the pull request and summarizing what was changed and answered). The admission comment SHALL include the resolved refs and attempt number and SHALL state whether the attempt starts fresh, resumes at a named step, or continues a prior claim's branch, and SHALL state when a resume point was unavailable and the attempt started fresh. The comment reporting a `needs-input` stop SHALL list the specific questions, summarize the direction drafted so far, and link the pushed branch; for a `preflight` stop it SHALL state that no branch was created and that the next attempt starts fresh. The comment linking a produced pull request SHALL state the number of red, orange, and yellow items of the final classification defined by `factory-feature-execution`, matching the pull request description. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a feature claim's inputs are accepted SHALL read `Feature inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a resumed attempt

- **WHEN** the factory admits a resumed attempt for a claim that stopped during design
- **THEN** the issue receives one comment naming the refs, the attempt number, and that the attempt resumes at design

#### Scenario: Report a definition stop

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the issue receives one comment listing the questions, summarizing the drafted direction, and linking the pushed branch

#### Scenario: Report flag counts with the pull request

- **WHEN** a feature attempt returns `pull-request` with two red items, three orange items, and twelve yellow items
- **THEN** the comment linking the pull request states two red flags, three orange flags, and twelve yellow items

#### Scenario: Report a fresh start after an unavailable resume point

- **WHEN** a continuing claim's prior branch no longer exists
- **THEN** the outcome comment states that the prior branch was unavailable and the attempt started fresh

#### Scenario: Accept a feature claim's inputs

- **WHEN** the factory accepts and freezes a feature claim's inputs
- **THEN** the acceptance comment reads `Feature inputs accepted and frozen.`

### Requirement: Map feature outcomes to the board

On `pull-request`, the factory SHALL link the pull request in a comment, move the card to Review, and set `Verdict=pending-human-review`. On `needs-input` from a feature attempt's definition, it SHALL post the reasons, apply the `needs-input` label, leave the card in Running, and release the feature slot. On `needs-input` from a review attempt, it SHALL post the reasons on the issue, apply the `needs-input` label, return the card to Review, restore the verdict it held when the round was admitted, and release the feature slot. On `failed`, it SHALL post the reasons, link the pushed branch or open pull request, move the card to Review, and set `Verdict=failed`. On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation. While a review attempt runs, the card SHALL be in Running with its verdict cleared. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a feature pull request

- **WHEN** a feature attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the pull request link is on the issue

#### Scenario: Park a feature stopped during definition

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the card stays in Running with the `needs-input` label, and the reasons and pushed branch link are on the issue
- **AND** another eligible feature can be admitted to the feature slot

#### Scenario: Report a failed feature without a pull request

- **WHEN** a feature attempt returns `failed` after the validator stayed red
- **THEN** the card moves to Review with `failed`, and the reasons and the pushed branch link are on the issue

### Requirement: Annotate the feature pull request by review attention

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a "Review first" section that lists every red item, then every orange item, then every yellow item, one line each, from the classification defined by `factory-feature-execution`, each linking to its detail. Red, orange, and yellow SHALL be visually distinct and SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference without a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, the full assumptions ledger with each assumption's resolution from assumption review, and the acceptance evidence, with white items and the full ledger collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits from the finalization loop exist, SHALL list them. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange and yellow item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the "Review first" section states that the red and orange tiers are empty and lists the yellow assumptions
- **AND** the passed criteria and their evidence are collapsed below it

#### Scenario: Repair CI after acceptance

- **WHEN** the finalization loop pushes commits to fix CI after acceptance ran
- **THEN** those commits appear as an orange item and the evidence names the accepted commit and lists the later commits
- **AND** the orange count in the description and in the issue comment includes that item

#### Scenario: Complete a review round on a feature

- **WHEN** a review round on a feature pull request completes
- **THEN** the acceptance evidence still names the commit it describes and the completion comment states that acceptance was not re-run

### Requirement: Deliver feature reports durably without duplicates

Feature reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as eval and fix reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a stop comment response

- **WHEN** GitHub accepts the `needs-input` comment but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment
