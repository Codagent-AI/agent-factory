## RENAMED Requirements

- FROM: `### Requirement: Select eligible work in manual Project order`
- TO: `### Requirement: Select eligible work by Priority`

## MODIFIED Requirements

### Requirement: Select eligible work by Priority

The factory SHALL select open issues from configured source repositories whose authors have write, maintain, or admin access, with native `Type=Eval`, `Owner=factory`, and `Status=Ready`, valid execution settings, and no applicable admission hold. Selection SHALL rank eligible requests by the Project Priority field, highest first with unset values last, then by newest creation time. Invalid or otherwise ineligible requests SHALL NOT prevent selection of a later eligible request. Reordering or reprioritizing SHALL NOT interrupt active work.

#### Scenario: Prioritize queued evaluations

- **WHEN** a user gives one eligible eval a higher Priority than another before the next selection
- **THEN** the higher-priority eval is selected first, regardless of issue age or board position

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible evals share a Priority value
- **THEN** the more recently created eval is selected first

#### Scenario: Skip an ineligible request

- **WHEN** the highest queued request is invalid or cannot yet resume and a lower request is eligible under current admission controls
- **THEN** the factory selects the lower eligible request

#### Scenario: Reprioritize while an evaluation is running

- **WHEN** a user changes Priority values during active execution
- **THEN** the active evaluation continues and the new ranking governs subsequent selection
