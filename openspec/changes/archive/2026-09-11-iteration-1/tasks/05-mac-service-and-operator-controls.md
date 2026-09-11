# Task: Deliver Mac service setup and complete operator controls

## Goal

Make the completed factory installable and operable as a per-user Mac service, with truthful diagnostics/status and a verified public CLI journey through controls and reporting.

## Background

All repository-relative code paths below are in `agent-factory` unless a sibling repository is explicitly named. Planning sources are `openspec/changes/iteration-1/proposal.md`, `design.md`, the cited files under `specs/`, and `test-plan.md` in that same change directory. Read the relevant design sections and the approved test plan; the excerpts below preserve the requirements and assigned automated obligations verbatim.

The repository begins as a Python 3.12/uv scaffold: `src/agent_factory/__init__.py`, `pyproject.toml`, `README.md`, and an empty `tests/` tree. The package areas named below are intended implementation locations, not claims that an API already exists. Use small protocols and dataclasses with static registration for only `eval` and `and-scene`. Preserve GitHub intent, SQLite execution history, and suite-owned evidence as separate authorities. Keep organization names, logical field mappings, local paths, and defaults configurable. Do not add more work kinds, suites, workers, queue/storage providers, a dynamic plugin loader, a generic outbox, or another evaluator.

The App, native Eval type, and board were already provisioned and confirmed; reuse the records in `openspec/changes/iteration-1/setup/`. That prior confirmation satisfies the pre-controller board-layout gate and is not evidence of implementation acceptance. Public configuration and documentation must use portable paths and contain no private credentials.

Read the design sections “Configuration and credentials”, “Controller cycle and admission”, “Migration and Deployment”, and the accepted operational specification. Work in `src/agent_factory/cli.py`, the shared/local configuration and readiness interfaces, `README.md`, `pyproject.toml`, `config/local.example.toml`, `docs/installation.md`, and `docs/operations.md`. Consume the existing production controller, store, supervisor, eval handler, and and-scene adapter; fill operational wiring/diagnostic gaps without creating another admission loop or bypass path. The installed CLI and resident entry point are the single public surface used by service and tests.

Deliver a LaunchAgent template at `packaging/launchd/com.codagent.agent-factory.plist` and reproducible per-user installation instructions with explicit executable, config, root, log, and private credential-file paths. Start on user login and restart the controller on crash. Only the controller is the permanent LaunchAgent. Restart operations must preserve the independent supervisor/session and immutable Python environment of active attempts, with file-backed logs rather than controller-owned pipes. Poll every five minutes independently of suite duration. Verify plist syntax and executable/config path rendering; actual launchd restart with the real suite remains separate acceptance.

Complete `doctor` using shared GitHub authentication/permission/field checks and work-kind/suite-supplied checks for repositories/worktrees, selected entry point/fixtures/launcher, Docker, model authentication, token environment files, and configured free-space floor. Explain each failed prerequisite and required operator action, distinguishing generic and selected-suite readiness. Launch no evaluation and repair no credentials/configuration. Verify the chosen full deployed harness SHA has the separately delivered score-failure contract and calibration-gate removal; never silently use HEAD, add a receipt gate, or implement the separate suite scoring change. Ensure an unavailable prerequisite can clear through ordinary subsequent readiness rechecks.

Complete saved-state `status` with current issue, claim/unit/attempt progress, pause, window, quota, readiness, cleanup errors, and unfinished reporting. Show next permitted start only when known; never invent a date for an operator action. `tick` invokes the same real cycle with the same single-execution and admission guards. `pause` persists across command/service restarts and permits only the running unit to finish; `resume` clears only pause. Recheck controls before every unit/recovery attempt; defaults remain 00:00–15:00 local admission, 30-minute inactivity, six-hour execution excluding bounded waits, 12-hour total, and five-hour recognized-Codex fallback, all configurable.

Document Python 3.12/uv and versioned installation, configured source repositories, full harness pin, shared/local TOML separation, explicit coordinated local/workflow revision updates, App permissions/key-file protection and Actions secrets, separate suite candidate credentials, Docker startup, model authentication and browser-proof prerequisites, login behavior, idle-sleep prevention, status/tick/pause/resume/doctor, controller restart, evidence paths, storage inspection, worktree lifecycle, and human-review handoff. Use portable public paths and the Codagent example; distinguish configuring supported behavior from adding unsupported kinds/suites. Reuse the App, board, Eval type, and recorded confirmation rather than reprovisioning them.

Document rollout order: shared routing code at the pinned revision before source callers on their default branches; label/template/caller setup through the normal repository deployment process; explicit local install/configuration and selected suite prerequisites before acceptance. Pause admission during install/update. Retain active supervisor environments, frozen worktrees, SQLite, credentials, and evidence; an incompatible older executable must not open a newer schema. Exclusive schema work waits for active writers. No automatic configuration fetch, runtime harness update, or destructive rollback is introduced. Deliver setup/service assets and instructions; do not claim that a static template or setup record proves live deployment acceptance.

Scope of shared requirements: own final installed command/diagnostic composition, service packaging and operator documentation, and the complete installed-CLI control/reporting journey. Configuration semantics, lifecycle decisions, supervision, suite integration, and worktree cleanup are consumed and regression-verified through public entry points, not reimplemented. Preserve all acceptance setup/evidence requirements without asking implementors to perform live acceptance.

## Spec

The following requirement blocks are copied verbatim from the approved specifications. For shared requirements, the Background states this delivery unit’s portion; retain the complete scenario semantics.

Source: `openspec/changes/iteration-1/specs/factory-operations/spec.md`.

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules, request labels, field and option mappings, repository locations, evaluation defaults, schedule, supervision limits, minimum free disk space, and local storage paths. It SHALL supply Codagent as an example deployment configuration. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific repository and executable locations SHALL be supplied to the suite integration rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers or additional work-kind implementations.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the initial Eval request behavior and the field names, options, and defaults described by this change

### Requirement: Apply shared deployment changes through explicit updates

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, and the deployed harness full commit SHA. The harness pin SHALL NOT default silently to a source checkout's current HEAD or remote default branch. Machine-specific paths, schedule, execution limits, and credential-file locations SHALL be configured separately in local TOML. Secret values SHALL remain outside the versioned deployment configuration.

The installed factory SHALL use the shared configuration from its explicitly installed version and SHALL NOT automatically fetch configuration changes from main. Reusable routing workflows SHALL use shared configuration from their explicitly pinned factory revision. Deployment instructions SHALL cover updating the local factory and the caller workflows' routing revision together. Updating configuration SHALL NOT mutate frozen inputs or the execution configuration of an already-running attempt.

#### Scenario: Edit shared configuration in GitHub

- **WHEN** shared deployment configuration changes on main but the local factory has not been explicitly updated
- **THEN** the installed factory continues using its installed configuration
- **AND** ordinary queue polling and operational controls continue without waiting for a software update

#### Scenario: Deploy a shared configuration change

- **WHEN** the operator explicitly updates the local installation and routing workflow pins to the intended factory revision
- **THEN** subsequent routing and new claims use that revision's shared deployment configuration
- **AND** existing claims retain frozen inputs and already-running attempts retain their execution configuration

### Requirement: Run as a recoverable per-user Mac service

The change SHALL provide a launchd LaunchAgent configuration and setup instructions that start the factory for the configured Mac user on login and restart the controller if it crashes. Execution SHALL use explicit executable, configuration, and credential paths suitable for the service environment. Controller restarts SHALL preserve running evaluations as required by `factory-claim-lifecycle`; restarting the controller SHALL NOT itself restart or terminate those evaluations.

The service SHALL poll GitHub every five minutes while independently supervising active execution. A long-running repetition SHALL NOT block queue reconciliation, cancellation checks, or pending report delivery.

#### Scenario: Start the user service

- **WHEN** the configured Mac user logs in with the LaunchAgent installed and prerequisites available
- **THEN** the factory starts using its configured paths and credentials and begins normal polling

#### Scenario: Recover a controller crash

- **WHEN** the controller crashes during an evaluation
- **THEN** launchd restarts the controller and it reconciles the surviving evaluation without terminating it merely because of the controller restart

#### Scenario: Poll during a long evaluation

- **WHEN** an evaluation runs for several hours
- **THEN** the service continues its five-minute GitHub checks and pending reporting independently of that evaluation

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, Docker availability, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against the configured minimum. It SHALL distinguish available prerequisites from problems needing operator action and explain each failed check. Diagnosis SHALL NOT launch an evaluation or attempt to repair credentials or configuration.

Shared diagnostics SHALL remain distinct from checks supplied by the selected work kind and suite. For the initial suite, setup and integration validation SHALL confirm the separately delivered automated-score failure contract described in `factory-eval-reporting`; implementing that suite behavior remains outside this change.

#### Scenario: Diagnose an unavailable prerequisite

- **WHEN** the operator runs doctor with Docker stopped, invalid required authentication, or an invalid Project mapping
- **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an evaluation

#### Scenario: Diagnose suite readiness

- **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
- **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites

### Requirement: Expose current operational status

`agent-factory status` SHALL show current work, repetition progress for eval claims, pause state, blocking conditions, and the next permitted start time when it can be determined. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, an unavailable prerequisite, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation and pending reporting, and MAY start eligible work. It SHALL apply the same admission window, pause state, prerequisite and quota holds, and single-execution guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work, no active execution, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside the admission window, while paused, or while another execution is active
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work and reporting

### Requirement: Persist pause and enforce configured admission controls

`agent-factory pause` SHALL save a factory-wide pause while allowing the current repetition to finish. `agent-factory resume` SHALL clear that pause without clearing unrelated usage or prerequisite holds. The saved pause SHALL survive controller restarts. Under the default schedule, each repetition or recovery attempt SHALL start only from 00:00 up to but not including 15:00 local time. Already-running attempts SHALL follow the lifecycle limits rather than stop solely because the window closes.

Configuration SHALL support the approved default limits of 30 minutes without progress, six hours of execution excluding recognized quota waits, 12 hours total per attempt, and a five-hour fallback for Codex reset holds. These values SHALL be configurable. The configured free-space minimum SHALL be checked before admission; insufficient space SHALL hold new affected work without consuming an execution retry.

#### Scenario: Resume while another hold remains

- **WHEN** the operator resumes a paused factory while a usage hold remains active
- **THEN** the pause clears but execution waits until the usage hold and other admission conditions permit it

#### Scenario: Run below the free-space minimum

- **WHEN** free disk space is below the configured minimum before admission
- **THEN** the factory starts no affected evaluation, reports the storage problem, and rechecks readiness without consuming a recovery retry

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees, and evaluation artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations.

Iteration 1 SHALL retain evidence and candidate outputs until manual cleanup. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, and perform operator-managed cleanup while preserving work still needed for execution, recovery, or human review. Automatic evidence and candidate-output pruning is outside this change; factory-owned worktrees follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees, and artifacts
- **AND** commands and result reports identify the actual paths in use

#### Scenario: Retain evidence after handoff

- **WHEN** an evaluation is handed off for human review
- **THEN** its artifacts and required suite files remain available for the posted review command until the reviewed item moves to Done; evidence remains retained after worktree cleanup

### Requirement: Document installation and service operation

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, Docker startup, model authentication, GitHub routing and board permissions, suite prerequisites, explicit service paths, login behavior, and preventing idle sleep. Documentation SHALL explain doctor, status, tick, pause, resume, service installation and restart, evidence locations, and the human-review handoff.

Credentials for the suite's candidate branch and draft-PR operations SHALL remain separately configured from board/routing credentials. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

#### Scenario: Set up the supported deployment

- **WHEN** an operator follows the installation instructions with the required credentials and separately delivered suite behavior available
- **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution, inspect progress, pause and resume work, and run a posted human-review command

#### Scenario: Reuse the public example

- **WHEN** another organization follows the public setup documentation
- **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
- **AND** the documentation clearly identifies any additional handler or suite implementation needed for different work behavior

## Test Plan

Own `E2E-003` through installed CLI subprocesses with real files/processes/persistence, an isolated root, a local GitHub stub, and a controlled suite executable writing representative artifacts. Include two queued cards, a repaired prerequisite, pause surviving command exit, unrelated holds surviving resume, manual reordering, active Ready and Done corrections, closure cancellation, and interrupted final delivery. Use short configurable intervals and a controlled test clock rather than waiting overnight or spending quota. Add focused checks of LaunchAgent syntax/rendered paths and packaging/entry-point installation. Run the complete collected automated integration/E2E suite as implementation verification, preserving its separate macOS and Docker requirements; this does not transfer ownership of the other obligations or authorize live acceptance.

Use implementation-time TDD for the copied specification scenarios within the scope stated above. Keep unit cases close to the behavior rather than reproducing every case at every test layer. Use isolated roots, temporary SQLite/files, and controlled external responses; automated verification must not read the operator's database, live queue, or private credentials. Run the relevant collected pytest tests and the repository's Ruff format/lint and strict Pyright checks. Register any markers in `pyproject.toml`; pytest exit code 5 or an unexecuted required platform test does not count as passing.

Implementors do not execute `AT-001`, `AT-002`, or `HT-001`. Preserve their requirements in `openspec/changes/iteration-1/test-plan.md` for the separate acceptance stage: real issue-event routing, one real suite repetition and launchd restart, and the operator's board-drag-to-API observation. Controlled automated evidence cannot satisfy those live boundaries. Do not change the approved definition artifacts or weaken their testing obligations.


### E2E-003: Installed CLI controls and reporting across a short multi-repetition journey

- Covers: Public doctor/status/tick/pause/resume behavior; admission between repetitions; readiness holds; manual ordering and status reconciliation; result and review-command delivery.
- Surface: Installed `agent-factory` commands, invoked as subprocesses rather than direct internal function calls.
- Setup: Isolated root and database, local GitHub stub with at least two candidate cards, controlled suite executable writing representative artifacts. Use real processes and files with short configured scheduling/supervision intervals and a controllable test clock where needed. One request has multiple short repetitions.
- Journey: Diagnose one unavailable prerequisite, repair that test fixture, admit work, pause while a repetition runs, finish it, and reopen the CLI before resuming. Reorder queued cards and change the active card to Ready. Deliver a final result and recover an interrupted report. In a separate controlled path, close the active issue to cancel it.
- Assertions: Doctor explains readiness without launching; status is useful during execution and waiting. Tick obeys holds and the single-run guard. Pause survives process exit and permits only the current unit to finish; resume leaves unrelated holds intact. Next selection follows manual position. A contradictory Running/Ready edit is corrected without another attempt. Also drag active execution to Done and verify Running is restored and its worktrees remain. Closure cancels owned work. Final issue/board payloads include correct outcome, pins, evidence paths, and an executable-path review command when eligible; no official pass or automatic issue closure is generated.
- **Constraints:** Do not wait overnight or exhaust real quota. Scheduling arithmetic belongs to unit tests; this journey proves the controls are wired through the public CLI and persistence. No live network/model dependencies are claimed.
- Execution: `tests/e2e/`; automated checks on supported development/CI hosts. Commands must return observable exit status/output and clean up test-owned processes and storage.

## Done When

- The installed doctor/status/tick/pause/resume commands expose the required behavior through actual subprocesses, return observable exit status/output, and share production admission, persistence, reporting, and supervision paths.
- `E2E-003` is collected and passes, including retained unrelated holds, active Done correction without worktree removal, correct final result/review-command delivery, and cancellation of only owned work. Test-owned processes/storage are cleaned up.
- The per-user LaunchAgent and installation/service/restart/rollback instructions use explicit paths, preserve active supervisor environments, and describe the supported five-minute polling deployment. Packaging and plist checks pass.
- All copied operational scenarios have their implementation and automated coverage within the stated scope. The full `uv run pytest tests/integration tests/e2e` coverage is accounted for, with required macOS/Docker executions reported separately if necessary and no silent skips counted as passing.
- Portable configuration, credentials separation, explicit version updates, suite prerequisites, storage cleanup, and review handoff are documented. The separate acceptance stage retains the required live GitHub/suite/launchd flows and human ordering observation; no human ratings or live-acceptance claims are manufactured.
