## ADDED Requirements

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
