## ADDED Requirements

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
