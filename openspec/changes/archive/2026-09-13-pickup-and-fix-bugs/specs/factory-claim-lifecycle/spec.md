## MODIFIED Requirements

### Requirement: Keep lifecycle behavior independent of work kind

Core scheduling, claim/run persistence, retries, cancellation, restart recovery, and durable reporting SHALL remain independent of Codagent identities and eval-specific parsing or scoring. Work kinds SHALL supply request interpretation, execution planning, result interpretation, report presentation, admission-time input resolution, kind-specific readiness checks, and the kind's fresh-attempt and unblock gestures through one handler interface that the controller and runtime use for every kind. The eval handler SHALL own repetition semantics and suite integration; the fix handler SHALL own bug eligibility, branch resolution, workflow invocation, and outcome mapping. The generic core SHALL NOT interpret a score out of 70, Runner/Skills fields, an `and-scene` command path, a pull-request outcome, or the blocked state's label. Extracting this interface from the existing eval-shaped controller SHALL preserve all existing eval behavior.

#### Scenario: Integrate another work kind later

- **WHEN** a later change supplies another work-kind handler
- **THEN** it can reuse the saved-claim, execution-attempt, scheduling, cancellation, and recovery behavior without adding that kind's request or scoring rules throughout the controller

#### Scenario: Extract the interface without changing eval behavior

- **WHEN** the eval handler is moved behind the handler interface
- **THEN** existing eval intake, execution, reporting, and recovery behavior is unchanged and its existing tests pass unmodified

#### Scenario: Read a kind-specific gesture

- **WHEN** a human drags a settled fix card from Review to Ready
- **THEN** the controller asks the fix handler, which treats it as a fresh-attempt request
- **AND** an eval card dragged the same way keeps its existing correction behavior

### Requirement: Prevent overlapping execution

The factory SHALL execute at most one attempt per work kind at a time across the service and manual execution commands: one eval repetition and one fix attempt. Per-kind slots SHALL be enforced atomically in SQLite so two admission paths cannot both reserve the same kind's slot. The factory SHALL reconcile saved execution records with surviving processes, containers, and evidence before dispatching new work of either kind. A blocked fix claim SHALL NOT occupy a slot. Status and pause controls SHALL remain usable while execution is active.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution while the service already has an attempt of the same kind running
- **THEN** no second attempt of that kind starts
- **AND** status and pause controls remain available

#### Scenario: Run an eval and a fix together

- **WHEN** an eval repetition is running and an eligible bug is admitted
- **THEN** the fix attempt starts in its own slot and the eval continues unaffected

#### Scenario: Migrate the single-slot database

- **WHEN** the factory starts against a database at the current shipped schema version with claims, runs, reporting progress, a pause, and an active quota hold
- **THEN** it migrates the schema to per-kind slots in one transaction without losing claim, run, or settings history
- **AND** the existing quota hold continues to apply to the provider it was recorded for

### Requirement: Correct status edits that contradict factory execution

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. A blocked fix claim SHALL be an exception: its card remains in Running with the `needs-input` label without execution and SHALL NOT be moved to a queued status; a blocked card moved to Review or Done SHALL be restored to Running, while a blocked card moved to Ready SHALL be treated as the fix handler's unblock gesture. Moving a request with verified active execution to Ready, Review, or Done SHALL restore Running and continue that same execution without restarting it or admitting overlapping work. Such status changes SHALL NOT cancel execution. Issue closure SHALL retain its defined cancellation behavior and take precedence over restoring Running. A drag that the request's work-kind handler recognizes as a fresh-attempt gesture SHALL be honored rather than corrected.

The factory SHALL leave status edits on cards without factory ownership alone. Corrective updates SHALL include a brief issue comment explaining the actual state and correction, using durable reporting to avoid repeating the same correction comment on each poll. The default correction interval SHALL be the normal five-minute poll; unavailable GitHub access SHALL delay the correction rather than change execution state based on an unverified board observation.

#### Scenario: Move an idle request to Running

- **WHEN** a human moves a factory-owned queued request to Running while it has no active execution
- **THEN** the next successful poll restores its appropriate queued status
- **AND** the drag itself creates no execution attempt and bypasses no admission control
- **AND** the factory briefly explains the correction on the issue

#### Scenario: Move a running request to Ready

- **WHEN** a human moves a factory-owned request with verified active execution to Ready
- **THEN** the next successful poll restores Running and continues the existing execution
- **AND** no fresh claim, duplicate attempt, restart, or recovery retry results from the status edit

#### Scenario: Move a running request to a handoff or completion column

- **WHEN** a human moves a factory-owned request with verified active execution to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running without cancelling execution or reporting it as complete

#### Scenario: Close an issue whose status also changed

- **WHEN** the factory observes that an active request's issue is closed and its card has moved out of Running
- **THEN** it follows the issue-closure cancellation policy rather than restoring Running

#### Scenario: Change a card that is not factory-owned

- **WHEN** a human changes the Status of a card without factory ownership
- **THEN** the factory does not correct that card or infer an execution request from the drag

#### Scenario: Retry delivery of a status correction

- **WHEN** a corrective update or its explanatory comment needs delivery again after a poll or controller restart
- **THEN** the factory reconciles current execution and board state before retrying delivery
- **AND** it does not duplicate a correction comment already delivered

#### Scenario: Leave a blocked fix in Running

- **WHEN** a fix claim is blocked awaiting input and its card sits in Running with the `needs-input` label
- **THEN** the poll does not move the card or post a correction

#### Scenario: Move a blocked fix to Review

- **WHEN** a human moves a blocked fix card from Running to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running with the label intact and explains the correction once

## ADDED Requirements

### Requirement: Classify admission holds by scope

Pause, the free-disk floor, and the memory headroom check SHALL apply to every kind. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.

#### Scenario: Hold Codex while fixing with Cursor

- **WHEN** a Codex quota hold is active and the fix roles use Cursor
- **THEN** an eligible bug can still be admitted while eval work using Codex waits

#### Scenario: Lose suite readiness

- **WHEN** the eval suite's prerequisites are unavailable
- **THEN** eval admission is held and fix admission is unaffected

### Requirement: Check memory headroom before admission

Before admitting any attempt, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check.

#### Scenario: Admit a second attempt with headroom

- **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
- **THEN** the second kind's attempt may be admitted

#### Scenario: Wait for memory

- **WHEN** the remaining Docker allowance is below the reservation
- **THEN** no new attempt starts and status names memory as the blocking condition

### Requirement: Exempt settled work from closure cancellation

Issue closure SHALL cancel only claims with unfinished execution. A settled fix claim with a recorded pull request SHALL remain eligible for its post-merge sync whether the issue was closed by a human or by the factory after a successful sync, and closure SHALL NOT be reported as a cancellation for such a claim.

#### Scenario: Close a fixed issue by hand before the sync

- **WHEN** a human closes an issue whose fix claim is settled with a merged PR before the factory has synced
- **THEN** the claim is not cancelled and the sync still runs once

#### Scenario: Observe the factory's own closure

- **WHEN** the factory closed the issue after a successful sync and observes the closed issue on a later poll
- **THEN** it records nothing new and posts no cancellation comment
