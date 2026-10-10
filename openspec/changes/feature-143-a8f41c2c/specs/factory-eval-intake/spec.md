## ADDED Requirements

### Requirement: Fill eval Priority lanes

Eval selection SHALL fill the eval kind's Priority lanes (see `factory-claim-lifecycle`). The factory SHALL admit an eligible eval only into the lane of its Priority, only while that lane is free and no eval attempt occupies a higher lane. An eval that cannot start because its lane or a higher lane is busy SHALL stay in Ready and SHALL NOT prevent selection of a later eligible eval whose lane can take it. The next repetition or recovery attempt of an eval claim that has started an attempt SHALL need only its own lane to be free. The other admission controls still apply to it: the window, pause, quota, readiness, and the job cap.

#### Scenario: Start a higher eval beside a running lower eval

- **WHEN** a Low eval repetition is running and an eligible High eval sits in Ready
- **THEN** the factory admits the High eval into the High eval lane, and the Low repetition continues

#### Scenario: Hold a new lower eval while a higher eval runs

- **WHEN** a High eval repetition is running and an eligible Medium eval sits in Ready with the Medium eval lane free
- **THEN** the factory does not admit the Medium eval until no eval attempt occupies the High or Urgent lane

#### Scenario: Continue a lower eval's repetitions while a higher eval runs

- **WHEN** a Low eval claim has finished repetition 1 of three, and a High eval repetition is running
- **THEN** the Low claim's repetition 2 starts in the Low eval lane under the normal admission controls

#### Scenario: Queue a second eval of the same Priority

- **WHEN** a High eval repetition is running and a second eligible High eval sits in Ready
- **THEN** the second High eval waits until the High eval lane is free
