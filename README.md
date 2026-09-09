# Agent Factory

Agent Factory will run Codagent's unattended evaluation and improvement workflows. The first iteration is a nightly evaluation tracer bullet: it will claim evaluation requests from the Codagent GitHub Project, run the existing `and-scene` suite, persist operational state in SQLite, and report results back to the project.

The durable controller/store boundary includes an independent supervisor and
public command-line entry points. The controller/service is intentionally not
the parent of a suite process: it reserves the run in SQLite, then starts a
new-session supervisor with file-backed output. A controller crash therefore
does not close suite pipes or terminate its process group.

## Operations

Install the package into the retained Python environment and invoke the command
with explicit portable paths (a launchd agent can use the same resident form):

```sh
agent-factory --state /var/lib/agent-factory/state.sqlite3 --config /etc/agent-factory/local.toml resident
agent-factory --state /var/lib/agent-factory/state.sqlite3 tick
agent-factory --state /var/lib/agent-factory/state.sqlite3 status
agent-factory --state /var/lib/agent-factory/state.sqlite3 pause
agent-factory --state /var/lib/agent-factory/state.sqlite3 resume
```

`tick` and the resident poll only reconcile and attach short-lived supervisors;
they never wait for an evaluation. `pause` affects later admission only, while
`status` reads saved state and remains available during execution. Keep the
installed package environment available until all previously launched attempts
have completed, since each supervisor starts from that installed environment.

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
