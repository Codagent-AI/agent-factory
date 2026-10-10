# factory-task-intake Specification

## Purpose
TBD - created by archiving change feature-76-691215c4. Update Purpose after archive.
## Requirements
### Requirement: Hand off tasks to the factory through Ready

When the task kind is configured, on each Project poll the factory SHALL treat an open issue placed in `Status=Ready` as an explicit handoff when the issue comes from a configured fix target and has the configured native task type (`[routing] task_type`, default `Task`). It SHALL set `Owner=factory` before admission when the issue author has effective write, maintain, or admin access to the repository, whatever the prior Owner value or its absence. This handoff SHALL be the only way a task becomes factory work. The factory SHALL NOT identify the person who moved the card; that gesture is trusted through Project write access. Failure to establish the author's permission SHALL NOT be treated as authorization. A GitHub issue assignee SHALL NOT be required. Bug and Feature handoff SHALL be unchanged.

#### Scenario: Ready placement assigns factory ownership

- **WHEN** a human moves an open Task from a configured fix target to Ready with Owner unset or set to human, and its author has write access
- **THEN** the next factory poll verifies the author's permission and sets `Owner=factory`
- **AND** admission re-verifies permission before work starts

#### Scenario: Move an outside contributor's task to Ready

- **WHEN** a human moves a Task to Ready and its author lacks write access to the repository
- **THEN** the factory does not set `Owner=factory` and admits no work
- **AND** it explains once on the issue why the task is not eligible

#### Scenario: Move a task from an unconfigured repository

- **WHEN** a Task from a repository that is not a configured fix target is moved to Ready
- **THEN** the factory leaves the card unchanged and admits no work

#### Scenario: Task kind not configured

- **WHEN** the shared configuration has no task section and a writer's Task is moved to Ready
- **THEN** the factory does not set `Owner=factory` and admits no work

#### Scenario: Use a custom task type name

- **WHEN** `[routing] task_type` names `Chore` and a writer's `Chore`-typed issue in a fix target is moved to Ready
- **THEN** the factory hands it off as task work
- **AND** an issue typed `Task` is not treated as task work

### Requirement: Select eligible tasks by Priority

The factory SHALL select an issue as eligible task work when all of these hold:

- it is open, in a configured fix target, and has the configured native task type;
- it has `Owner=factory` and `Status=Ready`;
- its author has effective write, maintain, or admin access, verified again at admission;
- it carries no `needs-input` label;
- no admission hold applies to it.

Selection SHALL rank eligible tasks by the Project Priority field, highest first with unset values last, then by newest creation time. Repository SHALL NOT affect the order. An ineligible task SHALL NOT prevent selection of a later eligible task. Task selection SHALL be independent of eval, bug, and feature selection: each kind fills only its own execution lanes. Selection SHALL fill the kind's Priority lanes (see `factory-claim-lifecycle`): it SHALL admit an eligible task only into the lane of its Priority, only while that lane is free and no task attempt occupies a higher lane. A task that cannot start because its lane or a higher lane is busy SHALL stay in Ready and SHALL NOT prevent selection of a later eligible task whose lane can take it. Tasks SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration. Reordering or reprioritizing SHALL NOT interrupt an active attempt.

#### Scenario: Pick the highest-priority task

- **WHEN** the task lanes are free and two eligible tasks with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority task first, regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible tasks share a Priority value
- **THEN** the factory admits the more recently created task first

#### Scenario: Skip a blocked or held task

- **WHEN** the top-ranked task carries the `needs-input` label or a task-specific hold applies
- **THEN** the factory selects the next eligible task instead

#### Scenario: Admit a task while a fix and a feature run

- **WHEN** a fix attempt holds a fix lane, a feature attempt holds a feature lane, and an eligible task waits in Ready with the task lanes free
- **THEN** the factory admits the task into the task lane of its Priority and the other attempts continue unaffected

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Task card has `Owner=factory` but its author no longer has write access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains once on the issue why the task is not eligible

#### Scenario: Bugs and Features are admitted as before

- **WHEN** the task kind is configured and eligible Bugs and Features sit in Ready
- **THEN** each is admitted through its own kind, lanes, and workflow exactly as without the task kind
- **AND** none of them is admitted as a task

#### Scenario: Admit a higher task beside a running lower task

- **WHEN** a Low task attempt is running and an eligible Urgent task sits in Ready
- **THEN** the factory admits the Urgent task into the Urgent task lane, and the Low attempt continues

#### Scenario: Hold lower tasks while a higher task runs

- **WHEN** a Medium task attempt is running and an eligible Low task sits in Ready
- **THEN** the factory does not admit the Low task until no task attempt occupies the Medium or a higher lane

### Requirement: Resolve task branches once per claim

A new task claim SHALL resolve the configured branches of the target repository, Agent Runner, and Agent Skills to commits at admission and record those commits on the claim. Configuration SHALL name branches, not commits. The claim's attempts, including its technical recovery retry and attempts admitted after `needs-input`, SHALL use the recorded commits. The `Refs` field SHALL render as `target@<7> runner@<7> skills@<7>`. Only a deliberately fresh claim SHALL re-resolve branch heads.

#### Scenario: Admit a task

- **WHEN** the factory admits a task
- **THEN** it records the resolved commits for the target repository, Runner, and Skills on the claim
- **AND** the card's Refs shows those three abbreviated commits

#### Scenario: Retry after the branch advanced

- **WHEN** the target branch receives new commits between a failed attempt and its recovery retry
- **THEN** the retry uses the commits recorded at admission

### Requirement: Recognize task retry gestures

A task claim that ended with a pull request, a `failed` outcome, or exhausted recovery is settled. Moving a settled task card from Review back to Ready SHALL request a new claim using current branch heads while keeping prior claim, attempt, and pull request history. The new attempt's input SHALL include the issue's eligible comments and any prior factory pull request. A settled task claim whose factory pull request is open SHALL also be re-admitted for a review round when an eligible pull-request comment newer than its review checkpoint appears, as defined by `factory-pull-request-lifecycle`. Issue comments on a settled claim SHALL NOT trigger a round.

A task claim that ended with `needs-input` is blocked. A claim blocked by task triage SHALL be re-admitted when an eligible comment newer than the decline appears on the issue, or when a human moves its card from Running to Ready. A claim blocked by a review round's `needs-input` SHALL instead be re-admitted by an eligible pull-request comment newer than the decline. In each case the factory SHALL remove the `needs-input` label and start a new attempt whose input includes the eligible comments present at that time.

An eligible comment is one authored by a user with write, maintain, or admin access to the repository. Comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission.

#### Scenario: Answer a declined task

- **WHEN** a writer comments on a task blocked by triage after the decline comment
- **THEN** the next poll removes the `needs-input` label and admits a new attempt when the task slot and holds permit
- **AND** the new attempt's input includes the writer's comment

#### Scenario: Drag a blocked task to Ready

- **WHEN** a human moves a blocked task card from Running to Ready without commenting
- **THEN** the next poll removes the `needs-input` label and admits a new attempt with the eligible comments already on the issue

#### Scenario: Drag a settled task back to Ready

- **WHEN** a human moves a task card whose claim is settled from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits and keeps the earlier claim history

#### Scenario: Comment on the pull request of a settled task

- **WHEN** a writer comments on the open factory pull request of a settled task claim
- **THEN** the next poll admits a review round on the same claim, keeping its history and pull request

#### Scenario: Comment on the issue of a settled task

- **WHEN** a writer comments on the issue, not the pull request, of a settled task claim in Review
- **THEN** no attempt starts

#### Scenario: Receive a comment from a non-writer

- **WHEN** a user without write access comments on a blocked task
- **THEN** the task remains blocked and the comment is not supplied to any attempt

### Requirement: Reconcile task side effects before launching

Before launching any task attempt, including a recovery retry or a fresh claim, the factory SHALL check the target repository for an existing factory task branch for that issue and an open factory pull request referencing it. An existing open factory pull request SHALL settle the claim as handed off rather than launch a duplicate. When the factory cannot establish whether a prior attempt pushed a branch or opened a pull request, it SHALL hold the claim, report the ambiguity on the issue and in status, and SHALL NOT launch.

#### Scenario: Find a pull request from a crashed attempt

- **WHEN** a task attempt opened a pull request but failed before its outcome was recorded
- **THEN** reconciliation finds the open factory pull request for the issue and reports it as the attempt's result without launching the retry

#### Scenario: Fail to reach GitHub before launch

- **WHEN** the reconciliation lookup fails
- **THEN** the factory does not launch and retries reconciliation on a later poll

