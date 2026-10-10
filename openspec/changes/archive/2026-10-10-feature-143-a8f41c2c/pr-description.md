Priority lanes let higher-priority work start beside lower-priority attempts within each work kind. Reservations atomically enforce one unfinished attempt per lane and block new lower starts while higher lanes run; continuations use saved episode history. Status reports holders and waits, and deploy restores the older per-kind guard before rollback.

🟠 Attention: This change runs up to four concurrent attempts per kind, so host load and Fly spend can rise. Rollback past this release goes only through `scripts/deploy.sh`; a hand rollback bypasses the guard.


Validation: all 1,621 tests passed across the final full-suite run and an isolated retry of one existing host-fix CLI timeout (1,620 passed in the full run; 1 passed on retry). Ruff formatting/lint, Pyright, and `uv build` passed. Migration tests use the verbatim v4 schema and supervisor SQL from `origin/main`; deploy tests use temporary HOME and stub tools. Agent Validator is deferred to the next workflow step.
