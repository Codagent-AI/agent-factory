# Agent Factory

Agent Factory will run Codagent's unattended evaluation and improvement workflows. The first iteration is a nightly evaluation tracer bullet: it will claim evaluation requests from the Codagent GitHub Project, run the existing `and-scene` suite, persist operational state in SQLite, and report results back to the project.

The durable controller/store boundary is implemented here; command-line and
service-entry-point wiring are delivered separately.

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
