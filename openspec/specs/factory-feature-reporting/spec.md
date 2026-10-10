# factory-feature-reporting Specification

## Purpose
TBD - created by archiving change factory-feature-support. Update Purpose after archive.
## Requirements
### Requirement: Comment on feature activity

The factory SHALL comment on the issue when it admits a feature attempt, when it admits a review round (naming the pull request and the comments it will address), when an attempt stops with `needs-input`, fails, is retried, is cancelled, or produces a pull request, and when a review round completes (linking the pull request and summarizing what was changed and answered). The admission comment SHALL include the resolved refs and attempt number and SHALL state whether the attempt starts fresh, resumes at a named step, or continues a prior claim's branch, and SHALL state when a resume point was unavailable and the attempt started fresh. The comment reporting a `needs-input` stop SHALL list the specific questions, summarize the direction drafted so far, and link the pushed branch; for a `preflight` stop it SHALL state that no branch was created and that the next attempt starts fresh. For a `needs-input` stop recorded because a step's repair was blocked, the comment SHALL show the agent's explanation as its question and a direction summary that names the blocked step and the step the next attempt resumes at. When that stop published no work on the claim's branch, the comment SHALL state that no branch was published and that the next attempt starts fresh, instead of linking a branch. The comment linking a produced pull request SHALL state the number of red, orange, and yellow items of the final classification defined by `factory-feature-execution`, matching the pull request description. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a feature claim's inputs are accepted SHALL read `Feature inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a resumed attempt

- **WHEN** the factory admits a resumed attempt for a claim that stopped during design
- **THEN** the issue receives one comment naming the refs, the attempt number, and that the attempt resumes at design

#### Scenario: Report a definition stop

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the issue receives one comment listing the questions, summarizing the drafted direction, and linking the pushed branch

#### Scenario: Report a blocked repair

- **WHEN** a feature attempt records `needs-input` because the repair of a check inside `implement` was blocked
- **THEN** the issue receives one comment with the agent's explanation, a direction summary naming the blocked check inside `implement` and the step the next attempt resumes at, and a link to the pushed branch

#### Scenario: Report a blocked repair with nothing published

- **WHEN** a feature attempt records `needs-input` with no resume point because the repair of a check inside definition was blocked before anything was pushed
- **THEN** the issue receives one comment with the agent's explanation, stating that no branch was published and that the next attempt starts fresh

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

The feature pull request's description SHALL be maintained in place rather than as comments and SHALL open with a line naming the issue number and title, followed by a "Review first" section that lists every red item, then the orange items described below, one line each, from the classification defined by `factory-feature-execution`. The orange items shown SHALL be every orange item that names a commit added after acceptance, followed by the classification's most important other orange items in its order, up to five orange items in total; the orange heading SHALL state the total orange count, and, when any orange or yellow items are collapsed, the section SHALL state how many further orange items and how many yellow items are collapsed below the change summary. Each item SHALL link to its detail: a file committed on the feature branch links to that file on GitHub, at the cited line or range where the classification cites one, and detail that exists only in the attempt's evidence links to the acceptance evidence in the description, never to a path on the host. Red, orange, and yellow SHALL be visually distinct; red and orange SHALL each be shown even when empty, stating that the tier has no items. Below that section the description SHALL carry the issue reference exactly once with a closing keyword, the claim marker, a short summary of the change with links to the archived proposal, specifications, design, and test plan, the orange items beyond the first five together with every yellow item, the change's decision log together with the assumptions that assumption review left unresolved, and the acceptance evidence, with those orange and yellow items, white items, the decision log, and the unresolved assumptions collapsed by default. The acceptance evidence SHALL name the commit acceptance ran against and, when later commits exist, SHALL list every commit on the branch after it. Each red, orange, or yellow item whose linked file a later commit changed SHALL say it may be fixed by that commit. A review round SHALL NOT re-run acceptance; the evidence SHALL keep naming the commit it describes, and the round's completion comment SHALL state that acceptance was not re-run. A review round SHALL keep the description the round started with, restoring it when the round's finalization rewrote it, and SHALL then bring it up to date with the round: a section before the change summary lists the round's commits and, when the round's changes passed validation and were pushed, the feedback they addressed by source and id without the triage-time reply text, or otherwise states that the commits were not pushed, and the later commits and the items they may have fixed are refreshed as above. When the round pushes a merge of the target branch, it is listed with the round's commits, and the round's completion comment SHALL also name that merge commit as added after acceptance and not covered by the acceptance evidence, linking the commit and the acceptance evidence in the description.

#### Scenario: Review a pull request with red flags

- **WHEN** a feature attempt returns `pull-request` after an acceptance criterion could not be verified
- **THEN** the "Review first" section lists that criterion as red above every orange item

#### Scenario: Review a clean pull request

- **WHEN** a feature attempt returns `pull-request` with every criterion passed and no decision-bearing assumption
- **THEN** the description opens with the issue number and title, and the "Review first" section states that the red and orange tiers are empty and how many yellow items are collapsed
- **AND** the yellow assumptions, the passed criteria, and their evidence are collapsed below it

#### Scenario: Review a pull request with many orange items

- **WHEN** a feature attempt returns `pull-request` with seven orange items, one of them for commits added after acceptance, and two yellow items
- **THEN** the "Review first" section shows the commits after acceptance and the four most important other orange items, gives the orange count as seven, and states that two more orange items and two yellow items are collapsed
- **AND** the two other orange items and the yellow items are collapsed below the change summary

#### Scenario: Link review items a reviewer can open

- **WHEN** a red item's detail is in a committed specification file and an orange item's detail is only in the attempt's session evidence
- **THEN** the red item links to the cited lines of that file on the feature branch on GitHub and the orange item links to the acceptance evidence in the description

#### Scenario: Repair CI after acceptance

- **WHEN** the finalization loop pushes commits to fix CI after acceptance ran
- **THEN** those commits appear as an orange item and the evidence names the accepted commit and lists the later commits
- **AND** the orange count in the description and in the issue comment includes that item

#### Scenario: Complete a review round on a feature

- **WHEN** a review round on a feature pull request completes
- **THEN** the acceptance evidence still names the commit it describes and the completion comment states that acceptance was not re-run
- **AND** the description is the one the round started with, even when finalization rewrote it during the round, with a section listing the round's commits and, when they passed validation and were pushed, the feedback they addressed
- **AND** the acceptance evidence and the "Commits after acceptance" item list every commit after acceptance, including the round's
- **AND** an item whose linked file a round commit changed says it may be fixed by that commit

#### Scenario: A commit after classification changes an item's linked file

- **WHEN** a commit made after acceptance changes the file a red, orange, or yellow item links to
- **THEN** that item in the description says it may be fixed by that commit

#### Scenario: Complete a review round that merged the target branch

- **WHEN** a review round on a feature pull request pushes a merge of the target branch
- **THEN** the description is the one the round started with, and its section of the round's commits and its "Commits after acceptance" item list the merge commit
- **AND** the completion comment names the merge commit, linked, as added after acceptance and links the acceptance evidence in the description

### Requirement: Deliver feature reports durably without duplicates

Feature reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as eval and fix reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a stop comment response

- **WHEN** GitHub accepts the `needs-input` comment but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment

### Requirement: Report the task-compliance result

The feature pull request's description and the issue comment linking it SHALL show the attempt's task-compliance result, as defined by `factory-feature-execution`, so that a reviewer never mistakes an unchecked change for one checked against its tasks. The description SHALL show:

- a `not-run` result as a red item in the "Review first" section. The item states that task-compliance did not run against the change's tasks and gives the reason, such as `Trusted`, `no_applicable_gates`, `no_changes`, or a validator error;
- a `failed` result as a red item that lists the unresolved task-compliance violations;
- a `not-declared` result as a yellow item stating that the target repository declares no task-compliance review;
- a `passed` result as a white item naming the head it reviewed, with its evidence.

The annotation SHALL add these items from the attempt's recorded result, whatever the agent's classification contains, and SHALL show each one exactly once. When commits on the pull request follow the head the last task-compliance verdict reviewed, the item naming commits after acceptance SHALL list them as not covered by task-compliance. The comment linking a produced pull request SHALL state the task-compliance result when it is `not-run` or `failed`, and its red count SHALL include a `not-run` or `failed` task-compliance item. A review round SHALL keep the task-compliance items of the description it started with.

#### Scenario: Review a pull request whose task-compliance did not run

- **WHEN** a feature attempt returns `pull-request` with a task-compliance result of `not-run` because the validator reported `Trusted`
- **THEN** the "Review first" section lists a red item stating that task-compliance did not run, with reason `Trusted`
- **AND** the issue comment linking the pull request states that task-compliance did not run, and its red count includes that item

#### Scenario: Review a pull request whose task-compliance stayed red

- **WHEN** a feature attempt returns `pull-request` with a task-compliance result of `failed`
- **THEN** the "Review first" section lists a red item with the unresolved task-compliance violations
- **AND** the issue comment states that task-compliance failed

#### Scenario: Review a pull request that passed task-compliance

- **WHEN** a feature attempt returns `pull-request` with a task-compliance result of `passed` at the pull request's head
- **THEN** the description has a white task-compliance item naming the reviewed head, and no red or orange task-compliance item
- **AND** the issue comment does not mention task-compliance

#### Scenario: Finalization adds commits after the task-compliance review

- **WHEN** finalization pushes CI repair commits after the head the last task-compliance verdict reviewed
- **THEN** the orange item naming commits after acceptance lists those commits as not covered by task-compliance
- **AND** the white task-compliance item names the earlier head it reviewed

#### Scenario: Review a pull request in a repository without task-compliance

- **WHEN** a feature attempt returns `pull-request` for a target that does not declare task-compliance
- **THEN** the collapsed yellow items include one stating that the repository declares no task-compliance review, and no red task-compliance item appears

#### Scenario: The classification omits the task-compliance result

- **WHEN** the agent's classification has no item for a `not-run` task-compliance result
- **THEN** the description still shows exactly one red task-compliance item, and the red count in the description and the comment includes it

