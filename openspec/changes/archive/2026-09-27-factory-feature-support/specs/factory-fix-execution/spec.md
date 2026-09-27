## MODIFIED Requirements

### Requirement: Invoke the versioned fix workflow

The factory SHALL run the packaged fix workflow in the execution mode configured for the fix kind, `docker` or `host`, passing the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, and the attempt's artifact directory. In `docker` mode the workflow runs in the existing sandbox through the Runner sandbox script with a per-run image tag; in `host` mode it runs as described by the host execution requirement. The workflow SHALL write its outcome and any intermediate records under the supplied artifact directory and SHALL NOT assume a fixed container path. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the Runner in use does not support what the workflow requires, and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure. A running attempt SHALL keep the mode it launched with; a recovery retry SHALL launch in the currently configured mode. The factory SHALL ship the review workflow, the shared implementation sub-workflow, and the feature and define workflows beside the fix workflow and stage all of them into the Runner catalog the attempt uses, in either mode, where the Runner resolves them by name and relative path. Each workflow contract SHALL be versioned and checked the same way. A fix attempt SHALL run `factory-fix` on a fresh branch from the recorded target commit; a review attempt SHALL run `factory-review` on the PR branch at its recorded head with the attempt's `review.json` as input, and SHALL write its outcome to `review-outcome.json` under the supplied artifact directory with the same outcome values. Extracting the shared sub-workflow SHALL NOT change the `factory-fix/1` contract or the fix workflow's observable behaviour.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged fix workflow declares a compatible contract version and the Runner in use supports what it requires
- **THEN** the attempt starts under its own supervisor in the configured execution mode with the configured roles

#### Scenario: Launch in Docker mode

- **WHEN** the fix kind is configured for `docker` and the packaged workflow declares the expected contract
- **THEN** the attempt starts under its own supervisor in the sandbox with the configured roles and a run-specific image tag
- **AND** the workflow receives the sandbox's artifact mount as its artifact directory

#### Scenario: Launch in host mode

- **WHEN** the fix kind is configured for `host` and host readiness passes
- **THEN** the attempt starts under its own supervisor on the host with the configured roles and no image tag
- **AND** the workflow receives the attempt's artifact directory under the storage root and writes `fix-outcome.json` there

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged workflow declares an unsupported contract version or the Runner in use lacks a feature the workflow requires
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Launch a review attempt

- **WHEN** a review round is admitted
- **THEN** the target clone is on the PR branch at the recorded head, the staged directory holds every packaged workflow file, and the Runner runs `factory-review` with `review.json`

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy

#### Scenario: Change the mode while an attempt runs

- **WHEN** the operator changes the fix execution mode while a fix attempt is running
- **THEN** the running attempt continues in the mode it launched with
- **AND** a later recovery retry or new attempt launches in the newly configured mode
