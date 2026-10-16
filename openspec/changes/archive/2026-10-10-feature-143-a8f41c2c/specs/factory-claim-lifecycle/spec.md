## MODIFIED Requirements

### Requirement: Prevent overlapping execution

The factory SHALL run work of each kind (eval, fix, feature, and task) in Priority lanes, one lane per Project Priority level: Urgent, High, Medium, and Low, in that order from highest to lowest. The factory SHALL execute at most one attempt per work kind and lane at a time across the service and manual execution commands. An attempt is unfinished, and occupies its lane, while it is reserved, running, or observing. The factory SHALL reserve a new start of kind K in lane P only when both of these hold:

- no unfinished attempt of K occupies lane P;
- no unfinished attempt of K occupies a lane higher than P.

A continuation, defined in "Classify new starts and continuations", SHALL require only that its own lane is free. The factory SHALL NOT stop, pause, or move a running attempt because higher-priority work arrives or because its card's Priority changes. Lanes of different kinds SHALL be independent: an attempt of one kind SHALL NOT block or free a lane of another kind.

Lane enforcement SHALL be atomic in SQLite for every reservation path: the resident service, `tick`, manual execution commands, review rounds, and blocked-claim resumes. Two paths SHALL NOT both reserve the same lane, and no path SHALL reserve a new start past a busy higher lane. The factory SHALL reconcile saved execution records with surviving processes, containers, and evidence before dispatching new work of any kind. A blocked fix, feature, or task claim SHALL NOT occupy a lane. Status and pause controls SHALL remain usable while execution is active.

Wherever another requirement refers to a kind's execution slot, it means the lane the attempt would occupy. Such a slot is free for an attempt when that attempt could be reserved under the rule above. A kind's slots are all free when no unfinished attempt of that kind exists in any lane. Releasing a kind's slot means releasing the lane the attempt occupied.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution in a lane where the service already has an attempt of the same kind running
- **THEN** no second attempt of that kind starts in that lane
- **AND** status and pause controls remain available

#### Scenario: Run an eval and a fix together

- **WHEN** an eval repetition is running and an eligible bug is admitted
- **THEN** the fix attempt starts in its own lane and the eval continues unaffected

#### Scenario: Migrate the single-slot database

- **WHEN** the factory starts against a database at the current shipped schema version with claims, runs, reporting progress, a pause, and an active quota hold
- **THEN** it migrates the schema to per-kind slots in one transaction without losing claim, run, or settings history
- **AND** the existing quota hold continues to apply to the provider it was recorded for

#### Scenario: Run a feature beside a fix and an eval

- **WHEN** an eval repetition and a fix attempt are running and an eligible feature is admitted
- **THEN** the feature attempt starts in its own lane and the other attempts continue unaffected

#### Scenario: Start a higher priority beside a running lower one

- **WHEN** a Low fix attempt is running and a Medium bug becomes eligible
- **THEN** the Medium fix attempt starts in the Medium lane without waiting
- **AND** the Low attempt continues unaffected

#### Scenario: Start a third priority level

- **WHEN** a Low and a Medium fix attempt are running and a High bug becomes eligible
- **THEN** the High fix attempt starts in the High lane without waiting, and both running attempts continue

#### Scenario: Hold lower priorities while a higher one runs

- **WHEN** a High fix attempt is running, the Low and Medium lanes are free, and eligible Low and Medium bugs sit in Ready
- **THEN** neither the Low nor the Medium bug starts until no fix attempt occupies the High or Urgent lane

#### Scenario: Keep one attempt per priority level

- **WHEN** a High fix attempt is running and a second High bug becomes eligible
- **THEN** the second High bug waits until the High lane is free

#### Scenario: Urgent holds every lower lane

- **WHEN** an Urgent task attempt is running and eligible High, Medium, and Low tasks sit in Ready with their lanes free
- **THEN** none of them starts until the Urgent attempt finishes

#### Scenario: Do not preempt running work

- **WHEN** a Low feature attempt and a Medium feature attempt are running and a High feature starts
- **THEN** the Low and Medium attempts keep running to their own outcomes

#### Scenario: A higher lane of another kind does not block

- **WHEN** an Urgent fix attempt is running and an eligible Low eval sits in Ready with the eval lanes free
- **THEN** the Low eval starts

#### Scenario: Concurrent reservations for one lane

- **WHEN** the resident service and a manual command try at the same moment to reserve the free High fix lane
- **THEN** exactly one reservation succeeds and the other path starts nothing

#### Scenario: Concurrent reservations across lanes

- **WHEN** one path reserves a High fix attempt while another path tries at the same moment to reserve a new Low fix start
- **THEN** the reservations take effect in a single order: if the High reservation takes effect first, the Low start is refused; if the Low start takes effect first, both succeed, because a higher lane is never blocked by a lower one
- **AND** no Low start takes effect while the High attempt is unfinished

## ADDED Requirements

### Requirement: Assign each attempt a Priority lane

When it reserves an attempt, the factory SHALL assign the attempt's lane from the card's current Project Priority. A card whose Priority is unset, or is not one of Urgent, High, Medium, or Low, SHALL use the Low lane. The factory SHALL record the lane on the attempt when it reserves it. The attempt SHALL keep that lane until it finishes, whatever later happens to the card's Priority. Each later attempt of the same claim SHALL take its lane from the card's Priority when that attempt is reserved.

#### Scenario: Unprioritized work runs in the Low lane

- **WHEN** an eligible bug with no Priority is admitted while the fix lanes are free
- **THEN** its attempt occupies the Low lane, and a later Low bug waits for that lane

#### Scenario: Reprioritize a running attempt

- **WHEN** a running fix attempt was reserved in the Low lane and a human changes its card to High
- **THEN** the attempt keeps running in the Low lane, and another High bug can still start in the High lane

#### Scenario: A later attempt follows the new Priority

- **WHEN** a Low eval claim finishes a repetition, its card is changed to High, and the High eval lane is free
- **THEN** its next repetition is reserved in the High lane

### Requirement: Classify new starts and continuations

The factory SHALL treat every reservation as a new start unless it is a continuation. A reservation SHALL be a continuation only when the same claim already has an attempt that actually started in its current execution episode. An attempt that was only reserved, and failed before launch, SHALL NOT count as started. A claim's execution episode SHALL begin at each of these:

- its admission or fresh re-admission;
- its unblock after `needs-input`;
- the admission of a review round.

The factory SHALL decide the classification from the claim's saved run history inside the same atomic step that reserves the attempt. It SHALL NOT take it from a caller's request or from the claim's lifecycle alone. A claim that is active or waiting but has not started an attempt in its current episode SHALL be a new start. That includes a claim held by a preparation or readiness failure before its first launch.

#### Scenario: Continue an eval while a higher eval runs

- **WHEN** a Low eval claim finished repetition 1 of three, a High eval attempt is running, and the Low eval lane is free
- **THEN** repetition 2 of the Low claim starts in the Low lane

#### Scenario: Retry a running issue after a technical failure

- **WHEN** a Low fix attempt that started fails technically while a High fix attempt runs, and recovery budget remains
- **THEN** the retry starts in the Low lane once that lane is free, without waiting for the High attempt

#### Scenario: A claim that never launched is a new start

- **WHEN** a Low bug was accepted and its preparation failed a readiness check before any attempt started, and a High fix attempt is now running
- **THEN** the Low claim's first attempt does not start until no fix attempt occupies a higher lane

#### Scenario: A pre-launch failure keeps the new-start category

- **WHEN** a Low claim's first reserved attempt failed during planning before launch, and a High fix attempt is now running
- **THEN** the Low claim's next attempt is still a new start and waits for the higher lane to clear

#### Scenario: An unblocked claim is a new start

- **WHEN** a Medium feature claim blocked for `needs-input` is unblocked while a High feature attempt runs
- **THEN** its resumed attempt does not start until no feature attempt occupies a higher lane

### Requirement: Hold every lane for attempts that predate lanes

An unfinished attempt with no recorded lane SHALL occupy every lane of its kind until it finishes. That covers an attempt that was unfinished when the factory upgraded to lanes, and an attempt reserved by a release without lanes. While it is unfinished, no other attempt of that kind SHALL start, either a new start or a continuation. Adding lanes to an existing database SHALL keep every claim, run, and setting. Lanes SHALL take effect when the resident of a release with lanes starts. Until then, a release with lanes that opens the database, for example its `doctor` during a deploy, SHALL leave the per-kind guard in place. It SHALL leave the database openable by the previous release and by supervisors still running from it, and those supervisors SHALL be able to keep recording their attempts' progress and results.

#### Scenario: Upgrade while a fix runs

- **WHEN** the factory upgrades to lanes while a fix attempt reserved by the previous release is running, and an eligible Medium bug sits in Ready
- **THEN** the Medium bug waits until that attempt finishes, whatever the running claim's Priority
- **AND** after it finishes, fix admission follows the lane rules

#### Scenario: Supervisor from the previous release finishes after upgrade

- **WHEN** a supervisor started by the previous release records its attempt's result after the upgrade
- **THEN** the result is recorded, the attempt finishes, and its kind's lanes are released

#### Scenario: Doctor of the new release leaves the old guard in place

- **WHEN** a deploy runs the new release's `doctor` while the previous release's resident is still running
- **THEN** the previous release's guard of one unfinished attempt per kind still holds, and lanes take effect only when the new release's resident starts
