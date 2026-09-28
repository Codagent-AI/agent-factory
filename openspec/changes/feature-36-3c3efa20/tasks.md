- [x] Run Agent Validator built from `main` for host fixes and features and for Fly evals, with recorded provenance

## Task: Run Agent Validator built from `main`, like Agent Runner

Implement the whole change in `proposal.md`, the delta specs under `specs/`, and `design.md`.
The decision log is `decisions.md`, and the automated obligations are in `test-plan.md`
(INT-001 to INT-007, E2E-001, E2E-002). Always compare against `origin/main`.

### Scope

1. **Configuration** (`src/agent_factory/config.py`, `config/codagent.toml`,
   `config/local.example.toml`):
   - Add `[repositories] agent_validator` with the sibling default
     `agent_runner.parent / "agent-validator"`, and record whether the path was set explicitly.
   - Add `[eval.defaults] agent_validator_ref = "main"`.
   - Add `EvalDefaults.agent_validator_ref`.
   - Leave the request keys (`_KEYS`) unchanged.
2. **Eval admission and reporting** (`src/agent_factory/work_kinds/eval/`):
   - Add `SourceRepositories.validator`. Set it only under Fly execution.
   - `resolve_revisions` returns a Validator SHA only under Fly.
   - `validator_source_url` normalizes GitHub HTTPS, `git@`, and `ssh://` origins to
     `https://github.com/<owner>/<repo>.git`. Any other origin raises `ReadinessError`.
   - `freeze` adds `revisions.validator` and `sources.validator` only when a Validator SHA is
     present.
   - `prepare` holds a Validator-pinned claim under Docker execution, next to the existing
     Cursor-on-Fly hold.
   - `refs_text` appends `validator@<7>` when the Validator revision is present.
   - `frozen_inputs_event` adds an "Agent Validator" line: the full SHA, or "published npm
     release (not pinned)".
3. **Fly image and provenance** (`suites/and_scene`, `fly/transport.py`, `fly/launcher.py`,
   `fly/guest.py`, and the eval handler's `_fly_versions`):
   - The manifest carries `commits.validator` and `validator_repository`, taken from the frozen
     `sources.validator`.
   - `build_claim_image` writes `claim.Dockerfile` in the factory artifact directory: the
     Runner Dockerfile, then the design's tail, then the Runner Dockerfile's last `USER` and
     `WORKDIR` restored. Use a pinned `BUN_VERSION`, and fail the build on a version mismatch.
   - Pass `--dockerfile <absolute path>` and the `AGENT_VALIDATOR_REVISION` and
     `AGENT_VALIDATOR_REPOSITORY` build arguments.
   - Claims without a Validator revision build exactly as they do today.
   - The guest records `agent-validator --version`, and Fly provenance reports it.
   - Update the `tests/fixtures/fly/flyctl.py` fixture to handle repeated `--build-arg` options
     and to log the Dockerfile.
4. **Host provenance** (`work_kinds/pull_request/launch.py`, `backends/host.py`):
   - `validator_provenance(checkout)` returns the executable's real path, its `--version`
     output, and the commit, expanded against the checkout without fetching, or
     `"unavailable"`.
   - Write these into `host-provenance.json`, the plan's ownership hints, and the run record.
     Extend `HOST_NOTE`.
   - Pass the checkout path from `LocalConfig` to `build_host_plan` for both fixes and
     features.
5. **Status and doctor** (`operations.py`, `work_kinds/pull_request/readiness.py`,
   `fly/backend.py`):
   - `agent-factory status` prints `host attempts: <n>`. It counts unfinished runs whose saved
     plan's backend is `host`, plus runs with no recorded backend.
   - Replace the host check that `agent-validator` is on `PATH` with two diagnostics:
     - the build check, which compares both the service `PATH` and the installed LaunchAgent
       `PATH` with `<checkout>/dist/index.js`;
     - the freshness check, which is informational when the build is behind `origin/main`.
   - Add the eval-fly "Agent Validator checkout" check.
   - Show no Validator lines in Docker-only setups.
6. **Deploy scripts** (`scripts/`):
   - Add `update-checkout.sh [--check-only] <label> <checkout>`.
   - Make `update-runner.sh` a wrapper that keeps its exact messages, and add
     `update-validator.sh`.
   - Add `host_slots_free` to `slots.sh`. It reads only `host attempts: 0`, and a missing line
     counts as busy.
   - Add `validator.sh`, with the functions `validator_checkout`, `validator_preflight`,
     `validator_build`, and `validator_path_check`.
   - Change `deploy.sh`:
     - add `--no-validator`;
     - before the pause, choose the checkout, check for `bun`, and run `--check-only`;
     - after the pause and the Runner build, read status with the new release; if there are
       no host attempts, fast-forward and build, otherwise skip both with a warning;
     - after a build, check the plist `PATH`;
     - keep pruning on `slots_free`.
7. **Docs:** update `AGENTS.md`, `docs/operations.md`, `docs/installation.md`, and the
   `deploy.sh` header. Cover:
   - the checkout, its sibling default, and the one-time `agent-validator` link;
   - the deploy step and `--no-validator`;
   - Fly pinning, and Docker still using npm;
   - the fact that eval results change with the Validator.
8. **Tests:** add every obligation in `test-plan.md`, plus unit tests for the new parsing,
   normalization, freezing, and `refs_text` logic.

### Done when

- Every scenario in the delta specs under `specs/` is implemented and covered by the tests
  that `test-plan.md` names.
- `uv run ruff format --check . && uv run ruff check .`, `uv run pyright`, `uv build`, and
  `uv run pytest` pass.
- `openspec validate feature-36-a9a6c89a --strict` passes.
- Claims admitted before the change (no `revisions.validator`) still plan, build, and report
  exactly as before.
- No test or implementation step runs `scripts/deploy.sh`, `launchctl`, or real Fly commands,
  or touches `~/.agent-factory`, `~/Library/LaunchAgents`, `/Users/paul/codagent/*`, or
  `~/.local/bin`.
