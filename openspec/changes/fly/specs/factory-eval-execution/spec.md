## MODIFIED Requirements

### Requirement: Execute against clean pinned worktrees

The factory SHALL prepare factory-owned clean Git worktrees for the accepted Runner and Skills commits and retain the deployed `agent-evals` version in a pinned worktree. These worktrees SHALL remain associated with the claim for its lifetime, including automatic deferrals and recovery, and SHALL NOT be repointed for another claim. Repetitions SHALL use the same accepted inputs rather than re-resolving moving refs. The worktrees SHALL remain the factory-host record of the accepted inputs in every execution mode; under `fly` execution the sandbox obtains the same commits by cloning, as defined in `factory-fly-execution`.

#### Scenario: Defer a claim while source branches advance

- **WHEN** a claim waits overnight or for quota and its original source branches advance
- **THEN** its next repetition or recovery attempt uses the original pinned worktrees and accepted revisions

#### Scenario: Prepare another claim

- **WHEN** the factory prepares another claim while an earlier claim retains resumable work
- **THEN** the earlier claim's worktrees remain pinned to their accepted revisions
- **AND** the new claim does not repoint them

#### Scenario: Verify pinned source provenance in the sandbox

- **WHEN** the selected suite runs against the claim's pinned Runner and Skills revisions
- **THEN** the sandbox can verify the actual pinned source SHAs and cleanliness: under Docker by reading the mounted linked-worktree Git metadata read-only, and under Fly through clones at the recorded commits
- **AND** no execution mode mutates the shared checkouts or their Git metadata

### Requirement: Invoke the suite with accepted execution settings

For `and-scene`, the factory SHALL invoke `evals/agent-runner/and-scene/run.sh` from the retained harness worktree using its agent-execution mode. It SHALL supply the pinned Runner and Skills worktree paths, complete lead/implementor/tester profiles, the repetition's artifact directory, and required environment-file paths through the suite's supported interface. Under Docker execution the integration SHALL support the selected suite's Codex, Claude, and Cursor role adapters; under Fly execution it SHALL support Codex and Claude only. It SHALL pass the accepted `skip_validator` setting to the suite. The factory SHALL use the suite's existing workflow and execution behavior rather than implement a second evaluator. The suite SHALL run in one sandbox per repetition in the configured execution mode, with the controller and supervisor on the host. Under Docker a recovery attempt SHALL reuse the repetition's artifacts through a new container; under Fly it SHALL reuse the repetition's surviving Machine as defined in `factory-fly-execution`. Controller restart SHALL preserve verified surviving execution in every mode. Under Docker, integration SHALL verify the selected Runner sandbox launcher supports required mounts and arguments; under Fly it SHALL verify that the pinned harness invokes the factory's launcher with exactly the argument sequence the launcher supports. Missing compatibility SHALL be exposed as an actionable readiness problem before model execution.

Selected-suite readiness SHALL be verified before execution. Calibration SHALL remain an optional suite-maintainer diagnostic; the factory SHALL NOT require a calibration receipt or pass removed calibration-record arguments. Unavailable prerequisites SHALL follow the readiness-hold behavior in `factory-claim-lifecycle`.

#### Scenario: Launch an accepted repetition

- **WHEN** the claim is eligible and its prerequisites are available
- **THEN** the suite receives the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting
- **AND** the invocation uses the retained harness version

#### Scenario: Launch with Cursor role profiles under Docker

- **WHEN** the eval kind runs under Docker execution, an accepted request selects Cursor for one or more role profiles, and the host Cursor CLI is available
- **THEN** the factory passes those profiles unchanged to the selected suite
- **AND** the suite remains responsible for validating the mounted Cursor authentication at launch

#### Scenario: Fail selected-suite readiness

- **WHEN** the selected suite's required entry-point or fixture files are unavailable in the pinned environment
- **THEN** no evaluation starts and the factory reports the readiness problem without consuming an execution attempt or recovery retry

#### Scenario: Hold Fly readiness on an unexpected launcher argument

- **WHEN** the eval kind runs under Fly execution and the pinned harness would invoke the launcher with an argument the launcher does not support
- **THEN** no Machine is created, eval admission is held, and the readiness problem names the unsupported argument and the harness commit

### Requirement: Resume through the suite's supported interface

The factory SHALL resume interrupted `and-scene` work with a valid checkpoint through the suite's `--resume` interface using saved inputs and the artifact directory, subject to the lifecycle recovery budget. When reconciliation establishes that an attempt stopped before creating a checkpoint and before candidate execution, the factory SHALL retry without `--resume` using the same repetition identity, inputs, artifact directory, and remaining retry budget. A corrupt checkpoint or missing state alongside evidence of execution SHALL NOT authorize a fresh start. It SHALL respect the suite's checks of input revisions, settings, candidate identity, and evidence. A rejected resume SHALL NOT be bypassed by silently changing inputs, discarding evidence, or starting a fresh evaluation under the same repetition identity. Completed repetitions SHALL remain completed. Under Fly execution, resume SHALL run only inside the repetition's surviving Machine; a missing Machine, or a Machine without a checkpoint it was known to have created, SHALL follow the lost-Machine outcome in `factory-fly-execution` rather than the corrupt-state scenario below, while a surviving Machine whose attempt verifiably stopped before checkpoint creation follows the fresh-retry scenario below inside that Machine.

#### Scenario: Resume compatible saved work

- **WHEN** saved work is eligible for recovery and passes the suite's resume checks
- **THEN** the suite continues that repetition using the existing evidence and accepted inputs

#### Scenario: Recover an attempt that never reached checkpoint creation

- **WHEN** the prior attempt is verified stopped before checkpoint creation and candidate execution, and a technical retry remains
- **THEN** recovery invokes the suite without `--resume` using the same repetition identity, artifact directory, and frozen inputs
- **AND** it consumes the existing technical retry allowance rather than granting another repetition or unlimited retries

#### Scenario: Preserve unexplained or corrupt saved state

- **WHEN** the checkpoint is corrupt, or is missing despite evidence that candidate execution began
- **THEN** the factory preserves the evidence and reports the recovery problem without silently starting fresh

#### Scenario: Reject incompatible saved work

- **WHEN** the suite rejects resume because saved evidence or score-affecting inputs do not match
- **THEN** the factory preserves the diagnostic and existing evidence and applies its technical-failure policy
- **AND** it does not bypass the checks or substitute new inputs

### Requirement: Record the evaluated environment accurately

The factory SHALL retain the selected suite identity, full Runner and Skills commit SHAs, deployed `agent-evals` commit SHA, fixture and reference pins, rubric identities, workflow, judge profile, role settings, validator setting, and repetition count. It SHALL record the sandbox image actually used: under Docker, the actual container's immutable image ID after the image is built; under Fly, the immutable image digest observed on the launched Machine together with the Machine identity, size, and region. It SHALL NOT infer the image from a mutable tag or claim that a future image digest was known when the request was accepted. If that observation is unavailable, provenance SHALL say so rather than substitute a later tag lookup. These records SHALL distinguish requested inputs from observed execution provenance.

#### Scenario: Inspect a completed evaluation

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the tested Runner and Skills revisions and the harness, suite, scoring inputs, and execution settings used to evaluate them are identifiable
- **AND** the recorded image identifies the image actually used and, for Fly, the Machine used
