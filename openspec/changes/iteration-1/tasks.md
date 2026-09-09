# Implementation tasks: iteration-1

The approved definition is an XL change: this scaffold gains an authenticated GitHub integration, durable single-worker lifecycle, independent process supervision, versioned suite/Docker integration, and a supported Mac service. Five delivery units keep the provider, persistence, process, companion-wrapper, and deployment boundaries independently verifiable. Scaffolding, schema, fixtures, tests, and documentation stay with the outcome they support.

Implement in the order below. Routing/configuration supplies the GitHub boundary for durable claims and reporting; the saved execution contracts support real supervision; that runtime supports concrete pinned-suite execution and cleanup; the final service outcome completes doctor, packaging, and the installed CLI journey. Dependencies describe delivery order, not new product approval gates. Each linked file contains its own context, verbatim relevant specification blocks, scope for shared scenarios, automated obligations, and completion criteria.

- [ ] [Deliver configured GitHub intake and routing](tasks/01-configured-github-routing.md)
- [ ] [Persist and reconcile evaluation claims and GitHub reporting](tasks/02-durable-claims-and-reporting.md)
- [ ] [Supervise execution independently of the controller](tasks/03-independent-execution-supervision.md)
- [ ] [Integrate pinned and-scene execution and worktree cleanup](tasks/04-pinned-and-scene-execution.md)
- [ ] [Deliver Mac service setup and complete operator controls](tasks/05-mac-service-and-operator-controls.md)

## Automated obligation ownership

Each full integration or E2E obligation has exactly one completion owner, at the boundary that first makes the whole approved journey executable. Routing gets focused tests immediately; the complete routing/reporting integration belongs to the controller outcome. Earlier public process entry points enable process and Git journeys before final Mac packaging. Durable claims/reporting owns saved control operations and admission effects; execution supervision owns the installed tick/status/pause/resume entry points. The controller integration also verifies native Type from issue data and the exact delivered template’s compatibility with the production parser. Unit scenarios remain implementation-time TDD obligations and are not waived when no extra integration ID is assigned.

| Obligation | Completion owner | Required boundary |
|---|---|---|
| INT-001 | [Persist and reconcile evaluation claims and GitHub reporting](tasks/02-durable-claims-and-reporting.md) | Production controller/handler/store, real temporary SQLite, controlled suite/API/time |
| INT-002 | [Persist and reconcile evaluation claims and GitHub reporting](tasks/02-durable-claims-and-reporting.md) | Production routing/controller/client and serialization, controlled GitHub, real reporting SQLite |
| E2E-002 | [Supervise execution independently of the controller](tasks/03-independent-execution-supervision.md) | Public controller/CLI with real processes, sessions, locks, SQLite; macOS required |
| INT-003 | [Integrate pinned and-scene execution and worktree cleanup](tasks/04-pinned-and-scene-execution.md) | Production suite adapter/handler with versioned artifact fixtures and controlled observations |
| E2E-001 | [Integrate pinned and-scene execution and worktree cleanup](tasks/04-pinned-and-scene-execution.md) | Public CLI admission/continuation/cleanup with real Git worktrees and a controlled suite executable |
| E2E-004 | [Integrate pinned and-scene execution and worktree cleanup](tasks/04-pinned-and-scene-execution.md) | Actual companion wrapper and selected Runner launcher, real Docker/Git and production container ownership |
| E2E-003 | [Deliver Mac service setup and complete operator controls](tasks/05-mac-service-and-operator-controls.md) | Installed CLI control/reporting journey with real processes/files and a controlled suite/GitHub boundary |

Run collected tests with `uv run pytest tests/integration tests/e2e`, plus applicable unit tests and the existing Ruff/Pyright checks. Required macOS process and Docker-wrapper coverage must run on their required hosts; register markers and any separate required Docker check. Missing environments, silent skips, and pytest exit code 5 are not passing evidence. Automated tests use isolated data and controlled external responses, never the operator's queue, database, credentials, or paid model execution.

## Acceptance retained outside implementation tasks

`test-plan.md` remains authoritative and unchanged. `AT-001` and `AT-002` both retain classification `Required`. Their execution belongs to the separate acceptance stage, not to implementors: actual issue-triggered GitHub routing/correction/general-work handling, followed by one real and-scene repetition on the installed Mac service with a controller-only launchd restart and final handoff. The human-review-command branch applies when the suite result is eligible; a suite-established product failure remains a valid observed outcome. Credentials, deployment, readiness, or admission-window unavailability leaves acceptance incomplete rather than permitting a mock substitute or schedule bypass.

`HT-001` remains the required operator board drag during paused intake acceptance, with before/after production Project POSITION reads. Prior board-layout confirmation is retained, but an API position mutation alone does not prove the UI-to-API ordering relationship. The test plan's evidence, cleanup, permitted-substitute, single-paid-repetition, and no-human-rating constraints remain intact.

## Companion delivery and deployment dependencies

The routing outcome includes the `agent-evals` Markdown template, source caller workflows across the five configured repositories, and shared agent-factory routing/label setup. The suite outcome includes the small and-scene linked-worktree mount compatibility patch. It consumes the separately delivered score-failure contract and calibration-gate removal; implementing the score threshold is outside this change. Do not update a live harness checkout still used by an existing evaluation. Record actual companion revisions and use an explicit full deployed harness SHA. The routing outcome may record a real provisional execution pin with documented compatibility gaps. The pinned-suite integration outcome owns replacing it in `config/codagent.toml` with the integrated immutable revision containing the companion mount patch and separately delivered suite prerequisites, and must use that same configured revision for `E2E-004` evidence. This adds no merge gate beyond the approved normal publication/deployment process.

Reuse the provisioned App, Project, native type, identifiers, and confirmed board layout under `setup/`. The shared routing revision must be published before caller workflows reference it, and source callers must reach their default branches through the normal deployment process before issue-event acceptance. Service assets and portable rollout/rollback documentation are implementation deliverables; live setup records and static tests are not acceptance evidence. No task adds a work kind, suite, worker, provider, automatic evidence/PR pruning, or official product scoring.
