# Task: Supervise Fly attempts through Machine ownership

## Goal

Make the Machine, not a Mac process, the durable identity of a Fly eval attempt. The supervisor
dispatches to `FlyMachineBackend` for plans with `ownership_hints["backend"] == "fly-machine"`:
it adopts a surviving Machine after a restart, reports a metadata mismatch without touching
anything, finishes a vanished Machine as `machine lost`, stops a job for inactivity/timeout/cancel
over ssh and lets the launcher collect before finishing, and proves pre-checkpoint failures so
recovery runs fresh in the same Machine. The eval handler settles a lost Machine without retry and
records Machine provenance in the result.

## Background

Read `openspec/changes/fly/proposal.md` and `openspec/changes/fly/design.md` (sections "Execution
backend concept", "Supervision", "Disposal after classification" for the lost-Machine
classification, "Provenance") first.

Today `src/agent_factory/supervisor.py` verifies ownership through the recorded local process and,
when `ownership_hints["sandbox"] == "docker"`, a discovered container (`_discovers_container`,
`_needs_container_discovery`, `_terminate_execution`, `_identity_status`, `_terminate`). A missing
recorded process is unverifiable and reported as uncertainty — correct for Docker and host, wrong
for a Machine that outlives every Mac process. **Leave the process and Docker ownership code
untouched**; add a dispatch for `backend == "fly-machine"` beside it.

### What already exists

- `src/agent_factory/backends/__init__.py`: the `ExecutionBackend` protocol (`readiness`,
  `identity_from_plan`, `probe` → `alive | stopped | gone | mismatch | unknown`, `terminate`,
  `dispose(identity, "destroy" | "stop" | "keep")`, `attach_argv`, `reconcile`, `provenance`).
  `src/agent_factory/fly/backend.py`: `FlyMachineBackend` with only `readiness()` implemented.
- `src/agent_factory/fly/api.py`: urllib Machines REST client (create, get, list by metadata, set
  one metadata key, update stopped Machine config env, stop, start, destroy with 404 = success,
  typed errors).
- `src/agent_factory/fly/transport.py`: `flyctl` ssh/sftp wrappers (put file, run command, stream,
  tar-get). `src/agent_factory/fly/launcher.py`: the `SANDBOX_RUNNER` adapter with fresh, resume,
  `attach --run-dir <artifact>`, stand-in, and `--dry-run` modes. It writes
  `<artifact>/.factory/machine.json` `{app, id, nonce, deadline, created_at, image_ref, guest,
  region, history}` before any secret is delivered, relays only guest bytes to stdout (captured by
  the supervisor as `<artifact>/factory-suite.log`), writes `<artifact>/.factory/heartbeat.json`
  only when guest-reported mtimes change (with `checkpoint_seen: true` once
  `/artifacts/run-state.json` exists), collects the verified tree on `DONE`, and exits with the
  guest job's exit code or: `70` Fly failure before a Machine existed, `71` Machine lost, `72`
  collection failed, `73` ownership mismatch. Outside stand-in mode it never destroys the Machine.
- Guest protocol: jobs live at `/artifacts/.factory/job/<n>/` (`job.sh`, `job.log`, `exit-code`,
  `files.txt`, `DONE`); killing a job's process group makes the guest write `DONE` with exit 143.
- `AndSceneAdapter.plan` under `fly` emits `ownership_hints["backend"] = "fly-machine"` (no
  `sandbox` key), progress sources `factory-suite.log` and `.factory/heartbeat.json`, and writes
  `<artifact>/.factory/manifest.json`, which includes an `expect_checkpoint` boolean that the plan
  builder takes as an input (default false).
- `operations.status` already renders `run.progress["machine"]`
  (`{app, id, nonce, deadline_epoch, state, image_ref, guest, region}`) and the settings
  `("runtime", "fly:mismatch")` = `{machine_id, run_id, expected, observed, remedy}`,
  `("runtime", "fly:unknown")`, `("runtime", "fly:cleanup-failed")`.
- `tests/fixtures/fly/`: fake Machines REST server and a fake `flyctl` shim operating on a temp
  guest directory.

### Backend methods to implement (`src/agent_factory/fly/backend.py`)

- `identity_from_plan(plan, run)`: read `<artifact>/.factory/machine.json`; `None` when absent.
- `probe(identity)`: GET the Machine; `gone` on 404; `mismatch` when any immutable ownership
  metadata (`factory-owner`, `run_id`, `claim_id`, `unit_key`, `nonce`) differs from the recorded
  attempt; `alive`/`stopped` from state; `unknown` on API failure (never treated as gone).
- `terminate(identity)`: verify ownership by probe, then ssh-kill the current job's process group.
  Keeps the Machine. Returns False (nothing terminated) on `mismatch`/`unknown`.
- `attach_argv(plan, run)`: the launcher's `attach --run-dir <artifact>` command.
- `provenance(identity)`: Machine id, observed image digest, cpu kind, cpus, memory, region.
- `dispose(identity, "destroy")` is needed here for cancellation; implement `destroy` (idempotent,
  verified by GET). Like `terminate`, it re-probes first and issues **no** destroy call on
  `mismatch` or `unknown`; `mismatch` takes the `fly:mismatch` path below. `stop`/`keep` and
  `reconcile` may remain unimplemented.

### Supervisor behavior for a `fly-machine` plan (`src/agent_factory/supervisor.py`)

- Ignore local process identity for ownership. `identity = backend.identity_from_plan()` is copied
  into `progress["machine"]` as soon as `machine.json` appears, along with `checkpoint_seen` from the
  heartbeat.
- On restart (`supervise()` / `resume_supervisor`), when no launcher process is alive:
  `alive` → spawn `attach_argv` and keep observing with existing timers (no new attempt, no retry
  consumed, no uncertainty); `stopped` with no quota hold recorded → interrupted; `gone` → finish
  the run as `interrupted` with `reason = "machine lost"`; `mismatch` → record
  `("runtime", "fly:mismatch")` with the Machine id, expected and observed metadata, and the remedy
  (destroy the Machine by hand or wait for its deadline), terminate nothing, report uncertainty; once
  a later probe finds that Machine gone, clear the setting and finish the run as `machine lost`.
- `unknown` (the API could not answer) is ambiguous ownership, not an outcome: attach nothing,
  terminate nothing, do not finish the run, keep its slot held so no overlapping eval starts,
  report it through the supervisor's existing uncertainty reporting with the API error, and probe
  again on the next pass until the answer is `alive`, `stopped`, `gone`, or `mismatch`.
- If the watcher died between `Popen` and copying `machine.json` into `progress`, the replacement
  reads `machine.json` from the artifact directory.
- Launcher exits `71` and `72`, and a run whose result carries `collection: "failed"`, are the
  lost-Machine outcome; `73` is the mismatch path; `70` is an ordinary technical failure.
- Observation lost for the inactivity period while `probe == alive` is the **inactivity limit**
  (same-Machine recovery), never a lost Machine. Relay activity is not progress; only the two
  progress sources are.
- `terminate` for inactivity, timeout, and cancel is a sequence, not a kill of the launcher:
  `backend.terminate()` → wait for the launcher's own exit, bounded by
  `fly.collection_grace_seconds` (it collects on `DONE`) → `finish_run`. Only if the launcher does
  not exit in time is it killed and the result marked `collection: "failed"`.
- After a verified **cancel** terminate the supervisor itself calls
  `backend.dispose(identity, "destroy")`, because `runtime._consume_results` skips cancelled claims.
  Timeout and inactivity leave the Machine in place.
- Codex quota holds are still recognized from the collected `result.json` exactly as under Docker
  (never from the log; see the comment in `suites/and_scene/__init__.py`).

### New-launch hold on mismatch (`src/agent_factory/runtime.py`)

While `("runtime", "fly:mismatch")` is set, `_kind_failures` holds new eval launches with an
`eval-fly` diagnostic naming the Machine and remedy; no overlapping execution starts.

### Handler behavior (`src/agent_factory/work_kinds/eval/handler.py`)

- `_technical_failure`: `reason == "machine lost"` is **not** technical. `classify` returns
  `settled` with `result["failure"] = {"owner": "factory", "code": "machine-lost"}`; the streamed
  evidence is preserved; no retry is consumed.
- `_run_needs_recovery` and `_nonresumable_workflow` recognize a factory-owned `machine-lost`
  failure as settled without retry; `next_unit` proceeds to the next repetition (new Machine).
  Aggregate-verdict wording for lost repetitions is outside this task; keep `settle()` from
  treating a lost repetition as needing recovery.
- `plan_attempt`: a previous attempt whose progress lacks `checkpoint_seen` (and whose collected
  tree holds no candidate marker) stopped before checkpoint creation → recovery runs the suite
  **without `--resume` in the same Machine**, consuming the ordinary retry (pass
  `pre_checkpoint_proven=True`, `expect_checkpoint=False`). Previous progress with
  `checkpoint_seen` → `expect_checkpoint=True`, so a now-missing checkpoint becomes exit 71 / lost
  Machine rather than the corrupt-state path.
- `read_result` merges `progress["machine"]` provenance into
  `result["execution_provenance"]["fly"]` (Machine id, image digest, cpu kind, cpus, memory,
  region). If the observation is unavailable, say so; never substitute the configured tag. Under
  `fly` no `image_tag` hint exists.

## Spec

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Make the Machine the durable execution identity

Ownership of a Fly attempt SHALL be verified from the recorded Machine identity, launch nonce, and metadata, never from the identity of a process on the factory host. After a controller or launcher restart, the factory SHALL reattach to a verified surviving Machine and continue supervision without creating a new attempt, consuming a recovery retry, or resetting supervision timers. A Machine whose metadata does not match the recorded attempt SHALL NOT be terminated or adopted; the factory SHALL report the mismatch with the Machine identity and the operator remedy, hold new eval launches, and treat the mismatch as resolved once that Machine no longer exists, whether by its deadline or by operator action, at which point the affected attempt SHALL settle as a lost Machine.

#### Scenario: Restart the Mac during a repetition

- **WHEN** the factory host restarts while a Machine is running a repetition
- **THEN** the factory reattaches to that Machine, the repetition continues, and no duplicate attempt starts

#### Scenario: Find a Machine that does not match

- **WHEN** the Machine recorded for an attempt exists but its metadata does not match the recorded identity and nonce
- **THEN** the factory terminates nothing, reports the mismatch with the Machine identity and remedy, and holds new eval launches until that Machine no longer exists, after which the attempt settles as a lost Machine

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Supervise through a live stream and heartbeat

While an attempt runs, the factory SHALL stream the Machine's suite output into the attempt's host log through a single writer and SHALL receive a heartbeat reporting changes in the suite's known progress and quota-hold signals at an interval well below the inactivity limit. Output and heartbeat content SHALL feed the existing progress, inactivity, and provider-quota behavior; relay activity itself SHALL NOT count as progress. If observation of a Machine is lost for the inactivity period while the Machine is verifiably alive, the factory SHALL treat the attempt as having reached the inactivity limit and apply same-Machine recovery rather than declaring the Machine lost.

#### Scenario: Observe a quota result from the Machine

- **WHEN** the collected result of a Fly attempt reports a recognized Codex usage limit
- **THEN** the factory records the provider hold exactly as it would for local execution

#### Scenario: Lose observation of a healthy Machine

- **WHEN** the factory cannot observe a Machine for the inactivity period and the Machine is verifiably running
- **THEN** the attempt is stopped by the inactivity limit and follows same-Machine recovery, and the Machine is not settled as lost

This task's portion: feeding progress/inactivity from the log and heartbeat, quota recognition
from the collected result, and "Lose observation of a healthy Machine".

_From `specs/factory-fly-execution/spec.md`:_

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

This task's portion: stopping the job and collecting before an attempt is finished by a limit or
cancellation, "Cancel a running repetition", "Miss the collection grace period", and "Collect a
partial tree" as seen by the supervisor and handler. Destroy/stop/keep after classification is
outside this task.

_From `specs/factory-fly-execution/spec.md`:_

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

This task's portion: recovery planning (`--resume` vs fresh in the same Machine) and its retry
accounting.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Settle a lost Machine as a failed repetition

When a Machine is found destroyed, missing, or without a checkpoint it was known to have, or its collection cannot be verified, the factory SHALL record the repetition as failed for a factory-owned infrastructure reason, preserve the evidence already streamed, consume no recovery retry, and continue remaining repetitions in new Machines. The lost repetition SHALL NOT be rerun automatically, SHALL NOT be classified as a product failure, and SHALL NOT be offered recovery. The aggregate verdict SHALL follow the lost-repetition rule in `factory-eval-reporting`.

#### Scenario: Lose a Machine mid-run

- **WHEN** a running repetition's Machine no longer exists
- **THEN** that repetition is recorded as failed with an infrastructure reason and its streamed evidence, and the next repetition starts in a new Machine

#### Scenario: Lose every repetition

- **WHEN** all of a claim's repetitions are settled as lost Machines
- **THEN** the claim reports through the existing infrastructure-error path with each loss explained

This task's portion: per-repetition classification, no retry, next repetition proceeds. The
aggregate verdict is outside this task.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Record Machine provenance

Before model execution, the factory SHALL record the Machine identity, the immutable image digest observed on the launched Machine, and the Machine's CPU kind, CPU count, memory, and region as observed execution provenance for the attempt. A configured mutable image tag SHALL NOT be recorded in place of the observed digest.

#### Scenario: Inspect a completed Fly repetition

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the Machine identity, observed image digest, size, and region used for that attempt are identifiable

_From `specs/factory-claim-lifecycle/spec.md`:_

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

_From `specs/factory-claim-lifecycle/spec.md`:_

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

_From `specs/factory-claim-lifecycle/spec.md`:_

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

_From `specs/factory-eval-execution/spec.md`:_

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

_From `specs/factory-eval-execution/spec.md`:_

### Requirement: Record the evaluated environment accurately

The factory SHALL retain the selected suite identity, full Runner and Skills commit SHAs, deployed `agent-evals` commit SHA, fixture and reference pins, rubric identities, workflow, judge profile, role settings, validator setting, and repetition count. It SHALL record the sandbox image actually used: under Docker, the actual container's immutable image ID after the image is built; under Fly, the immutable image digest observed on the launched Machine together with the Machine identity, size, and region. It SHALL NOT infer the image from a mutable tag or claim that a future image digest was known when the request was accepted. If that observation is unavailable, provenance SHALL say so rather than substitute a later tag lookup. These records SHALL distinguish requested inputs from observed execution provenance.

#### Scenario: Inspect a completed evaluation

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the tested Runner and Skills revisions and the harness, suite, scoring inputs, and execution settings used to evaluate them are identifiable
- **AND** the recorded image identifies the image actually used and, for Fly, the Machine used


## Test Plan

Automated tests never contact Fly; use the fake REST server and fake `flyctl` shim in
`tests/fixtures/fly/`. Follow the style of `tests/integration/test_supervision_recovery.py` and
`tests/integration/test_supervision_hardening.py`. Unit cases are your TDD decisions.

### INT-005: Supervisor ownership through the Fly backend
- Boundary: `supervisor.py` with `ownership_hints["backend"] = "fly-machine"`, the fake API, and
  the fake `flyctl` shim.
- Setup: a run marked running with a recorded launcher process that no longer exists.
- Action: restart the watcher with the Machine alive; with the Machine gone; with metadata nonce
  mismatched, then with that Machine gone on a later probe; with the Machine alive but the stream
  silent past the inactivity limit; cancel a running attempt; let a prior attempt fail before
  `checkpoint_seen` and plan the recovery attempt.
- Assertions: alive → attach launcher spawned, no retry consumed, no uncertainty recorded; gone →
  run finishes with failure `{"owner": "factory", "code": "machine-lost"}` and the handler
  settles it without recovery; mismatch → `fly:mismatch` recorded with id and remedy, no attach,
  and the run finishes as lost once the Machine is gone; silent → job killed over ssh, the
  launcher allowed to collect before the run finishes, Machine kept, classified technical;
  cancel → job killed, collected, and the Machine destroyed by the supervisor; pre-checkpoint
  failure → recovery plan without `--resume` in the same Machine consuming the retry.
- Execution: `tests/integration/test_fly_supervision.py`.

## Done When

- Every scenario in the Spec section that falls in this task's portion is covered by a passing test,
  and INT-005 passes at the named path.
- Beyond INT-005's listed cases, `tests/integration/test_fly_supervision.py` also covers: a restart
  probe that fails with a typed API error leaves the run unresolved with nothing attached or
  terminated and no new eval launched, then resolves when a later probe answers; and a cancel
  whose Machine metadata mismatches issues no destroy call.
- Existing Docker and host supervision tests pass unmodified; the process/container ownership code
  paths are not refactored.
- `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest` passes.
