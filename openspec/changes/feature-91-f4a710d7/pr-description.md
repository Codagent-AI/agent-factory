## Summary

- Accept optional `fixture_ref` in eval requests, verify that its commit is published on the and-scene origin, and freeze its SHA.
- Pass the frozen fixture and repository to each suite attempt, report its provenance, and keep default evals unchanged.
- Add checkout diagnostics, read-only revision commands, and a two-stage rollback guard.

## Verification

- `uv run pytest`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run pyright`
- `uv build`
- `agent-validate run`

## Attention

- 🟠 #91 is only partly delivered until agent-evals' `.github/ISSUE_TEMPLATE/eval-request.md` adds this line under the existing overrides:

  ```toml
  # fixture_ref = "<and-scene branch, tag, or commit>"  # default: the agent-evals pin
  ```

  Paul opens that agent-evals change, or assigns it to the factory, before closing #91.
