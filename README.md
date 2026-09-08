# Agent Factory

Agent Factory will run Codagent's unattended evaluation and improvement workflows. The first iteration is a nightly evaluation tracer bullet: it will claim evaluation requests from the Codagent GitHub Project, run the existing `and-scene` suite, persist operational state in SQLite, and report results back to the project.

This repository currently contains project scaffolding only. Controller behavior, database schemas, CLI commands, and service configuration have not been implemented.

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

