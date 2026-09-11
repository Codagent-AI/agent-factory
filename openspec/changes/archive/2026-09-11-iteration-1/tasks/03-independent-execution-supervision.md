# Task: Supervise execution independently of the controller

## Goal

Run one owned attempt through public controller entry points while preserving it across controller failure and safely reconciling startup, survivor, cancellation, and timeout states.

## Background

All repository-relative code paths below are in `agent-factory` unless a sibling repository is explicitly named. Planning sources are `openspec/changes/iteration-1/proposal.md`, `design.md`, the cited files under `specs/`, and `test-plan.md` in that same change directory. Read the relevant design sections and the approved test plan; the excerpts below preserve the requirements and assigned automated obligations verbatim.

The repository begins as a Python 3.12/uv scaffold: `src/agent_factory/__init__.py`, `pyproject.toml`, `README.md`, and an empty `tests/` tree. The package areas named below are intended implementation locations, not claims that an API already exists. Use small protocols and dataclasses with static registration for only `eval` and `and-scene`. Preserve GitHub intent, SQLite execution history, and suite-owned evidence as separate authorities. Keep organization names, logical field mappings, local paths, and defaults configurable. Do not add more work kinds, suites, workers, queue/storage providers, a dynamic plugin loader, a generic outbox, or another evaluator.

The App, native Eval type, and board were already provisioned and confirmed; reuse the records in `openspec/changes/iteration-1/setup/`. That prior confirmation satisfies the pre-controller board-layout gate and is not evidence of implementation acceptance. Public configuration and documentation must use portable paths and contain no private credentials.

Read the design sections “Interface contracts”, “Persistent model”, “Controller cycle and admission”, “Supervisor, process lifetime, and restart recovery”, and “Progress, quota, and failure classification”. Implement `src/agent_factory/supervisor.py` and cohesive process/container observation helpers, connecting the controller's durable run reservation and `ExecutionPlan`/`Observation`/`AttemptResult` contracts to real operating-system execution. The `claim`, `run`, and `settings` store and cycle/admission/reporting behavior are the consumed production contracts; retain their column ownership and short transactions.

Deliver the installed `agent-factory` command entry point in `pyproject.toml` and `src/agent_factory/cli.py`, plus a resident controller entry point suitable for service invocation. Wire `tick` to the actual shared cycle, `status` to saved state, and `pause`/`resume` to durable independent controls so real process journeys use public commands. A resident controller polls every five minutes while supervisors observe on their own short loops. `tick` may start execution and then exit; neither it nor a poll waits for a long evaluation. These controls must remain usable with a running attempt. launchd packaging and complete doctor diagnostics are deployment concerns; expose a stable explicit-path resident command for that packaging.

Commit the reserved run and launch nonce before spawning an internal supervisor with run ID and explicit state/config paths. Use `start_new_session=True` and file-backed output, never controller-owned pipes. Keep the supervisor's installed Python environment and launch-time plan/limits stable for the attempt lifetime. Use a per-run advisory lock and nonce verification so watcher replacement cannot launch the reserved attempt twice. The database's single nonterminal slot and cycle lock cover service/tick concurrency and uncertain startup.

Give suite execution its own surviving process/session identity as well. Persist PID plus start identity and run-specific invocation/artifact association, not PID alone. A replacement watcher cannot assume it can waitpid an unrelated surviving child. After interrupted startup, discover a survivor from launch intent and execution/artifact evidence before launching; when absence/ownership/completion cannot be proved, retain the slot and report uncertainty. Surviving supervisor or child/container means continued observation of the same run, with no new attempt or retry. Durable completion while the controller is absent resumes result aggregation/reporting. Only established execution interruption enters the existing quota/technical recovery policy.

Consume normalized progress and recognized bounded waits supplied by the suite boundary. Persist start, last progress, accumulated recognized wait intervals, wait deadlines, and elapsed accounting; use monotonic time within a live supervisor and saved timestamps/accounting after restart. Default limits are 30 minutes without progress, six hours execution excluding recognized bounded waits, and 12 hours total including waits. A bound expiring restores normal inactivity detection; waiting between units is outside attempt timers. A timeout records its specific limit and remains an execution outcome, not invented product failure.

Cancellation is a controller-written request observed by the supervisor. Before stopping or escalating, reverify recorded process/container ownership and evidence association. Container discovery inspects the exact resolved artifact bind source with `/artifacts` destination, records container ID and immutable `.Image` while present, and requires the recorded identity plus the same mount at termination. A shared image/tag, reused PID, or contradictory mount never authorizes termination. Reconcile artifacts before treating a `--rm` container's disappearance as failure. Unknown identity blocks overlap and automatic termination. Preserve unrelated processes/containers and all suite evidence. Record terminal run state before releasing the run lock; only the controller may admit a subsequent unit.

Scope of shared requirements: own real process lifetime, startup/survivor reconciliation, timer enforcement, execution ownership, and the public process entry points. Consume lifecycle decisions and durable reporting without rewriting them. Tests can use a declared controlled executable in place of model/suite work, while keeping processes, sessions, locks, SQLite, and controller commands real. Docker observation logic belongs here, with full real-wrapper/container compatibility exercised by the suite integration boundary.

## Spec

The following requirement blocks are copied verbatim from the approved specifications. For shared requirements, the Background states this delivery unit’s portion; retain the complete scenario semantics.

Source: `openspec/changes/iteration-1/specs/factory-claim-lifecycle/spec.md`.

### Requirement: Prevent overlapping execution

Iteration 1 SHALL execute at most one repetition at a time across the service and manual execution commands. The factory SHALL reconcile saved execution records with surviving processes and suite evidence before dispatching new work. Status and pause controls SHALL remain usable while execution is active.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution while the service already has an evaluation running
- **THEN** no second evaluation starts
- **AND** status and pause controls remain available

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

### Requirement: Recover reporting independently of execution

The factory SHALL retain enough reporting progress to recover missing issue activity comments, results, and field updates independently of evaluation execution. Restarting the controller or retrying GitHub delivery SHALL NOT repeat completed evaluation work or duplicate already delivered activity. Reporting SHALL preserve later human intent subject to the explicit correction policy for status edits that contradict factory execution.

#### Scenario: Retry a failed result update

- **WHEN** evaluation results are saved but delivery to GitHub fails
- **THEN** the factory retries the missing reporting without rerunning the evaluation
- **AND** it reconciles already delivered comments and later human changes before repairing remaining updates

Source: `openspec/changes/iteration-1/specs/factory-operations/spec.md`.

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

## Test Plan

Own `E2E-002` in `tests/e2e/`, invoked through the delivered resident controller and CLI subprocesses. Use a controlled suite executable and local GitHub stub with realistic normalized/artifact completion, and test-owned short timing limits. The process/session, locking, persistence, entry-point, and restart boundaries must be real; do not replace them with calls directly into internal functions. macOS execution is required for this obligation. Use synchronization points around reserved startup and bounded readiness/exit observations, not probabilistic sleeps. Add focused tests for Docker identity/mount verification with controlled inspect responses; those do not claim real-wrapper coverage.

Use implementation-time TDD for the copied specification scenarios within the scope stated above. Keep unit cases close to the behavior rather than reproducing every case at every test layer. Use isolated roots, temporary SQLite/files, and controlled external responses; automated verification must not read the operator's database, live queue, or private credentials. Run the relevant collected pytest tests and the repository's Ruff format/lint and strict Pyright checks. Register any markers in `pyproject.toml`; pytest exit code 5 or an unexecuted required platform test does not count as passing.

Implementors do not execute `AT-001`, `AT-002`, or `HT-001`. Preserve their requirements in `openspec/changes/iteration-1/test-plan.md` for the separate acceptance stage: real issue-event routing, one real suite repetition and launchd restart, and the operator's board-drag-to-API observation. Controlled automated evidence cannot satisfy those live boundaries. Do not change the approved definition artifacts or weaken their testing obligations.


### E2E-002: Real processes survive controller failure without duplicate execution

- Covers: Independent supervisor lifetime; single execution across service/tick; interrupted startup; real execution recovery; persisted timers and cancellation; ownership verification.
- Surface: Delivered controller/service process plus CLI commands, with real supervisor and child processes.
- Setup: Isolated SQLite, locks, logs, and factory configuration; local GitHub stub. A long-running controlled suite executable exposes durable progress and records each launch. Give the test runner explicit ownership of all created processes. Use short configured limits for timeout variants.
- Journey: Start an attempt and terminate only its controller. Restart the controller and issue a concurrent tick. Separately interrupt the supervisor while its child survives, finish a child while the controller is absent, and interrupt execution itself to exercise bounded recovery. Cover the reserved-start interval using synchronization points, not probabilistic timing. Request cancellation of test-owned execution and retain an unrelated decoy process.
- Assertions: Controller restart leaves the same execution alive, retains elapsed accounting, and causes no new attempt/retry. Concurrent entry points do not overlap. Supervisor replacement observes a survivor without relaunching it. Durable completion recovers reporting without rerunning. Actual interruption alone triggers the specified recovery policy. An uncertain launch/identity holds the slot. Cancellation/timeout affects only owned execution; the decoy survives.
- **Constraints:** The suite executable is a declared substitute for agent/model work; controller, supervisor, locks, SQLite, and process/session boundaries must be real. This is not proof of Docker mount ownership; E2E-004 proves it using controlled real containers, and AT-002 records ownership of the real suite container. Do not kill or stop unrelated host processes/containers to create faults.
- Execution: `tests/e2e/`; required on macOS for the supported deployment's process semantics. Compatible portions may also run on other CI hosts. All test-owned processes must be stopped/reaped in cleanup. Actual launchd restart behavior is additionally observed in AT-002.

## Done When

- A normal tick or resident poll launches an independent supervisor/child, exits or restarts without killing evaluation, and reconstructs execution and pending results without duplicate launches or resetting timers/retry usage.
- Startup uncertainty, vanished supervisors with surviving children, completion while offline, actual interruption, simultaneous ticks, cancellation, and each timeout have the specified observable behavior. Only verified owned execution is terminated; decoys remain alive.
- `E2E-002` is collected and passes on macOS, including the reserved-start synchronization cases and cleanup/reaping of every test-owned process. Any missing required platform execution is reported as incomplete coverage, never a pass.
- `agent-factory tick`, `status`, `pause`, `resume`, and the resident entry point work as installed subprocesses and keep operational controls available during execution. Document their process lifetime and retained installation requirements; do not claim actual launchd restart acceptance from controlled process tests.
