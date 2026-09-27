## ADDED Requirements

### Requirement: Never route features to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured feature type, whatever the author's repository role. Such an issue SHALL be routed as an untyped issue is. When the feature type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule: a card whose Owner and Status still hold the values routing last initialized SHALL return to Backlog, and a card a human has edited since SHALL keep its values. An issue auto-routed to the factory as a bug and then retyped as a feature SHALL therefore not remain in Ready as factory feature work. The only path by which a feature becomes factory work SHALL be the Ready handoff defined by `factory-feature-intake`.

#### Scenario: File a feature as a maintainer

- **WHEN** a user with the maintain or admin role creates an issue with native Type Feature in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Retype an auto-routed bug as a feature

- **WHEN** routing placed a maintainer's bug in Ready with `Owner=factory`, no human has edited the card, and its native Type is changed to Feature
- **THEN** routing returns the card to Backlog
- **AND** the factory does not admit it as feature work

#### Scenario: Retype an edited card as a feature

- **WHEN** a human changed a routed card's Status or Owner and its native Type is then changed to Feature
- **THEN** routing leaves the card's Owner and Status unchanged
