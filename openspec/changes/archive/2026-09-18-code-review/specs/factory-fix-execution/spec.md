## MODIFIED Requirements

### Requirement: Invoke the versioned fix workflow

The factory SHALL ship the fix workflow, the review workflow, and their shared implementation sub-workflow with its own package, stage them together into the attempt's artifact directory where the sandboxed Runner resolves them by name and relative path, and run the attempt's workflow in the existing sandbox through the Runner sandbox script, passing a per-run image tag, the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the attempt's input file, the attempt number, and the location of the fix credential. Both workflow contracts SHALL be versioned; the factory SHALL refuse to launch when the packaged fix or review workflow does not declare its configured contract version or when the recorded Runner commit cannot run it (its `finalize-pr` workflow does not accept the `ci_fix_cycles` parameter), and SHALL report this as a readiness problem. A fix attempt SHALL run `factory-fix` on a fresh branch from the recorded target commit; a review attempt SHALL run `factory-review` on the PR branch at its recorded head. Each workflow SHALL return exactly one structured outcome declaring its contract version, written to `fix-outcome.json` or `review-outcome.json` respectively in the attempt's artifact directory: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. Absence of a structured outcome SHALL be treated as a technical failure. Extracting the shared sub-workflow SHALL NOT change the `factory-fix/1` contract or the fix workflow's observable behaviour.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged fix workflow declares the configured contract version and the recorded Runner commit can run it
- **THEN** the attempt starts under its own supervisor with the configured roles and a run-specific image tag

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged workflow declares an unsupported contract version or the recorded Runner commit cannot run it
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Launch a review attempt

- **WHEN** a review round is admitted
- **THEN** the target clone is on the PR branch at the recorded head, the staged directory holds all three workflow files, and the Runner runs `factory-review` with `review.json`

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy
