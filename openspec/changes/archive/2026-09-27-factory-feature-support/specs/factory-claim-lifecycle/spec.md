## MODIFIED Requirements

### Requirement: Keep lifecycle behavior independent of work kind

Core scheduling, claim/run persistence, retries, cancellation, restart recovery, and durable reporting SHALL remain independent of Codagent identities and eval-specific parsing or scoring. Work kinds SHALL supply request interpretation, execution planning, result interpretation, report presentation, admission-time input resolution, kind-specific readiness checks, and the kind's fresh-attempt and unblock gestures through one handler interface that the controller and runtime use for every kind. The eval handler SHALL own repetition semantics and suite integration; the pull-request handler, configured per kind for fix and feature work, SHALL own eligibility for its issue type, branch resolution, workflow invocation, and outcome mapping. The generic core SHALL NOT interpret a score out of 70, Runner/Skills fields, an `and-scene` command path, a pull-request outcome, or the blocked state's label. Extracting this interface from the existing eval-shaped controller SHALL preserve all existing eval behavior.

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

The factory SHALL execute at most one attempt per work kind at a time across the service and manual execution commands: one eval repetition, one fix attempt, and one feature attempt. Per-kind slots SHALL be enforced atomically in SQLite so two admission paths cannot both reserve the same kind's slot. The factory SHALL reconcile saved execution records with surviving processes, containers, and evidence before dispatching new work of any kind. A blocked fix or feature claim SHALL NOT occupy a slot. Status and pause controls SHALL remain usable while execution is active.

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

#### Scenario: Run a feature beside a fix and an eval

- **WHEN** an eval repetition and a fix attempt are running and an eligible feature is admitted
- **THEN** the feature attempt starts in its own slot and the other attempts continue unaffected

### Requirement: Correct status edits that contradict factory execution

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. A fix claim blocked by bug triage, or a feature claim blocked during definition, SHALL be an exception: its card remains in Running with the `needs-input` label without execution and SHALL NOT be moved to a queued status; such a blocked card moved to Review or Done SHALL be restored to Running, while a blocked card moved to Ready SHALL be treated as its handler's unblock gesture. A fix or feature claim blocked by a review round's `needs-input` SHALL instead remain in Review with the `needs-input` label; moved to Running or Done it SHALL be restored to Review, and moved to Ready it SHALL be treated as the fresh-claim gesture. Moving a request with verified active execution to Ready, Review, or Done SHALL restore Running and continue that same execution without restarting it or admitting overlapping work. Such status changes SHALL NOT cancel execution. Issue closure SHALL retain its defined cancellation behavior and take precedence over restoring Running. A drag that the request's work-kind handler recognizes as a fresh-attempt gesture SHALL be honored rather than corrected.

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

#### Scenario: Move a fix blocked by a review round

- **WHEN** a human moves a fix card blocked by a review round's `needs-input` from Review to Running or Done while its issue remains open
- **THEN** the factory restores Review, keeps the `needs-input` label, and comments once on the correction

#### Scenario: Leave a blocked feature in Running

- **WHEN** a feature claim stopped during definition and its card sits in Running with the `needs-input` label
- **THEN** the poll does not move the card or post a correction
- **AND** a human moving the card to Ready is treated as the unblock gesture

### Requirement: Bound technical recovery per repetition

The factory SHALL allow one automatic recovery retry per repetition after a technical execution failure, using supported suite resume behavior and preserving completed repetitions. It SHALL record execution attempts and consumed retries durably. A completed evaluation with a poor product result SHALL NOT trigger a technical retry. A suite-confirmed non-resumable implementation-workflow failure SHALL settle that repetition as failed without a retry and SHALL allow remaining repetitions to proceed. Resumable workflow failures and recoverable harness failures SHALL follow the bounded technical recovery policy. The factory SHALL preserve the suite's failure owner, code, and resumable value and SHALL NOT infer non-resumability from the workflow-failed status alone. Recognized quota waiting, unavailable prerequisites detected before execution, and waiting for an admission window SHALL NOT consume this retry budget. Under Fly execution, a lost Machine SHALL settle that repetition as failed for a factory-owned infrastructure reason without consuming a retry and SHALL allow remaining repetitions to proceed, as defined in `factory-fly-execution`.

A pre-suite failure SHALL NOT consume the recovery retry. A pre-suite failure is one identified by an explicit launch-stage signal, never by elapsed time: a readiness or planning error raised after the attempt was reserved but before its process started; a failure of the attempt's process to start; under Fly execution, a failure of the per-claim image build, a launcher transport failure before the job was delivered to the Machine, or a job failure before the factory's job script wrote the setup-complete marker defined in `factory-fly-execution`. A failure after the suite or workflow process has started, including a model login failure, SHALL NOT be a pre-suite failure. On a pre-suite failure the factory SHALL record the failed attempt with its diagnostic, SHALL under Fly execution destroy any Machine that attempt created before anything relaunches (a recovery attempt's retained Machine SHALL remain retained), and SHALL hold the claim waiting with `infra-error` and an explanation. On a subsequent poll where readiness passes, the factory SHALL relaunch the same unit of work with the same recovery status it had, so a first attempt relaunches as a first attempt and a recovery attempt relaunches as that recovery attempt. A second consecutive pre-suite failure of the same unit SHALL stop the claim exactly as an exhausted recovery does. This SHALL apply to eval, fix, and feature claims alike.

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

#### Scenario: Lose a Fly Machine

- **WHEN** repetition 2's Machine is found destroyed or without its checkpoint in a request for three repetitions
- **THEN** repetition 2 is recorded as failed for an infrastructure reason with its streamed evidence, no retry is consumed, and repetition 3 starts in a new Machine

#### Scenario: Relaunch after a pre-suite failure

- **WHEN** repetition 1's first attempt fails because the launcher could not deliver the job to its Machine
- **THEN** the attempt is recorded as failed with its diagnostic, its Machine is destroyed, the claim waits with `infra-error`, and no recovery retry is consumed
- **AND** on the next poll where readiness passes, repetition 1 launches again as a first attempt in a new Machine

#### Scenario: Stop after two pre-suite failures

- **WHEN** the relaunched attempt of the same repetition also fails before the suite starts
- **THEN** the claim stops as for an exhausted recovery and the issue moves to Review with `infra-error` and both diagnostics

#### Scenario: Fail after the suite started

- **WHEN** an attempt fails after the job script wrote the setup-complete marker
- **THEN** the failure follows the ordinary bounded technical recovery policy and is not treated as pre-suite

#### Scenario: Fail a fix attempt before its process starts

- **WHEN** a fix attempt fails with a readiness error raised after the attempt was reserved
- **THEN** its recovery attempt is not consumed and the fix relaunches as the same attempt once readiness passes

### Requirement: Exempt settled work from closure cancellation

Issue closure SHALL cancel only claims with unfinished execution. A settled fix or feature claim with a recorded pull request SHALL remain eligible for its post-merge sync whether the issue was closed by a human or by the factory after a successful sync, and closure SHALL NOT be reported as a cancellation for such a claim.

#### Scenario: Close a fixed issue by hand before the sync

- **WHEN** a human closes an issue whose fix or feature claim is settled with a merged PR before the factory has synced
- **THEN** the claim is not cancelled and the sync still runs once

#### Scenario: Observe the factory's own closure

- **WHEN** the factory closed the issue after a successful sync and observes the closed issue on a later poll
- **THEN** it records nothing new and posts no cancellation comment
