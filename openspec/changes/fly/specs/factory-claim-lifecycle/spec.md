## MODIFIED Requirements

### Requirement: Preserve running evaluations across controller restarts

Evaluations SHALL be launched so that restarting the factory controller does not itself terminate them. After restart, the factory SHALL resume monitoring verified surviving execution, recover results from execution that finished while it was offline, or apply the recovery policy if execution itself stopped unexpectedly. A controller restart alone SHALL NOT create another execution attempt, consume a retry, or reset supervision timers. Completed repetitions SHALL NOT be rerun to recover reporting. Under Fly execution, surviving execution is the recorded Machine, and the factory SHALL reattach to it as defined in `factory-fly-execution`.

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

#### Scenario: Restart with a surviving Machine

- **WHEN** the controller restarts while a Fly attempt's recorded Machine is still running and its metadata matches the recorded attempt
- **THEN** the factory adopts that Machine and continues supervision without a new attempt, a consumed retry, or reset timers

### Requirement: Bound technical recovery per repetition

The factory SHALL allow one automatic recovery retry per repetition after a technical execution failure, using supported suite resume behavior and preserving completed repetitions. It SHALL record execution attempts and consumed retries durably. A completed evaluation with a poor product result SHALL NOT trigger a technical retry. A suite-confirmed non-resumable implementation-workflow failure SHALL settle that repetition as failed without a retry and SHALL allow remaining repetitions to proceed. Resumable workflow failures and recoverable harness failures SHALL follow the bounded technical recovery policy. The factory SHALL preserve the suite's failure owner, code, and resumable value and SHALL NOT infer non-resumability from the workflow-failed status alone. Recognized quota waiting, unavailable prerequisites detected before execution, and waiting for an admission window SHALL NOT consume this retry budget. Under Fly execution, a lost Machine SHALL settle that repetition as failed for a factory-owned infrastructure reason without consuming a retry and SHALL allow remaining repetitions to proceed, as defined in `factory-fly-execution`.

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

### Requirement: Wait for provider quota without discarding progress

On a detected Codex usage limit, including during product judging, the factory SHALL preserve claim progress and persist an admission hold until the reliably interpreted reset time reported by the CLI. Only after recognizing a Codex rate-limit diagnostic, if no reset time can be interpreted reliably, it SHALL use a configurable default of five hours from detection. A generic execution error or an absent reset time alone SHALL NOT create a quota hold; generic errors SHALL follow ordinary failure recovery. The hold SHALL survive controller restarts. After it expires, execution SHALL remain subject to the admission window, pause state, and other applicable prerequisites. Under Fly execution, the hold SHALL stop the repetition's Machine with its root filesystem retained and start it when execution is eligible, as defined in `factory-fly-execution`.

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

#### Scenario: Hold a Fly attempt for Codex quota

- **WHEN** a Fly attempt's streamed output reports a Codex usage limit
- **THEN** the Machine is stopped with its disk retained, the hold is recorded, and the Machine is started and the suite resumed in place when execution is eligible without consuming a retry

### Requirement: Verify execution ownership before termination

Before terminating execution for cancellation, timeout, or recovery cleanup, the factory SHALL verify that the process, container, or Machine belongs to the recorded attempt using its recorded identity and associated evidence location. A Docker sandbox attempt SHALL be verified through its process and container; a host attempt SHALL be verified through its recorded process identity alone and no container evidence SHALL be expected for it; a Fly attempt SHALL be verified through its recorded Machine identity, launch nonce, and metadata, and no host process or container evidence SHALL be expected for it. A shared Docker image tag alone SHALL NOT establish ownership. Ambiguous ownership SHALL stop automatic termination and be reported for operator attention. The factory SHALL NOT launch potentially overlapping execution while the prior execution's status remains unresolved.

#### Scenario: Encounter ambiguous container ownership

- **WHEN** available identity and artifact-location evidence cannot establish which container belongs to the repetition
- **THEN** the factory does not terminate a container based only on its image tag
- **AND** it reports the ambiguity and avoids potentially overlapping execution

#### Scenario: Find a host process that no longer matches

- **WHEN** the process recorded for a host attempt is gone or its identity no longer matches the recorded attempt
- **THEN** the factory terminates nothing, treats the attempt's execution as unverified, and resolves it through reconciliation before launching new work of that kind

#### Scenario: Find a Machine that no longer matches

- **WHEN** the Machine recorded for a Fly attempt exists but its metadata does not carry the recorded identity and launch nonce
- **THEN** the factory terminates nothing, reports the mismatch, and resolves it through reconciliation before launching new eval work

### Requirement: Recheck unavailable prerequisites without retrying execution

Before accepting work for execution, the factory SHALL check the readiness that applies to that kind and, before launch, the selected suite's or mode's readiness. Readiness checks SHALL be classified as shared, eval, eval-sandbox, eval-fly, fix-sandbox, or fix-host, and only the classes that apply to a kind under its configured execution mode SHALL hold that kind. The eval class SHALL cover the mode-neutral eval prerequisites, namely harness branch resolution, the factory host's Codex and Claude logins, the suite environment file, and free disk space, and SHALL hold the eval kind under every execution mode. The eval-sandbox class SHALL cover only Docker availability, the Docker launcher, and the memory allowance. The eval-fly class SHALL cover Fly API access with the configured deploy token, the configured app, a resolvable configured image, the SSH transport the factory uses for delivery and collection, and the absence of a Cursor role on the claim being admitted. An unavailable prerequisite SHALL hold affected work without launching an attempt, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. A recorded hold whose check no longer applies to a kind after a configuration change SHALL clear on the next successful poll. These checks SHALL NOT attempt to repair credentials or configuration.

#### Scenario: Wait for Docker or authentication

- **WHEN** Docker is unavailable or required authentication is invalid before launch
- **THEN** the factory reports the prerequisite problem and starts no affected attempt
- **AND** it rechecks availability without consuming attempts or retries

#### Scenario: Detect a repaired prerequisite

- **WHEN** the operator fixes a prerequisite and a subsequent readiness check succeeds
- **THEN** the associated hold clears and affected work may proceed under the other admission controls without restarting the factory

#### Scenario: Switch fixes to the host under a Docker hold

- **WHEN** a Docker hold is recorded for both kinds and the operator changes the fix kind to host execution
- **THEN** the next successful poll clears the fix hold, evaluates host readiness for fixes, and leaves the eval hold in place

#### Scenario: Lose Fly access

- **WHEN** the eval kind runs under Fly execution and the Fly API is unreachable or rejects the deploy token
- **THEN** eval admission is held with the Fly problem named, no Machine is created, and no attempt or retry is consumed

#### Scenario: Hold a frozen Cursor claim under Fly

- **WHEN** a claim with a frozen Cursor role profile is next in line and the eval kind runs under Fly execution
- **THEN** that claim is held with the Cursor role named, its frozen inputs are unchanged, and other eligible eval claims may proceed

### Requirement: Classify admission holds by scope

Pause SHALL apply to every kind. The free-disk floor SHALL apply to every kind using the floor configured for the kind being admitted. Docker availability and the memory headroom check SHALL apply only to kinds whose configured execution mode is Docker. Fly readiness SHALL hold only eval work configured for Fly execution. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential, and in host mode the host prerequisites) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.

#### Scenario: Hold Codex while fixing with Cursor

- **WHEN** a Codex quota hold is active and the fix roles use Cursor
- **THEN** an eligible bug can still be admitted while eval work using Codex waits

#### Scenario: Lose suite readiness

- **WHEN** the eval suite's prerequisites are unavailable
- **THEN** eval admission is held and fix admission is unaffected

#### Scenario: Stop Docker while fixes run on the host

- **WHEN** Docker is unavailable and the fix kind is configured for host execution
- **THEN** an eligible bug is admitted and eval admission is held with Docker named as the reason

#### Scenario: Apply the fix disk floor

- **WHEN** free space is below the eval floor but at or above the lower floor configured for fixes
- **THEN** an eligible bug is admitted and eval admission is held for disk

#### Scenario: Run without Docker

- **WHEN** Docker is stopped, the eval kind is configured for Fly execution, and the fix kind is configured for host execution
- **THEN** both an eligible eval and an eligible bug are admitted subject to their other holds and Docker is not named as a reason for either

### Requirement: Check memory headroom before admission

Before admitting an attempt that will run in the Docker sandbox, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check. An attempt that will run on the host or in a Fly Machine SHALL NOT be subject to the Docker memory probe and SHALL be admitted without it.

#### Scenario: Admit a second attempt with headroom

- **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
- **THEN** the second kind's attempt may be admitted

#### Scenario: Wait for memory

- **WHEN** the remaining Docker allowance is below the reservation
- **THEN** no new sandbox attempt starts and status names memory as the blocking condition

#### Scenario: Admit a host fix while Docker is stopped

- **WHEN** the fix kind is configured for host execution and Docker is not running
- **THEN** the memory probe is not performed for the fix and the bug is admitted subject to the other holds

#### Scenario: Admit a Fly eval while Docker is stopped

- **WHEN** the eval kind is configured for Fly execution and Docker is not running
- **THEN** the memory probe is not performed for the eval and it is admitted subject to the other holds
