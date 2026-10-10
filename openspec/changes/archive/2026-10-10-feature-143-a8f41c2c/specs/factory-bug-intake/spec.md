## MODIFIED Requirements

### Requirement: Select eligible bugs by Priority

On each Project poll, the factory SHALL treat placement of an open issue from a configured fix target with native `Type=Bug` in `Status=Ready` as an explicit handoff and set `Owner=factory` before admission when the issue author has effective write, maintain, or admin access, regardless of the prior or missing Owner value. A GitHub issue assignee SHALL NOT be required. The factory SHALL then select open issues with native `Type=Bug`, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified again at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL rank eligible bugs by the Project Priority field, highest first with unset values last, then by newest creation time; repository SHALL NOT affect the order. An ineligible bug SHALL NOT prevent selection of a later eligible bug. Selection SHALL be independent for each work kind: each kind fills only its own execution lanes. Selection SHALL fill the kind's Priority lanes (see `factory-claim-lifecycle`): it SHALL admit an eligible bug only into the lane of its Priority, only while that lane is free and no bug attempt occupies a higher lane. A bug that cannot start because its lane or a higher lane is busy SHALL stay in Ready and SHALL NOT prevent selection of a later eligible bug whose lane can take it. Bugs SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration.

A `needs-input` label the factory added for a pre-claim revision readiness failure SHALL NOT block a later admission check. The factory SHALL remove that label when readiness passes. An author-added `needs-input` label remains ineligible.

#### Scenario: Ready placement assigns factory ownership

- **WHEN** a human moves an open configured Bug to Ready with Owner unset or set to human
- **THEN** the next factory poll verifies the author's repository permission and sets `Owner=factory`
- **AND** admission re-verifies permission before work starts

#### Scenario: Pick the top bug

- **WHEN** the fix lanes are free and two eligible bugs with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority bug first, regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible bugs share a Priority value
- **THEN** the factory admits the more recently created bug first

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Bug card has `Owner=factory` but its author lacks the required repository access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the bug is not eligible

#### Scenario: Skip a blocked or held bug

- **WHEN** the top-ranked bug carries the `needs-input` label or a fix-specific hold applies
- **THEN** the factory selects the next eligible bug instead

#### Scenario: Reorder while a fix is running

- **WHEN** a user changes a bug's Priority during active fix execution
- **THEN** the active fix continues and the new ranking governs subsequent selection

#### Scenario: Admit a higher bug beside a running lower fix

- **WHEN** a Low fix attempt is running and an eligible Medium bug sits in Ready
- **THEN** the factory admits the Medium bug into the Medium fix lane

#### Scenario: Skip a bug whose lane is busy

- **WHEN** a Medium fix attempt is running, and the eligible bugs in Ready are a second Medium bug and a High bug
- **THEN** the factory admits the High bug, and the second Medium bug stays in Ready until no fix attempt occupies the Medium or a higher lane

#### Scenario: Hold lower bugs while a higher fix runs

- **WHEN** a High fix attempt is running and only Low and Medium bugs sit in Ready
- **THEN** the factory admits neither, and both stay in Ready without a decline or `needs-input` label
