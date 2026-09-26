## ADDED Requirements

### Requirement: Hand off features to the factory through Ready

On each Project poll, the factory SHALL treat placement of an open issue from a configured fix target with the configured native feature type in `Status=Ready`, when the feature kind is configured, as an explicit handoff and set `Owner=factory` before admission when the issue author has effective write, maintain, or admin access to the repository, regardless of the prior or missing Owner value. This handoff SHALL be the only way a feature becomes factory work. The factory SHALL NOT identify the person who moved the card; that gesture is trusted through Project write access. Failure to establish the author's permission SHALL NOT be treated as authorization. A GitHub issue assignee SHALL NOT be required.

#### Scenario: Ready placement assigns factory ownership

- **WHEN** a human moves an open Feature from a configured fix target to Ready with Owner unset or set to human, and its author has write access
- **THEN** the next factory poll verifies the author's permission and sets `Owner=factory`
- **AND** admission re-verifies permission before work starts

#### Scenario: Move an outside contributor's feature to Ready

- **WHEN** a human moves a Feature to Ready whose author lacks write access to the repository
- **THEN** the factory does not set `Owner=factory` and admits no work
- **AND** it explains on the issue once why the feature is not eligible

#### Scenario: Move a feature from an unconfigured repository

- **WHEN** a Feature from a repository that is not a configured fix target is moved to Ready
- **THEN** the factory leaves the card unchanged and admits no work

### Requirement: Select eligible features by Priority

The factory SHALL select open issues from configured fix targets with the configured native feature type, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified again at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL rank eligible features by the Project Priority field, highest first with unset values last, then by newest creation time. An ineligible feature SHALL NOT prevent selection of a later eligible feature. Feature selection SHALL be independent of eval and bug selection: each kind fills only its own execution slot. Features SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration. Reordering or reprioritizing SHALL NOT interrupt an active attempt.

#### Scenario: Pick the highest-priority feature

- **WHEN** the feature slot is free and two eligible features with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority feature regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible features share a Priority value
- **THEN** the factory admits the more recently created feature first

#### Scenario: Skip a blocked or held feature

- **WHEN** the top-ranked feature carries the `needs-input` label or a feature-specific hold applies
- **THEN** the factory selects the next eligible feature instead

#### Scenario: Admit a feature while a fix runs

- **WHEN** a fix attempt occupies the fix slot and an eligible feature waits in Ready
- **THEN** the factory admits the feature into the free feature slot

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Feature card has `Owner=factory` but its author no longer has write access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the feature is not eligible

### Requirement: Resolve feature branches once per claim

A new feature claim SHALL resolve the configured branches of the target repository, Agent Runner, and Agent Skills to commits at admission and record those commits on the claim. Configuration SHALL name branches, not commits. The claim's attempts, including technical recovery retries and attempts resumed after `needs-input`, SHALL use the recorded commits. The `Refs` field SHALL render as `target@<7> runner@<7> skills@<7>`. Only a new claim SHALL re-resolve branch heads.

#### Scenario: Admit a feature

- **WHEN** the factory admits a feature
- **THEN** it records the resolved commits for the target repository, Runner, and Skills on the claim
- **AND** the card's Refs shows those three abbreviated commits

#### Scenario: Resume after the target branch advanced

- **WHEN** the target branch receives new commits while a feature claim is blocked, and the claim is then re-admitted
- **THEN** the resumed attempt uses the commits recorded at admission

### Requirement: Recognize feature retry gestures

A feature claim that ended with `needs-input` is blocked. A blocked feature claim SHALL be re-admitted when an eligible comment newer than the stop appears on the issue, or when a human moves its card from Running to Ready; a claim blocked by a review round's `needs-input` SHALL instead be re-admitted by an eligible pull-request comment newer than the stop. The factory SHALL remove the `needs-input` label and start a new attempt on the same claim that resumes at the step that stopped, as defined by `factory-feature-execution`.

A feature claim that ended with a pull request, a `failed` outcome, or exhausted recovery is settled. Moving a settled feature card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and pull request history. When the most recent prior feature claim for the issue pushed a branch carrying a committed plan, the factory SHALL supply that branch to the new claim, which continues from its plan as defined by `factory-feature-execution`. A settled feature claim whose factory pull request is open SHALL be continued only through review rounds, as defined by `factory-pull-request-lifecycle`; issue comments on a settled claim SHALL NOT trigger an attempt. Moving such a card from Review back to Ready SHALL NOT create a claim or start an attempt: the factory SHALL restore the card to Review and comment once that the open pull request is continued by commenting on it. A blocked claim whose stop was `preflight` SHALL be re-admitted as a fresh definition rather than resumed.

Every resumed or continued attempt's input SHALL include the issue's eligible comments present at admission. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission.

#### Scenario: Answer a blocked feature

- **WHEN** a writer comments on a blocked feature after the stop comment
- **THEN** the next poll removes the `needs-input` label and admits a resumed attempt on the same claim when the feature slot and holds permit
- **AND** the attempt's input includes the writer's comment

#### Scenario: Drag a blocked feature to Ready

- **WHEN** a human moves a blocked feature card from Running to Ready without commenting
- **THEN** the next poll removes the `needs-input` label and admits a resumed attempt with the eligible comments already on the issue

#### Scenario: Drag a failed feature back to Ready

- **WHEN** a human moves a feature card whose claim ended `failed` after its plan commit from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits, keeps the earlier claim history, and supplies the prior claim's branch to the new claim

#### Scenario: Drag a failed feature with an open pull request to Ready

- **WHEN** a feature claim ended `failed` after CI stayed red, leaving its pull request open, and a human moves its card from Review to Ready
- **THEN** no claim is created and no attempt starts
- **AND** the card returns to Review and the issue receives one comment explaining that commenting on the pull request continues it

#### Scenario: Comment on the issue of a settled feature

- **WHEN** a writer comments on the issue, not the pull request, of a settled feature claim in Review
- **THEN** no attempt starts

#### Scenario: Receive a comment from a non-writer

- **WHEN** a user without write access comments on a blocked feature
- **THEN** the feature remains blocked and the comment is not supplied to any attempt

#### Scenario: Post a factory comment on a blocked feature

- **WHEN** the factory delivers one of its own comments to a blocked feature
- **THEN** the feature remains blocked and no attempt starts

### Requirement: Reconcile feature side effects before launching

Before launching any attempt for a feature, including a recovery retry, a resumed attempt, or a new claim, the factory SHALL look up the claim's feature branch and any open factory pull request referencing the issue. The claim's own pushed branch, and its own draft pull request left by an attempt that ended technically before finalization, SHALL be treated as the attempt's resume point rather than as a duplicate. An open factory pull request for the issue that is not a draft SHALL settle the claim as handed off rather than launch a duplicate. When the factory cannot establish whether a prior attempt pushed a branch or opened a pull request, it SHALL hold the claim, report the ambiguity on the issue and in status, and SHALL NOT launch.

#### Scenario: Recover from the claim's draft pull request

- **WHEN** an attempt opened its draft pull request and then failed technically before finalization
- **THEN** reconciliation identifies the branch and draft pull request as the resume point and launches the recovery retry

#### Scenario: Find a ready pull request from a crashed attempt

- **WHEN** an attempt marked its pull request ready but failed before its outcome was recorded
- **THEN** reconciliation reports that pull request as the attempt's result without launching the retry

#### Scenario: Fail to reach GitHub before launch

- **WHEN** the reconciliation lookup fails
- **THEN** the factory does not launch and retries reconciliation on a later poll
