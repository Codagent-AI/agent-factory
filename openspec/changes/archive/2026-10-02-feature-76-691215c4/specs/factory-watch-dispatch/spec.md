## MODIFIED Requirements

### Requirement: Detect watch events in every cycle

When watching is enabled, every factory cycle, whether started by the resident or by `tick`, SHALL detect watch events after it consumes attempt results. It SHALL detect them whether the factory is paused or not, and whether the admission window is open or not. The watcher exists to make sure the factory itself works, so the factory SHALL detect only two event kinds:

- `FAILURE`: an attempt of any kind whose status is `failed`, `interrupted`, `cancelled`, or `timed_out`, and which still has that status once the failure grace period has passed since it finished;
- `PR-READY`: a fix, feature, or task attempt that completed with the `pull-request` outcome. This includes initial, recovery, and review-round attempts.

A newly admitted claim and a finished eval SHALL NOT be detected as events. The factory SHALL keep a durable watch cursor holding the time watching was enabled and the time of the last detection pass, and each cycle SHALL record its detection time there. `PR-READY` detection SHALL scan eligible fix, feature, and task attempts completed with the `pull-request` outcome, finished after watching was enabled and within the last 7 days, and not previously detected.

`FAILURE` detection SHALL NOT depend on that point. Each cycle SHALL detect every attempt that meets all of these conditions:

- it has a failure status;
- it finished after watching was enabled and within the last 7 days;
- it finished at least the currently configured grace period ago;
- its result has been consumed (`consumed-results` exists for the attempt);
- it has not yet been detected.

So a change to the grace period can neither skip a failure nor detect it twice. When watching is first enabled, and whenever it is enabled again after being disabled, the enablement time SHALL be the current time, so events from before that time are not detected. When watching is disabled, no event SHALL be detected. An error during detection SHALL be logged, SHALL leave the cursor unchanged, and SHALL NOT stop the rest of the cycle.

#### Scenario: A quiet day starts no session

- **WHEN** watching is enabled and a day of cycles passes with no failed or pull-request work
- **THEN** no event is detected and no agent session is started

#### Scenario: Claims and finished evals are not events

- **WHEN** a claim is admitted and an eval attempt finishes successfully
- **THEN** no event is detected and no dispatch is recorded for either

#### Scenario: A host fix passes through interrupted

- **WHEN** a host fix attempt is recorded `interrupted` when its process exits, and a cycle within the grace period reads its outcome and records it `completed` with a pull request
- **THEN** no `FAILURE` event is detected for it, and one `PR-READY` event is detected

#### Scenario: A cycle fails before consuming a host result

- **WHEN** a host fix is recorded `interrupted`, GitHub work fails before result consumption while the watch cursor advances, and a later cycle consumes the result and normalizes it to `completed` with a pull request more than two minutes after its `finished_at`
- **THEN** exactly one `PR-READY` event is queued and one PR-READY check session starts, with no `FAILURE` event

#### Scenario: A failed result has not been consumed

- **WHEN** an attempt is recorded failed beyond the grace period but its result has not been consumed
- **THEN** no `FAILURE` event is queued until a cycle consumes the result

#### Scenario: A failure outlasts the grace period

- **WHEN** a Fly eval attempt is recorded `failed` and is still `failed` when the grace period has passed
- **THEN** the first cycle after the grace period detects one `FAILURE` event for that attempt

#### Scenario: The grace period shrinks while a failure waits

- **WHEN** an attempt failed five minutes before a cycle, with a seven-minute grace period, and the grace period is changed to zero before the next cycle
- **THEN** the next cycle detects exactly one `FAILURE` event for that attempt

#### Scenario: The grace period grows while a failure waits

- **WHEN** an attempt failed five minutes before a cycle, with a seven-minute grace period, and the grace period is changed to fifteen minutes
- **THEN** no `FAILURE` event is detected for it until fifteen minutes after it finished, and then exactly one is

#### Scenario: Detection while paused

- **WHEN** the factory is paused and a fix attempt completes with a pull request
- **THEN** the next cycle detects the `PR-READY` event

#### Scenario: Enable watching on an installation with history

- **WHEN** watching is enabled for the first time on an installation with past failed attempts and pull requests
- **THEN** none of those past events is detected, and events after the enablement are

#### Scenario: Re-enable watching

- **WHEN** watching is disabled for a day, a pull request becomes ready during that day, and watching is enabled again
- **THEN** the pull request that became ready while watching was disabled is not detected

#### Scenario: A task attempt opens a pull request

- **WHEN** watching is enabled and a task attempt completes with the `pull-request` outcome
- **THEN** the next cycle detects one `PR-READY` event for it

#### Scenario: A task attempt fails

- **WHEN** a task attempt is recorded `failed` and its result has been consumed past the grace period
- **THEN** one `FAILURE` event is detected for it
