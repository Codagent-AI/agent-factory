## MODIFIED Requirements

### Requirement: Classify admission holds by scope

Pause SHALL apply to every kind. The free-disk floor SHALL apply to every kind using the floor configured for the kind being admitted. Docker availability and the memory headroom check SHALL apply only to kinds whose configured execution mode uses the sandbox. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential, and in host mode the host prerequisites) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.

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

### Requirement: Check memory headroom before admission

Before admitting an attempt that will run in the sandbox, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check. An attempt that will run on the host SHALL NOT be subject to the Docker memory probe and SHALL be admitted without it.

#### Scenario: Admit a second attempt with headroom

- **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
- **THEN** the second kind's attempt may be admitted

#### Scenario: Wait for memory

- **WHEN** the remaining Docker allowance is below the reservation
- **THEN** no new sandbox attempt starts and status names memory as the blocking condition

#### Scenario: Admit a host fix while Docker is stopped

- **WHEN** the fix kind is configured for host execution and Docker is not running
- **THEN** the memory probe is not performed for the fix and the bug is admitted subject to the other holds

### Requirement: Recheck unavailable prerequisites without retrying execution

Before accepting work for execution, the factory SHALL check the readiness that applies to that kind and, before launch, the selected suite's or mode's readiness. Readiness checks SHALL be classified as shared, eval-sandbox, fix-sandbox, or fix-host, and only the classes that apply to a kind under its configured execution mode SHALL hold that kind. An unavailable prerequisite SHALL hold affected work without launching an attempt, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. A recorded hold whose check no longer applies to a kind after a configuration change SHALL clear on the next successful poll. These checks SHALL NOT attempt to repair credentials or configuration.

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

### Requirement: Verify execution ownership before termination

Before terminating execution for cancellation, timeout, or recovery cleanup, the factory SHALL verify that the process or container belongs to the recorded attempt using its recorded identity and associated evidence location. A sandbox attempt SHALL be verified through its process and container; a host attempt SHALL be verified through its recorded process identity alone and no container evidence SHALL be expected for it. A shared Docker image tag alone SHALL NOT establish ownership. Ambiguous ownership SHALL stop automatic termination and be reported for operator attention. The factory SHALL NOT launch potentially overlapping execution while the prior execution's status remains unresolved.

#### Scenario: Encounter ambiguous container ownership

- **WHEN** available identity and artifact-location evidence cannot establish which container belongs to the repetition
- **THEN** the factory does not terminate a container based only on its image tag
- **AND** it reports the ambiguity and avoids potentially overlapping execution

#### Scenario: Find a host process that no longer matches

- **WHEN** the process recorded for a host attempt is gone or its identity no longer matches the recorded attempt
- **THEN** the factory terminates nothing, treats the attempt's execution as unverified, and resolves it through reconciliation before launching new work of that kind

### Requirement: Enforce separate progress and runtime limits

Each execution attempt SHALL have configurable limits with defaults of 30 minutes without progress, six hours of execution excluding recognized bounded quota waits, and 12 hours of total elapsed time including those waits. Progress SHALL include activity in known suite logs and saved state as well as subprocess output, and for a host attempt activity in its recorded Runner session directory. Recognized bounded quota waits SHALL suspend inactivity detection and be excluded from the execution-time budget, but SHALL count toward the total elapsed-time limit. Timing history SHALL survive controller restarts. Waiting between repetitions SHALL NOT count toward an attempt's timers.

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
