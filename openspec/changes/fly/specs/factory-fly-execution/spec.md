## ADDED Requirements

### Requirement: Execute eval attempts in a Fly Machine

When the eval kind is configured for `fly` execution, the factory SHALL run each repetition attempt in one Fly Machine owned by the factory. The Machine SHALL obtain the Agent Runner, Agent Skills, and `agent-evals` harness sources by cloning them at the full commits recorded on the claim, at the paths the selected suite expects, and SHALL run the suite's unmodified workflow, controller, judging, and scoring inside the Machine. Each repetition SHALL use its own Machine; a recovery attempt for a repetition SHALL reuse that repetition's Machine as defined below. A failure to obtain any recorded commit SHALL end the attempt as a technical failure before model execution.

#### Scenario: Launch a repetition on Fly

- **WHEN** an eligible eval claim's repetition is admitted under `fly` execution
- **THEN** a Machine is created for that attempt and the suite runs inside it against clones at the claim's recorded Runner, Skills, and harness commits
- **AND** the suite's own source provenance and cleanliness checks pass against those clones

#### Scenario: Fail to obtain a recorded commit

- **WHEN** the Machine cannot obtain one of the recorded commits
- **THEN** the attempt ends as a technical failure without model execution and the diagnostic is preserved with the attempt

### Requirement: Establish containment before delivering anything

The Machine create request SHALL carry the factory's ownership marker, the run identity, a launch nonce, the attempt's absolute deadline, and destroy-on-exit. The Machine's initial process, set in that same request, SHALL enforce the absolute deadline independently of the factory. The factory SHALL persist the Machine identity, launch nonce, and deadline and verify that the created Machine carries them before delivering any secret. A fresh launch that finds a Machine already recorded for the attempt SHALL adopt it when it is verified and never received a secret, or destroy it before creating another, and SHALL record which. No credential, candidate-delivery token, or other secret SHALL be delivered to a Machine whose ownership has not been persisted and verified.

#### Scenario: Lose the controller between creation and recording

- **WHEN** the factory creates a Machine and stops before recording its identity
- **THEN** the Machine still destroys itself no later than its deadline
- **AND** no secret was delivered to it

#### Scenario: Lose the controller before delivery

- **WHEN** the factory has recorded a Machine but stops before delivering credentials
- **THEN** on restart the factory either continues the launch from the recorded Machine or destroys it before creating another, records which, and the Machine never held a credential in the meantime

### Requirement: Bound every Machine with one absolute deadline

Each attempt SHALL have one absolute deadline equal to its launch time plus the attempt's configured total elapsed-time limit plus a configured collection grace period. The supervisor, the Machine's in-Machine enforcement, the Machine metadata, and stale-Machine reconciliation SHALL all use that same recorded value. Existing eval limits SHALL be unchanged; Machine startup, cloning, and Runner compilation SHALL count against the attempt. When a recovery attempt starts in a surviving Machine, or when a stopped Machine is started after a quota hold, the factory SHALL record a fresh deadline computed from that attempt's start and update the Machine's enforcement and metadata before execution continues; for a stopped Machine the in-Machine enforcement SHALL carry the new deadline before the Machine is started, so it never boots against an expired one.

#### Scenario: Reach the total limit with the controller offline

- **WHEN** an attempt's total elapsed-time limit passes while the factory is offline
- **THEN** the Machine is destroyed no later than the collection grace period after that limit without factory involvement

#### Scenario: Start a recovery attempt

- **WHEN** a recovery attempt starts in a surviving Machine
- **THEN** the recorded deadline, the Machine metadata, and the in-Machine enforcement all reflect the new attempt's deadline before the suite resumes

#### Scenario: Start a stopped Machine whose old deadline has passed

- **WHEN** a stopped Machine is started after its previous deadline has passed
- **THEN** the in-Machine enforcement already holds the new deadline at boot and the Machine does not destroy itself

### Requirement: Make the Machine the durable execution identity

Ownership of a Fly attempt SHALL be verified from the recorded Machine identity, launch nonce, and metadata, never from the identity of a process on the factory host. After a controller or launcher restart, the factory SHALL reattach to a verified surviving Machine and continue supervision without creating a new attempt, consuming a recovery retry, or resetting supervision timers. A Machine whose metadata does not match the recorded attempt SHALL NOT be terminated or adopted; the factory SHALL report the mismatch with the Machine identity and the operator remedy, hold new eval launches, and treat the mismatch as resolved once that Machine no longer exists, whether by its deadline or by operator action, at which point the affected attempt SHALL settle as a lost Machine.

#### Scenario: Restart the Mac during a repetition

- **WHEN** the factory host restarts while a Machine is running a repetition
- **THEN** the factory reattaches to that Machine, the repetition continues, and no duplicate attempt starts

#### Scenario: Find a Machine that does not match

- **WHEN** the Machine recorded for an attempt exists but its metadata does not match the recorded identity and nonce
- **THEN** the factory terminates nothing, reports the mismatch with the Machine identity and remedy, and holds new eval launches until that Machine no longer exists, after which the attempt settles as a lost Machine

### Requirement: Deliver credentials from an exact allowlist after ownership

Model credentials SHALL be delivered to the Machine only from an exact per-provider file allowlist for Codex and Claude, only after ownership is persisted and verified, over an authenticated transport rather than the Machine configuration, with owner-only permissions in the Machine. Cursor credentials SHALL never be delivered. Credentials and the candidate-delivery token SHALL never appear in the Machine configuration, the artifact tree, factory logs, or the heartbeat, and SHALL be removed from the Machine before the completion marker is written. The factory SHALL never write the factory host's own credential files.

#### Scenario: Deliver credentials

- **WHEN** ownership of a Machine has been persisted and verified
- **THEN** only the allowlisted Codex and Claude files and the candidate-delivery environment are delivered, and the Machine configuration contains none of them

#### Scenario: Collect artifacts

- **WHEN** the artifact tree is collected from a completed Machine
- **THEN** it contains no delivered credential or token

### Requirement: Supervise through a live stream and heartbeat

While an attempt runs, the factory SHALL stream the Machine's suite output into the attempt's host log through a single writer and SHALL receive a heartbeat reporting changes in the suite's known progress and quota-hold signals at an interval well below the inactivity limit. Output and heartbeat content SHALL feed the existing progress, inactivity, and provider-quota behavior; relay activity itself SHALL NOT count as progress. If observation of a Machine is lost for the inactivity period while the Machine is verifiably alive, the factory SHALL treat the attempt as having reached the inactivity limit and apply same-Machine recovery rather than declaring the Machine lost.

#### Scenario: Observe a quota result from the Machine

- **WHEN** the collected result of a Fly attempt reports a recognized Codex usage limit
- **THEN** the factory records the provider hold exactly as it would for local execution

#### Scenario: Lose observation of a healthy Machine

- **WHEN** the factory cannot observe a Machine for the inactivity period and the Machine is verifiably running
- **THEN** the attempt is stopped by the inactivity limit and follows same-Machine recovery, and the Machine is not settled as lost

### Requirement: Collect the whole artifact tree once, then destroy the Machine

When the Machine writes its completion marker, the factory SHALL collect the repetition's entire artifact tree, including the suite's checkpoint, phase results, judging output, session state, and the built candidate output the human-review command serves, verify the collected tree against a file manifest the Machine wrote with its completion marker, and only then place it in the attempt's artifact directory and record the guest exit code. A collection that does not verify SHALL NOT be recorded as a result and SHALL settle the attempt as a lost Machine. When a limit or cancellation stops an attempt, the factory SHALL stop the job inside the Machine and collect before the attempt is finished. After the attempt is classified, the factory SHALL destroy the Machine unless the attempt is retained for same-Machine recovery or stopped for a quota hold as defined below. Collection SHALL be verified complete before the Machine is destroyed or stopped. If collection has not completed within the collection grace period, the Machine's own enforcement SHALL destroy it and the attempt SHALL be settled as a lost Machine. The posted human-review command SHALL work against the collected directory on the factory host without any Fly resource.

#### Scenario: Complete a repetition

- **WHEN** the suite finishes and the Machine writes its completion marker
- **THEN** the full artifact tree is present under the repetition's host artifact directory, the exit code is recorded, and once the repetition is settled the Machine no longer exists

#### Scenario: Miss the collection grace period

- **WHEN** the factory does not complete collection within the grace period after the completion marker
- **THEN** the Machine destroys itself and the attempt is settled as a lost Machine with whatever evidence was streamed

#### Scenario: Collect a partial tree

- **WHEN** the transfer of the artifact tree is interrupted so the collected files do not match the Machine's manifest
- **THEN** no result is recorded from the partial tree and the attempt is settled as a lost Machine

#### Scenario: Cancel a running repetition

- **WHEN** the issue behind a running Fly repetition is cancelled
- **THEN** the job is stopped in the Machine, the evidence so far is collected, and the Machine is destroyed

### Requirement: Resume only inside the surviving Machine

Bounded technical recovery for a Fly attempt SHALL run the suite's `--resume` inside the same Machine, as a new recorded attempt with a fresh deadline, subject to the existing recovery budget and the suite's resume checks. When the previous attempt verifiably stopped before the suite created its checkpoint and before candidate execution, recovery SHALL run the suite without `--resume` inside the same surviving Machine, consuming the ordinary retry; such a stop SHALL NOT be treated as a lost Machine. The factory SHALL NOT attempt to resume a repetition in a different Machine. When the recovery budget is exhausted, the Machine SHALL be destroyed after evidence collection and the existing exhaustion policy SHALL apply.

#### Scenario: Recover after a limit stops the suite

- **WHEN** a Fly attempt is stopped by a limit and a recovery retry remains
- **THEN** the recovery attempt resumes the suite inside the same Machine with a new attempt record and fresh deadline

#### Scenario: Recover a failure before the checkpoint existed

- **WHEN** a Fly attempt fails while cloning, building, or logging in, before the suite created its checkpoint, and a recovery retry remains
- **THEN** the recovery attempt runs the suite without `--resume` inside the same Machine and the retry is consumed

#### Scenario: Exhaust recovery on Fly

- **WHEN** the recovery attempt in a Machine also fails
- **THEN** the evidence is collected, the Machine is destroyed, and the claim follows the existing exhausted-recovery policy

### Requirement: Stop the Machine during a Codex quota hold

When a recognized Codex quota hold interrupts a Fly attempt, the factory SHALL stop the Machine with its root filesystem retained rather than destroy it, record a deadline that covers the earliest time execution can become eligible again (the later of the hold's expiry and the next admission-window opening) plus the attempt's total elapsed-time limit plus the collection grace period, refresh that deadline whenever the hold moves, and start the Machine only when execution is eligible again under the admission window and other holds. Before resuming, the factory SHALL verify that the suite's checkpoint is present in the started Machine; a missing checkpoint SHALL take the lost-Machine path. A stopped Machine SHALL count as the repetition's surviving Machine for reconciliation and status.

#### Scenario: Resume after a hold inside the window

- **WHEN** a Codex hold expires while execution is eligible
- **THEN** the stopped Machine is started, its checkpoint is verified, and the suite resumes there without consuming a recovery retry

#### Scenario: Find the checkpoint gone after a stop

- **WHEN** the started Machine no longer holds the suite's checkpoint
- **THEN** the repetition is settled as a lost Machine and the Machine is destroyed

#### Scenario: Reach reset outside the admission window

- **WHEN** a Codex hold expires outside the admission window
- **THEN** the Machine stays stopped, its recorded deadline still lies beyond the next window opening, and it is started when the window opens

### Requirement: Settle a lost Machine as a failed repetition

When a Machine is found destroyed, missing, or without a checkpoint it was known to have, or its collection cannot be verified, the factory SHALL record the repetition as failed for a factory-owned infrastructure reason, preserve the evidence already streamed, consume no recovery retry, and continue remaining repetitions in new Machines. The lost repetition SHALL NOT be rerun automatically, SHALL NOT be classified as a product failure, and SHALL NOT be offered recovery. The aggregate verdict SHALL follow the lost-repetition rule in `factory-eval-reporting`.

#### Scenario: Lose a Machine mid-run

- **WHEN** a running repetition's Machine no longer exists
- **THEN** that repetition is recorded as failed with an infrastructure reason and its streamed evidence, and the next repetition starts in a new Machine

#### Scenario: Lose every repetition

- **WHEN** all of a claim's repetitions are settled as lost Machines
- **THEN** the claim reports through the existing infrastructure-error path with each loss explained

### Requirement: Reconcile stale Machines every cycle

Each controller cycle SHALL inspect every Machine in the factory's Fly app that carries the factory's ownership marker, including Machines the local store never recorded. Any such Machine past its recorded deadline SHALL be destroyed; a Machine within its deadline but unknown to the store SHALL be left running and reported. Cleanup SHALL be idempotent, retried, and verified against Fly, and a cleanup failure SHALL be reported for operator attention rather than silently dropped.

#### Scenario: Destroy an orphan

- **WHEN** a Machine carrying the factory's marker is past its deadline and no recorded attempt owns it
- **THEN** reconciliation destroys it and records that it did so

#### Scenario: Leave an unknown Machine within its deadline

- **WHEN** a Machine carrying the factory's marker is within its deadline but no recorded attempt owns it
- **THEN** reconciliation leaves it running and reports it, and it is destroyed once its deadline passes

### Requirement: Treat in-Machine authentication failure as attempt failure

A model login failure inside the Machine SHALL end the attempt as a technical failure with its diagnostic preserved, subject to the ordinary recovery policy. It SHALL NOT modify credentials on the factory host. When a subsequent readiness check finds a factory-host model login invalid, the existing prerequisite hold SHALL apply with an explanation naming the provider.

#### Scenario: Reject a copied login in the Machine

- **WHEN** a provider rejects the delivered credential inside the Machine
- **THEN** the attempt fails as a technical failure and the factory host's credential files are unchanged

#### Scenario: Find the host login invalid afterwards

- **WHEN** the next readiness check finds the factory host's Codex or Claude login invalid
- **THEN** eval admission is held with the provider named and no attempt is launched

### Requirement: Record Machine provenance

Before model execution, the factory SHALL record the Machine identity, the immutable image digest observed on the launched Machine, and the Machine's CPU kind, CPU count, memory, and region as observed execution provenance for the attempt. A configured mutable image tag SHALL NOT be recorded in place of the observed digest.

#### Scenario: Inspect a completed Fly repetition

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the Machine identity, observed image digest, size, and region used for that attempt are identifiable
