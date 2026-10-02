## ADDED Requirements

### Requirement: Never route tasks to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured task type (`[routing] task_type`, default `Task`), whatever the author's repository role. Such an issue SHALL be routed as an untyped issue is. When the task type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule: a card whose Owner and Status still hold the values routing last initialized SHALL return to Backlog, and a card a human has edited since SHALL keep its values. The only path by which a task becomes factory work SHALL be the Ready handoff defined by `factory-task-intake`. Eval, bug, and feature routing SHALL be unchanged.

#### Scenario: File a task as a maintainer

- **WHEN** a user with the maintain or admin role creates an issue with native Type Task in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Retype an auto-routed bug as a task

- **WHEN** routing placed a maintainer's bug in Ready with `Owner=factory`, no human has edited the card, and its native Type is changed to Task
- **THEN** routing returns the card to Backlog
- **AND** the factory does not admit it as task work until a human moves it to Ready

#### Scenario: Retype an edited card as a task

- **WHEN** a human changed a routed card's Status or Owner and its native Type is then changed to Task
- **THEN** routing leaves the card's Owner and Status unchanged
