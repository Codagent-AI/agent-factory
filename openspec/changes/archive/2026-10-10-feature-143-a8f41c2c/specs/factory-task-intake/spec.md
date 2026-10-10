## MODIFIED Requirements

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
