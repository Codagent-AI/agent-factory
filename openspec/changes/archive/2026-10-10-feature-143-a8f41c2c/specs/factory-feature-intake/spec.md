## MODIFIED Requirements

### Requirement: Select eligible features by Priority

The factory SHALL select open issues from configured fix targets with the configured native feature type, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified again at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL rank eligible features by the Project Priority field, highest first with unset values last, then by newest creation time. An ineligible feature SHALL NOT prevent selection of a later eligible feature. Feature selection SHALL be independent of eval, bug, and task selection: each kind fills only its own execution lanes. Selection SHALL fill the kind's Priority lanes (see `factory-claim-lifecycle`): it SHALL admit an eligible feature only into the lane of its Priority, only while that lane is free and no feature attempt occupies a higher lane. A feature that cannot start because its lane or a higher lane is busy SHALL stay in Ready and SHALL NOT prevent selection of a later eligible feature whose lane can take it. Features SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration. Reordering or reprioritizing SHALL NOT interrupt an active attempt.

A `needs-input` label the factory added for a pre-claim revision readiness failure SHALL NOT block a later admission check. The factory SHALL remove that label when readiness passes. An author-added `needs-input` label remains ineligible.

#### Scenario: Pick the highest-priority feature

- **WHEN** the feature lanes are free and two eligible features with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority feature first, regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible features share a Priority value
- **THEN** the factory admits the more recently created feature first

#### Scenario: Skip a blocked or held feature

- **WHEN** the top-ranked feature carries the `needs-input` label or a feature-specific hold applies
- **THEN** the factory selects the next eligible feature instead

#### Scenario: Admit a feature while a fix runs

- **WHEN** a fix attempt occupies a fix lane and an eligible feature waits in Ready with the feature lanes free
- **THEN** the factory admits the feature into the feature lane of its Priority

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Feature card has `Owner=factory` but its author no longer has write access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the feature is not eligible

#### Scenario: Admit a higher feature beside a running lower feature

- **WHEN** a Medium feature attempt is running and an eligible High feature sits in Ready
- **THEN** the factory admits the High feature into the High feature lane, and the Medium attempt continues

#### Scenario: Hold a resumed lower feature while a higher feature runs

- **WHEN** a High feature attempt is running and a blocked Low feature claim's `needs-input` is answered
- **THEN** the Low claim's resumed attempt does not start until no feature attempt occupies a higher lane
