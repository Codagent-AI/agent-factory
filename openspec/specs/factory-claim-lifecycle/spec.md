# factory-claim-lifecycle Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
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

### Requirement: Persist accepted work and execution history

The factory SHALL persist accepted requests as claims in local SQLite, including issue and Project-item identity, work kind, frozen settings and revisions, selected eval suite, progress, and outcome. Each execution attempt SHALL have a run record identifying its claim and repetition, execution ownership, timing, outcome, and evidence location. Completed repetitions and consumed recovery retries SHALL survive controller restarts and card moves. Detailed suite evidence SHALL remain in files, with their locations recorded in SQLite. GitHub SHALL remain the source of user intent; board fields alone SHALL NOT replace saved execution history.

#### Scenario: Inspect progress after a restart

- **WHEN** the factory restarts after a claim has completed one repetition and attempted another
- **THEN** it retains the frozen inputs, completed result, attempt history, retry usage, and evidence locations
- **AND** it does not reconstruct execution history solely from the card's current fields

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

### Requirement: Preserve running evaluations across controller restarts

Evaluations SHALL be launched so that restarting the factory controller does not itself terminate them. After restart, the factory SHALL resume monitoring verified surviving execution, recover results from execution that finished while it was offline, or apply the recovery policy if execution itself stopped unexpectedly. A controller restart alone SHALL NOT create another execution attempt, consume a retry, or reset supervision timers. Completed repetitions SHALL NOT be rerun to recover reporting.

#### Scenario: Restart while an evaluation is still running

- **WHEN** the controller restarts and the recorded evaluation is still running
- **THEN** that evaluation continues and the controller resumes monitoring it
- **AND** no duplicate execution starts, no recovery retry is consumed, and existing timing history is retained

#### Scenario: Finish while the controller is offline

- **WHEN** an evaluation finishes while the controller is offline and its results are available on restart
- **THEN** the factory records the result and resumes pending reporting without repeating the evaluation

#### Scenario: Find execution interrupted

- **WHEN** reconciliation establishes that the evaluation itself stopped unexpectedly without a completed result
- **THEN** the factory preserves existing evidence and applies the repetition's remaining recovery policy

### Requirement: Bound technical recovery per repetition

The factory SHALL allow one automatic recovery retry per repetition after a technical execution failure, using supported suite resume behavior and preserving completed repetitions. It SHALL record execution attempts and consumed retries durably. A completed evaluation with a poor product result SHALL NOT trigger a technical retry. A suite-confirmed non-resumable implementation-workflow failure SHALL settle that repetition as failed without a retry and SHALL allow remaining repetitions to proceed. Resumable workflow failures and recoverable harness failures SHALL follow the bounded technical recovery policy. The factory SHALL preserve the suite's failure owner, code, and resumable value and SHALL NOT infer non-resumability from the workflow-failed status alone. Recognized quota waiting, unavailable prerequisites detected before execution, and waiting for an admission window SHALL NOT consume this retry budget.

If the recovery retry fails, the factory SHALL stop the claim, leave remaining repetitions unstarted, retain completed results and failure evidence, and move the issue to Review with `infra-error` and an explanation. Other eligible requests MAY proceed. Another evaluation SHALL require the explicit fresh-request behavior defined by intake and SHALL start the requested repetitions anew while preserving prior claim history.

#### Scenario: Recover the second repetition

- **WHEN** repetition 1 completed and repetition 2 encounters its first technical failure
- **THEN** the factory can resume repetition 2 once when execution is permitted
- **AND** repetition 1 remains completed

#### Scenario: Exhaust recovery

- **WHEN** repetition 2's recovery retry fails in a request for three repetitions
- **THEN** the claim stops, repetition 3 remains unstarted, and the issue moves to Review with `infra-error` and an explanation
- **AND** completed results and both failed attempts' evidence remain available
- **AND** the factory may process other eligible requests

#### Scenario: Preserve the retry limit across interruption

- **WHEN** the controller restarts or the card moves after the repetition has consumed its recovery retry
- **THEN** those events do not grant another automatic recovery retry

#### Scenario: Complete with a product failure

- **WHEN** an evaluation completes and establishes that the tested product failed
- **THEN** the factory reports the result without retrying it as a technical failure

#### Scenario: Settle a non-resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=false`, such as a typed prohibited workflow action
- **THEN** the factory records a failed repetition without attempting technical recovery and permits remaining repetitions
- **AND** it preserves the suite's product verdict and failure details instead of inventing a product score or verdict

#### Scenario: Recover a resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=true` and no recognized quota hold
- **THEN** the repetition follows the existing bounded technical recovery policy rather than being treated as a settled product failure

### Requirement: Wait for provider quota without discarding progress

On a detected Codex usage limit, including during product judging, the factory SHALL preserve claim progress and persist an admission hold until the reliably interpreted reset time reported by the CLI. Only after recognizing a Codex rate-limit diagnostic, if no reset time can be interpreted reliably, it SHALL use a configurable default of five hours from detection. A generic execution error or an absent reset time alone SHALL NOT create a quota hold; generic errors SHALL follow ordinary failure recovery. The hold SHALL survive controller restarts. After it expires, execution SHALL remain subject to the admission window, pause state, and other applicable prerequisites.

The `and-scene` suite SHALL continue to manage Claude quota waiting internally. Recognized quota waiting SHALL NOT consume the technical recovery retry, although any later execution attempt SHALL still be recorded. Automatic continuation SHALL retain the same frozen claim and completed repetitions.

#### Scenario: Detect a reported Codex reset

- **WHEN** Codex reports a usage limit with a reliable reset time
- **THEN** the factory records the hold and preserves the unfinished claim until execution becomes eligible after that reset
- **AND** waiting consumes no recovery retry

#### Scenario: Use the fallback after a restart

- **WHEN** a recognized Codex rate-limit error had no reliable reset time and the controller restarts during the resulting five-hour hold
- **THEN** the original hold remains in effect without restarting the five-hour countdown

#### Scenario: Avoid a false quota hold

- **WHEN** an execution error contains no recognized Codex rate-limit diagnostic
- **THEN** the factory applies ordinary failure recovery without creating a five-hour quota hold

#### Scenario: Reach reset outside the admission window

- **WHEN** the usage hold expires after 15:00 under the default schedule
- **THEN** the factory waits for the next admission window before continuing eligible execution

### Requirement: Enforce separate progress and runtime limits

Each execution attempt SHALL have configurable limits with defaults of 30 minutes without progress, six hours of execution excluding recognized bounded quota waits, and 12 hours of total elapsed time including those waits. Progress SHALL include activity in known suite logs and saved state as well as subprocess output. Recognized bounded quota waits SHALL suspend inactivity detection and be excluded from the execution-time budget, but SHALL count toward the total elapsed-time limit. Timing history SHALL survive controller restarts. Waiting between repetitions SHALL NOT count toward an attempt's timers.

When a limit is reached, the factory SHALL stop verified owned execution, preserve evidence, record which limit was exceeded, and apply the bounded technical recovery policy. It SHALL NOT classify a timeout alone as a product failure.

#### Scenario: Make progress through artifact logs

- **WHEN** subprocess output is quiet but known suite logs or saved state continue to advance
- **THEN** the factory recognizes progress rather than stopping the attempt for inactivity

#### Scenario: Wait for quota within an attempt

- **WHEN** the suite enters a recognized bounded quota wait
- **THEN** the inactivity and execution-time accounting exclude that wait
- **AND** total elapsed time continues toward the 12-hour limit

#### Scenario: Exceed a configured limit

- **WHEN** an attempt exceeds its inactivity, execution-time, or total elapsed-time limit
- **THEN** the factory stops verified owned execution and records the specific timeout reason
- **AND** it preserves evidence and applies the remaining technical recovery budget

### Requirement: Observe admission and pause controls between repetitions

The factory SHALL check admission controls before starting each repetition and before starting a recovery attempt. Under the default schedule, starts SHALL be permitted from 00:00 up to but not including 15:00 local time. An attempt already running when the window closes SHALL be allowed to finish subject to supervision limits. Remaining repetitions SHALL retain the same claim and inputs while waiting.

`agent-factory pause` SHALL prevent new execution while allowing the current repetition to finish. `agent-factory resume` SHALL permit eligible execution subject to the window and other holds. Pause state SHALL survive controller restarts. These controls SHALL apply to the whole factory and SHALL NOT introduce additional Project statuses.

#### Scenario: Finish after the cutoff

- **WHEN** repetition 1 of three finishes at 15:30 under the default schedule
- **THEN** its result is saved and repetition 2 waits until the next admission window
- **AND** the claim, frozen inputs, completed work, and recovery budget are preserved

#### Scenario: Pause and resume the factory

- **WHEN** the user pauses during an active repetition
- **THEN** that repetition may finish, but no subsequent repetition or recovery attempt starts while paused
- **AND** resuming permits execution only when the other admission conditions also allow it

### Requirement: Cancel a request when its issue is closed

On the next successful GitHub check after the user closes a request's issue, the factory SHALL stop verified owned execution for that request, skip its remaining repetitions, and preserve existing evidence. Cancellation SHALL NOT trigger a recovery retry or prevent other eligible requests from proceeding once execution has stopped. The factory SHALL respect the closed issue rather than moving it back into the active queue; GitHub closure automation SHALL move its card to Done.

#### Scenario: Close an active request

- **WHEN** the user closes an issue while its evaluation is running
- **THEN** the next successful GitHub check triggers cancellation of that request's owned execution
- **AND** remaining repetitions are skipped and existing evidence is retained
- **AND** other eligible requests can proceed after the cancelled execution stops

### Requirement: Verify execution ownership before termination

Before terminating execution for cancellation, timeout, or recovery cleanup, the factory SHALL verify that the process or container belongs to the recorded repetition using its recorded identity and associated evidence location. A shared Docker image tag alone SHALL NOT establish ownership. Ambiguous ownership SHALL stop automatic termination and be reported for operator attention. The factory SHALL NOT launch potentially overlapping execution while the prior execution's status remains unresolved.

#### Scenario: Encounter ambiguous container ownership

- **WHEN** available identity and artifact-location evidence cannot establish which container belongs to the repetition
- **THEN** the factory does not terminate a container based only on its image tag
- **AND** it reports the ambiguity and avoids potentially overlapping execution

### Requirement: Recheck unavailable prerequisites without retrying execution

Before accepting work for execution, the factory SHALL check shared readiness and, before launch, the selected suite's readiness. An unavailable prerequisite SHALL hold affected work without launching an evaluation, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. These checks SHALL NOT attempt to repair credentials or configuration. Holds SHALL apply to work that depends on the unavailable prerequisite.

#### Scenario: Wait for Docker or authentication

- **WHEN** Docker is unavailable or required authentication is invalid before launch
- **THEN** the factory reports the prerequisite problem and starts no affected evaluation
- **AND** it rechecks availability without consuming attempts or retries

#### Scenario: Detect a repaired prerequisite

- **WHEN** the operator fixes a prerequisite and a subsequent readiness check succeeds
- **THEN** the associated hold clears and affected work may proceed under the other admission controls without restarting the factory

### Requirement: Recover reporting independently of execution

The factory SHALL retain enough reporting progress to recover missing issue activity comments, results, and field updates independently of evaluation execution. Restarting the controller or retrying GitHub delivery SHALL NOT repeat completed evaluation work or duplicate already delivered activity. Reporting SHALL preserve later human intent subject to the explicit correction policy for status edits that contradict factory execution.

#### Scenario: Retry a failed result update

- **WHEN** evaluation results are saved but delivery to GitHub fails
- **THEN** the factory retries the missing reporting without rerunning the evaluation
- **AND** it reconciles already delivered comments and later human changes before repairing remaining updates

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

