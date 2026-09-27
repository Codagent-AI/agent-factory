# Task: Own every execution through a named backend and settle runs left unsupervised by a restart

## Goal

Move Docker container and host process ownership out of the supervisor into
`ExecutionBackend` implementations beside the existing Fly backend, resolve each run's
backend from its recorded plan (including plans recorded by the previous release), and
route supervision, termination, result disposal, restart reconciliation, and doctor
readiness through the resolved backend for every work kind. While adoption is being
rewritten, close the restart gap: a replacement watcher that finds the launcher gone probes
the whole execution through its backend and settles the run from the evidence instead of
holding it forever.

This is a refactor of the factory's most delicate code while bug and eval work is live.
Apart from the adoption fix, observable behavior must not change, and every existing suite
must stay green.

## Background

Read `openspec/changes/factory-feature-support/design.md`, section "A. Execution backends"
(A1 to A4), Decisions 1 and 2, and the risks "Supervisor refactor on live code" and
"`tests/e2e/test_docker_worktrees.py` may already be broken". The ownership rules the
backends must keep applying live in `openspec/specs/factory-claim-lifecycle/spec.md`,
`openspec/specs/factory-fix-execution/spec.md` ("Supervise a host attempt by process"), and
`openspec/specs/factory-fly-execution/spec.md`.

Current state (paths relative to `src/agent_factory/`):

- `supervisor.py` runs every attempt. `_launch_and_observe` spawns the plan's argv, captures
  `_process_identity` (pid, `ps` start time, argv, artifact path), and records it with
  `store.begin_run` under a nonce compare-and-set. `_observe` watches progress sources,
  enforces limits, and records the result. Docker ownership is inline:
  `discover_container`, `inspect_container`, `container_matches_recorded_ownership`,
  `_mount_matches`, `stop_owned_container`, `_terminate_execution`. Fly is selected by
  `ownership_hints["backend"] == "fly-machine"` branches and runs `_supervise_fly` /
  `_observe_fly`.
- `supervise()` adopts a run only through a live launcher pid. When the launcher exited
  while no watcher ran, the replacement watcher reports "recorded execution identity is no
  longer verifiable" and the run stays `observing`, holding its kind's slot, even when a
  result file exists or the Docker container still runs.
- `backends/__init__.py` defines `ExecutionBackend` (`readiness`, `identity_from_plan`,
  `probe`, `terminate`, `dispose`, `attach_argv`, `reconcile`, `provenance`). Only
  `fly/backend.py` (`FlyMachineBackend`) implements it; runtime and doctor reach it through
  configuration-gated branches (`runtime.py` startup Fly reconcile, `_dispose_fly_result`,
  `operations.py` Fly readiness).
- `ExecutionPlan` (`controller.py`) persists `ownership_hints` as a string map. Hint shapes
  in deployed stores: eval Docker `{artifact_path, suite: and-scene, sandbox: docker,
  image_tag}`; eval Fly `{artifact_path, suite: and-scene, backend: fly-machine}`; fix and
  review Docker `{artifact_path, image_tag, sandbox: docker, branch_name}`; fix and review
  host `{artifact_path, sandbox: host, branch_name, runner_executable, runner_version,
  session_dir}`; eval plans recorded before `sandbox=docker` existed carry only
  `suite: and-scene`. Plans are built by the and-scene adapter
  (`suites/and_scene/`), `work_kinds/fix/launch.py` `build_plan`, and `build_host_plan`.

Decisions to implement:

- **Resolution** (`backends/resolve.py`): `backend_for(plan)` and `backend_name(hints)`.
  Order: `hints["backend"]` when present (`docker`, `host`, `fly-machine`; it must win,
  because Fly plans also carry `suite=and-scene`); else `sandbox=host` → `host`,
  `sandbox=docker` → `docker`; else `suite=and-scene` with an empty identity or an argv whose
  program is `run.sh` → `docker`; else hints carrying only `artifact_path` → `host`;
  anything else or conflicting hints → `None`, which callers treat as ambiguous ownership
  (report, terminate nothing, hold new launches of that kind).
- Every new plan writes `backend` and keeps writing today's `sandbox` and `suite` hints so a
  rollback still resolves it. Copy the backend name into `progress["backend"]` at launch.
- **Protocol**: keep the methods; make `attach_argv` optional through a `supports_attach`
  attribute (only Fly sets it); add `adopt(plan, run, store) -> Probe`.
- `HostProcessBackend` (`backends/host.py`): identity is `run.process`; `probe` maps
  `_identity_status` (alive, missing → `gone`, unknown); `terminate` is today's killpg
  `_terminate`; `dispose` and `reconcile` are no-ops; `readiness` returns the host
  executable and Runner-settings checks doctor already runs for `fix-host`, parameterized by
  the kinds configured for host execution; `provenance` returns runner path and version from
  the hints.
- `DockerContainerBackend` (`backends/docker.py`): identity is the composite of
  `run.process` (launcher) and `progress["container"]` (`{id, image, artifact_path}`).
  `probe`: `alive` when the launcher is alive, or the launcher is gone and the recorded
  container inspects running and matches recorded ownership; `mismatch` when the container
  no longer matches; `gone` when both are gone; `unknown` on Docker CLI failure or multiple
  matches. Container discovery and `stop_owned_container` / `_terminate_execution` move here
  unchanged. `dispose` does nothing at result time (image removal stays in
  `work_kinds/images.py` cleanup). `readiness` holds the `docker info`, memory-allowance, and
  reclaimable-space checks now in `operations.py` and `runtime.py`. `reconcile` stays a
  no-op (orphan-container reconciliation is a non-goal).
- `FlyMachineBackend` stays in `fly/backend.py`, unchanged apart from implementing `adopt`
  through its existing reattach path.
- **Supervisor**: keep the generic launcher and watcher (spawn — merge
  `_launch_and_observe` and `_spawn_plan_process` into one `launch(plan, argv)` — process
  identity, watcher lease and nonce, `_run_lock`, timers, progress sources, limits, quota
  pause, result loading, exit-code wrapper). Mechanism-specific code calls the resolved
  backend. `_observe` and `_observe_fly` may keep separate loops but take liveness,
  termination, and disposal from the backend.
- **Adoption** (the one intended behavior change). A replacement watcher on a `running` or
  `observing` run whose recorded launcher is not alive calls `backend.adopt(...)`:

  | Probe of the whole execution | Result file present | Action |
  |---|---|---|
  | alive (Docker container running, Fly Machine running) | — | continue observing (Fly: reattach a launcher) |
  | gone | yes | record the result as the watcher would (`finish_run` with the result's status) |
  | gone | no | `finish_run(interrupted, "execution ended while unsupervised")`, a technical failure for the recovery policy |
  | mismatch or unknown | — | report uncertainty as today (held, operator attention) |

  "Result file present" means `result.json`, or the kind's structured outcome file
  (`fix-outcome.json`, `review-outcome.json`, `feature-outcome.json`) in the attempt's
  evidence, which the handler's `read_result` interprets exactly as for an observed exit. A
  run whose recorded identity is empty follows the same table. A `reserved` run is still
  never resumed by `tick`.
- **Runtime and doctor**: `runtime.py` replaces the startup Fly reconcile with a loop over
  the backends named by nonterminal and undisposed runs plus the backends configured for
  new work, and replaces `_dispose_fly_result` with `_dispose_result`, which resolves the
  run's backend and applies today's decision table (only Fly acts). `_consume_results`
  stops copying `progress["container"]`; the Docker backend's `provenance` supplies it.
  `operations.doctor` asks each configured backend for `readiness`, keeping today's group
  names; `needs_docker` becomes "the Docker backend is configured for some kind".

Constraints:

- Feature attempts do not exist yet. The host backend serves every host plan; cover the
  host scenarios with host fix and review plans.
- `tests/e2e/test_docker_worktrees.py` may already fail (its plan carries only
  `suite=and-scene` with a `python -c sleep` argv). Check it before and after touching
  container discovery and report what you find instead of assuming it passes.
- Regression gates that must pass unchanged in intent (fixture edits for moved module paths
  or renamed functions are expected): `tests/e2e/test_fix_cycle.py`,
  `tests/e2e/test_factory_cycle.py`, `tests/e2e/test_independent_execution.py`,
  `tests/integration/test_supervision_hardening.py`,
  `tests/integration/test_supervision_recovery.py`, `tests/integration/test_fly_*.py`,
  `tests/integration/test_fix_*.py`, `tests/integration/test_host_launch.py`, and
  `tests/integration/test_post_run_audit.py`.
- Unit-test the resolver over every hint shape and the adoption decision table test-first.

## Spec

_From `specs/factory-execution-backends/spec.md` (ADDED Requirements)_

### Requirement: Own every execution through a named backend

Every execution plan SHALL name the execution backend that owns the attempt: `docker` for a Docker sandbox, `host` for a host process, or `fly-machine` for a Fly Machine. The run record SHALL store that name. For every work kind, readiness reported by doctor, ownership verification, probing, termination, result disposal, restart reconciliation, and provenance SHALL be provided by the attempt's backend, and the ownership rules of `factory-claim-lifecycle`, `factory-fix-execution`, and `factory-fly-execution` SHALL continue to apply to their respective mechanisms. Where a backend's surviving execution needs a new local launcher after a controller or launcher restart, that backend SHALL provide the reattachment; a backend whose execution is a local process SHALL resume watching the verified process instead. Selecting a backend SHALL NOT depend on the work kind.

#### Scenario: Record the backend at launch

- **WHEN** a host feature attempt, a Docker fix attempt, and a Fly eval attempt launch
- **THEN** each run record names its backend, `host`, `docker`, and `fly-machine` respectively

#### Scenario: Terminate through the recorded backend

- **WHEN** the factory cancels a running attempt
- **THEN** it verifies ownership and terminates execution through the backend the attempt's plan names, applying that mechanism's ownership rules

#### Scenario: Diagnose each backend in use

- **WHEN** the operator runs doctor with host features, Docker fixes, and Fly evals configured
- **THEN** doctor reports readiness for the host, Docker, and Fly Machine backends

_From `specs/factory-execution-backends/spec.md` (ADDED Requirements)_

### Requirement: Resolve the backend of plans recorded before backends were named

A plan recorded without a backend name SHALL resolve its backend from its existing ownership hints: a `fly-machine` backend hint resolves to `fly-machine`, a `host` sandbox hint to `host`, a `docker` sandbox hint to `docker`, and an eval suite hint without a sandbox hint to `docker`. A plan whose hints match none of these, or conflicting ones, SHALL be treated as ambiguous ownership: the factory SHALL terminate nothing, report the attempt for operator attention, and launch no potentially overlapping work of that kind until the attempt is resolved. Resolving a legacy plan SHALL NOT create an attempt, consume a recovery retry, or reset supervision timers.

#### Scenario: Deploy while a host fix runs

- **WHEN** the factory restarts on this change while a host fix attempt launched by the previous version is running
- **THEN** the attempt resolves to the `host` backend, its verified process is adopted, and supervision continues without a new attempt or consumed retry

#### Scenario: Deploy while a Docker eval runs

- **WHEN** the factory restarts on this change while an eval repetition launched by the previous version runs in a Docker sandbox
- **THEN** the attempt resolves to the `docker` backend and supervision continues

#### Scenario: Find an unresolvable plan

- **WHEN** a recorded plan carries no backend name and no recognized ownership hint
- **THEN** the factory terminates nothing, reports the attempt, and holds new launches of that kind until the attempt is resolved

_From `specs/factory-execution-backends/spec.md` (ADDED Requirements)_

### Requirement: Adopt unsupervised execution after a restart

When a replacement watcher finds a running or observing attempt whose recorded launcher process is no longer alive, the factory SHALL probe the whole execution through the attempt's backend before deciding. When execution is still alive, such as a running Docker container or Fly Machine that matches the recorded ownership, the factory SHALL continue supervising it. When nothing is alive and the attempt's result or structured outcome file exists, the factory SHALL record that result as it would for an attempt observed to exit. When nothing is alive and no result exists, the factory SHALL record the attempt as interrupted and apply the recovery policy. Only a mismatch or an unknown probe SHALL hold the attempt for operator attention. Adoption SHALL NOT create an attempt, consume a recovery retry for an attempt whose result exists, or reset supervision timers for execution that is still alive.

#### Scenario: Finish while the factory is down

- **WHEN** a host attempt writes its outcome and exits while no watcher is running, and the factory restarts
- **THEN** the replacement watcher records the attempt's result and releases its slot

#### Scenario: Lose execution while the factory is down

- **WHEN** a host attempt's process is gone after a restart and it wrote no outcome
- **THEN** the attempt is recorded as interrupted and the recovery policy applies

#### Scenario: Container outlives its launcher

- **WHEN** a Docker attempt's launcher exited during a restart but its container is still running and matches the recorded ownership
- **THEN** the factory continues supervising the container

#### Scenario: Find an ambiguous container

- **WHEN** the recorded container no longer matches the recorded ownership after a restart
- **THEN** the factory terminates nothing and holds the attempt for operator attention


Portion for this task: in "Record the backend at launch", the host case is proved with a
host fix attempt, since the feature kind is not implemented here; the plan builder the
feature kind will use is the same `build_host_plan`.

## Test Plan

- `INT-001` — adoption after a restart settles unsupervised host runs. Boundary: supervisor
  watcher, real child processes, SQLite store (darwin process-session semantics). Setup: a
  temporary storage root; host plans whose argv is a small script that writes a kind outcome
  file (or `result.json`) and exits, or sleeps; the watcher killed before the child exits.
  Action: start a replacement watcher (`resume_supervisor`) after the child (a) exited with
  an outcome, (b) exited without one, (c) is still alive, (d) was replaced by an unrelated
  process reusing the pid. Assertions: (a) the run finishes with the outcome's status and the
  slot is released; (b) the run is `interrupted` with the unsupervised reason and the
  recovery policy schedules one retry; (c) supervision continues with no new attempt, no
  consumed retry, and preserved elapsed time; (d) nothing is terminated and the run is held
  for operator attention. File: `tests/integration/test_backend_adoption.py`,
  `@pytest.mark.darwin`.
- `INT-002` — adoption through the Docker backend. Boundary: supervisor, Docker backend, a
  real Docker daemon with test-owned containers. Setup: a test-owned container mounting the
  attempt's artifact path; the launcher exits while no watcher runs; a decoy container with a
  different mount. Action: start a replacement watcher; then stop the container; separately
  alter the recorded container identity. Assertions: a matching running container is adopted
  and supervised until it exits, then the result is recorded; a container that no longer
  matches is neither stopped nor adopted and the run is held; the decoy is never touched.
  File: `tests/integration/test_backend_adoption_docker.py`, `@pytest.mark.docker`
  (deselected by default; run with `pytest -m docker` on a Docker host; check free disk space
  before running Docker tests).
- `INT-003` — plans recorded before the deploy keep working. Boundary: SQLite run records →
  resolver → supervisor, runtime disposal, doctor, status. Setup: store fixtures with every
  legacy plan shape (eval Docker with `sandbox=docker`; eval with only `suite=and-scene`;
  eval Fly; fix Docker; fix and review host) plus new plans. Action: adopt, dispose, and
  report each run through the refactored code; load new plans with the previous release's
  hint checks. Assertions: each legacy plan resolves to the expected backend and is
  supervised, disposed (Fly decision table unchanged), and shown in status as before; an
  unresolvable plan is held without termination and blocks only its kind; every new plan
  carries `backend` and its legacy `sandbox`/`suite` hints. File:
  `tests/integration/test_backend_resolution.py`.

## Done When

- `backends/resolve.py`, `backends/host.py`, and `backends/docker.py` exist; the supervisor,
  runtime disposal and startup reconciliation, and doctor reach every mechanism through the
  resolved backend, with no remaining `ownership_hints["backend"] == "fly-machine"` or
  `sandbox == "docker"` branches outside the resolver.
- Every newly built plan carries `backend` beside its legacy hints, and the run's
  `progress["backend"]` is set at launch.
- INT-001, INT-002 (run on a Docker host, or reported as not run with the reason), and
  INT-003 pass, with unit tests for the resolver matrix and the adoption table.
- The listed regression suites pass; the status of `tests/e2e/test_docker_worktrees.py`
  before and after is reported.
- `uv run pytest`, `uv run ruff format --check . && uv run ruff check .`, and `uv run pyright`
  pass.
