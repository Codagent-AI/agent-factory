## MODIFIED Requirements

### Requirement: Invoke the suite with accepted execution settings

For `and-scene`, the factory SHALL invoke `evals/agent-runner/and-scene/run.sh` from the retained harness worktree using its agent-execution mode. It SHALL supply the pinned Runner and Skills worktree paths, complete lead/implementor/tester profiles, the complete crosscheck profile when the claim's frozen settings include one, the repetition's artifact directory, and required environment-file paths through the suite's supported interface. Under Docker execution the integration SHALL support the selected suite's Codex, Claude, and Cursor role adapters; under Fly execution it SHALL support Codex and Claude only. It SHALL pass the accepted `skip_validator` setting to the suite. The factory SHALL use the suite's existing workflow and execution behavior rather than implement a second evaluator. The suite SHALL run in one sandbox per repetition in the configured execution mode, with the controller and supervisor on the host. Under Docker a recovery attempt SHALL reuse the repetition's artifacts through a new container; under Fly it SHALL reuse the repetition's surviving Machine as defined in `factory-fly-execution`. Controller restart SHALL preserve verified surviving execution in every mode. Under Docker, integration SHALL verify the selected Runner sandbox launcher supports required mounts and arguments; under Fly it SHALL verify that the pinned harness invokes the factory's launcher with an argument set the launcher supports. The launcher SHALL accept the harness's credential-mount flags in any order, because the harness emits them in role order and that order varies with the configured role CLIs. The Fly compatibility check SHALL exercise a role selection that produces the widest credential-mount shape the harness can emit, including a crosscheck profile when the pinned harness accepts one, so that an ordering or grammar mismatch is detected before model execution rather than during an attempt. Missing compatibility SHALL be exposed as an actionable readiness problem before model execution.

Selected-suite readiness SHALL be verified before execution. Readiness SHALL NOT check for any particular Agent Runner workflow file, workflow name, or workflow step; the suite selects and verifies its evaluated workflow in its own preflight, before any model call. Calibration SHALL remain an optional suite-maintainer diagnostic; the factory SHALL NOT require a calibration receipt or pass removed calibration-record arguments. Unavailable prerequisites SHALL follow the readiness-hold behavior in `factory-claim-lifecycle`.

#### Scenario: Launch an accepted repetition

- **WHEN** the claim is eligible and its prerequisites are available
- **THEN** the suite receives the saved role profiles, including the crosscheck profile when one is frozen, component worktree paths, artifact directory, environment paths, and validator setting
- **AND** the invocation uses the retained harness version

#### Scenario: Launch with Cursor role profiles

- **WHEN** the eval kind runs under Docker execution, an accepted request selects Cursor for one or more role profiles, and the host Cursor CLI is available
- **THEN** the factory passes those profiles unchanged to the selected suite
- **AND** the suite remains responsible for validating the mounted Cursor authentication at launch

#### Scenario: Fail selected-suite readiness

- **WHEN** the selected suite's required entry-point or fixture files are unavailable in the pinned environment
- **THEN** no evaluation starts and the factory reports the readiness problem without consuming an execution attempt or recovery retry

#### Scenario: Hold Fly readiness on an unexpected launcher argument

- **WHEN** the eval kind runs under Fly execution and the pinned harness would invoke the launcher with an argument the launcher does not support
- **THEN** no Machine is created, eval admission is held, and the readiness problem names the unsupported argument and the harness commit

#### Scenario: Launch a claim frozen before the crosscheck role

- **WHEN** a claim admitted before configured eval defaults included `crosscheck` has no frozen crosscheck profile
- **THEN** the factory invokes its retained harness with only the lead, implementor, and tester profiles, as before
- **AND** it does not add a crosscheck profile from current defaults

#### Scenario: Pinned harness does not accept a frozen crosscheck profile

- **WHEN** a claim's frozen settings include a crosscheck profile but its retained harness does not accept `--crosscheck-cli`
- **THEN** no evaluation starts, eval admission is held, and the readiness problem names the crosscheck option and the harness commit

#### Scenario: Runner revision lacks the suite's default workflow

- **WHEN** the selected Agent Runner revision does not contain the workflow the suite evaluates
- **THEN** factory readiness does not inspect Runner workflow files, and the suite's preflight rejects the revision before any model call
