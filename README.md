# Agent Factory

Agent Factory runs Codagent's unattended evaluation and improvement workflows.
It supports two work kinds side by side, each with its own execution slot: the
nightly `eval` tracer bullet, which claims evaluation requests from the
Codagent GitHub Project and runs the existing `and-scene` suite; and `fix`,
which claims Bug-typed issues routed from the configured source repositories
and runs the companion Agent Runner fix workflow to produce a pull request, a
`needs-input` decline, or a failure. Both persist operational state in SQLite
and report results back to the project.

The durable controller/store boundary includes an independent supervisor and
public command-line entry points. The controller/service is intentionally not
the parent of a suite process: it reserves the run in SQLite, then starts a
new-session supervisor with file-backed output. A controller crash therefore
does not close suite pipes or terminate its process group.

## Operations

Install the package into a retained Python 3.12/uv environment and invoke the
command with an explicit portable local configuration (the LaunchAgent uses the
same resident form):

```sh
agent-factory --config /absolute/path/to/config.toml resident
agent-factory --config /absolute/path/to/config.toml doctor
agent-factory --config /absolute/path/to/config.toml tick
agent-factory --config /absolute/path/to/config.toml status
agent-factory --config /absolute/path/to/config.toml pause
agent-factory --config /absolute/path/to/config.toml resume
```

`doctor` starts no work or repairs. `tick` and the resident controller share the
normal admission path and never wait for an evaluation; `pause` affects later
admission only, while `status` reads saved state and remains available during
execution. Keep the installed package environment available until all previously
launched attempts have completed, since each supervisor starts from that
installed environment. See [installation](docs/installation.md) and
[operations](docs/operations.md) for the supported per-user Mac service,
credentials separation, upgrades, rollback, storage, and review handoff.

## Durable controller boundary

The controller persists claim state in a local SQLite database configured outside
the repository.  It uses only three application tables: `claim`, `run`, and
`settings`. SQLite foreign keys, WAL mode, a five-second busy timeout, and
`user_version` migrations are enabled by `ClaimStore`.

`claim` preserves the accepted request fingerprint, versioned frozen eval
settings and immutable Runner/Skills/harness revisions. `run` preserves each
unit attempt, including recovery and quota continuations. `settings` holds the
factory pause, admission holds, and small feedback receipts. No transaction
encloses a GitHub, Git, Docker, or suite operation.

`Controller` accepts only writer-authorized, factory-owned Ready Eval requests;
it parses one fenced `eval` TOML block, freezes inputs once, and reserves a
single SQLite-protected run only after readiness passes. Invalid requests use a
durable `needs-input` feedback receipt instead of a claim. Reporting events are
stored on the claim with stable HTML markers. A later delivery searches existing
comments before reposting, so a lost GitHub response never reruns work or
duplicates activity.

The shipped eval parser supports the configured Runner/Skills refs, complete
role profiles, `skip_validator`, and a positive `repetitions` count. Defaults
are applied solely while a new claim is frozen. The snapshot used by its parser
contract is kept in `tests/fixtures/eval-request.md` with its source revision.

The pinned `and-scene` integration, suite prerequisites, companion wrapper
revision, retained review-command lifetime, and safe storage cleanup procedure
are documented in [suite integration](docs/suite-integration.md). In particular,
the configured harness is a deployment commit—not a branch or the sibling
checkout currently open on the Mac.

## Planned stack

- Python 3.12 with `asyncio`
- `uv` for packaging and dependency management
- SQLite for local operational state
- TOML for runtime configuration
- pytest, Ruff, and Pyright for development checks

## Development

```sh
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```
