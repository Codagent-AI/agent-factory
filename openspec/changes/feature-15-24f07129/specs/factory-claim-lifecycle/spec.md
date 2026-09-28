## MODIFIED Requirements

### Requirement: Persist accepted work and execution history

The factory SHALL persist accepted requests as claims in local SQLite. A claim SHALL include
the issue and Project-item identity, work kind, frozen settings and revisions, selected eval
suite, progress, and outcome. Each execution attempt SHALL have a run record identifying its
claim and repetition, execution ownership, timing, outcome, and evidence location. Completed
repetitions and consumed recovery retries SHALL survive controller restarts and card moves.
Detailed suite evidence SHALL remain in files, with their locations recorded in SQLite.
GitHub SHALL remain the source of user intent; board fields alone SHALL NOT replace saved
execution history.

Each transition of a claim into `settled`, `cancelled`, or `superseded` from a different
lifecycle SHALL durably record the claim's terminal time, atomically with the lifecycle
change. A claim that settles again after a later run, such as a review round, SHALL have its
terminal time replaced by the later transition. An update that keeps a claim in the same
terminal lifecycle SHALL NOT change its terminal time. For example, recording review activity
on a settled claim that is waiting for its slot is such an update.

Some claims were persisted before terminal times were recorded. The first time the factory
reads such a claim, it SHALL record the claim's last recorded update time as its terminal
time. That time is never earlier than the actual transition. The factory
SHALL NOT derive a terminal time from run completion times.

#### Scenario: Inspect progress after a restart

- **WHEN** the factory restarts after a claim has completed one repetition and attempted another
- **THEN** it retains the frozen inputs, completed result, attempt history, retry usage, and evidence locations
- **AND** it does not reconstruct execution history solely from the card's current fields

#### Scenario: Record when a claim is superseded

- **WHEN** a claim whose last run finished weeks ago is superseded by a fresh claim today
- **THEN** its terminal time is today, recorded with the lifecycle change, and survives a controller restart

#### Scenario: Re-settle after a review round

- **WHEN** a settled fix claim runs a review round and settles again
- **THEN** its terminal time becomes the time of the new settlement

#### Scenario: Keep the terminal time while a review round waits for its slot

- **WHEN** a settled fix claim has eligible review comments, its kind's slot stays busy for several polls, and each poll records the waiting review on the settled claim
- **THEN** its terminal time stays the time it originally settled, and its unreviewed retention deadline does not move

#### Scenario: Read a claim that predates terminal times

- **WHEN** the factory first reads a cancelled claim that has no recorded terminal time
- **THEN** it records the claim's last recorded update time as the terminal time and keeps that value on later polls
