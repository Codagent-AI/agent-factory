## Coverage Strategy

The specifications remain the source of unit-test requirements: configuration defaults, freezing,
`refs_text`, `_fly_versions`, version parsing, and diagnostic wording. This plan records only the
additional integration and end-to-end obligations, the acceptance testing envelope, and any
human-only checks.

Most of this change sits at boundaries:

- shell scripts against real git repositories;
- the `flyctl` build command and the Dockerfile the factory generates;
- admission against real git remotes;
- host process provenance;
- `doctor` against real symlinks and the LaunchAgent definition.

Those boundaries are where the tests go. Two existing end-to-end cycles are extended rather than
adding new ones.

All automated tests run under the repository's validator check `uv run pytest`, which deselects
the `docker` marker by default. None of them needs Docker, network access, Fly, or GitHub.

## Integration Tests

### INT-001: Checkout update scripts keep the safety rules for both repositories

- **Covers:** `factory-operations` "Build the host Agent Validator from main on deploy": deploy
  with the checkout on `main`, and skip an unsafe checkout. It also checks that `update-runner.sh`
  behaves exactly as before.
- **Boundary:** `scripts/update-checkout.sh` through the `update-runner.sh` and
  `update-validator.sh` wrappers, run against real git repositories.
- **Setup:** extend `tests/integration/test_update_runner.py` and parametrize it over both
  wrappers. Use its existing fixtures: a bare origin, the operator's checkout, and an upstream
  clone that pushes.
- **Action:** run each wrapper in these states:
  - on `main` and behind `origin/main`;
  - on another branch;
  - with uncommitted changes;
  - with a local commit not on `origin/main`;
  - with a missing path.
- **Assertions:**
  - Exit 0 and a fast-forward to `origin/main` in the first state.
  - Exit 3 with a warning naming the right label ("Agent Runner" or "Agent Validator"), and an
    unchanged `HEAD`, in the skip states.
  - Exit 1 for a missing path.
  - The origin never receives a push.
  - The Runner wrapper's messages are byte-identical to the current ones.
- **Execution:** `tests/integration/test_update_runner.py`, run by `uv run pytest`.

### INT-002: The deploy's Validator step, with real checkouts and a stub `bun`

- **Covers:** the rest of `factory-operations` "Build the host Agent Validator from main on
  deploy":
  - no default checkout;
  - a missing configured checkout;
  - no build tool;
  - a build deferred while a host fix runs;
  - a build while only an eval runs;
  - a failed build;
  - a service `PATH` that finds another Validator;
  - `--no-validator`.
- **Boundary:** the functions in `scripts/validator.sh` (`validator_checkout`,
  `validator_preflight`, `validator_build`, `validator_path_check`) and the `host_slots_free`
  predicate, sourced by bash. They run against a real git checkout, a local config file, a stub
  `bun` on `PATH` that writes `dist/index.js` or exits non-zero, and a temporary plist-`PATH`
  directory with an `agent-validator` symlink.
- **Setup:**
  - A temporary directory holding a Runner checkout and, when the case needs one, a sibling
    `agent-validator` checkout.
  - A local config with and without `agent_validator = "…"`.
  - Literal `agent-factory status` text with `host attempts: 0` or `1`, busy or free eval
    slots, and no `host attempts:` line.
  - A Validator origin with a new commit, so a fast-forward is observable.
- **Action:** call the functions the same way `deploy.sh` does.
- **Assertions:**
  - A missing default checkout gives a skip warning and no failure.
  - A missing explicit checkout fails with the path in the message.
  - A missing `bun` fails before any build, naming `bun` and `--no-validator`.
  - `host attempts: 1`, or a status with no such line, skips both the fast-forward and the
    build with a warning. The checkout's `HEAD` is unchanged, and the stub `bun` is never
    called.
  - The pre-pause check (`--check-only`) fetches but leaves `HEAD` unchanged.
  - `host attempts: 0` with a busy eval slot fast-forwards and builds.
  - A failing `bun` returns non-zero.
  - A symlink on the plist `PATH` pointing elsewhere gives a warning naming both paths, and the
    symlink is left unchanged.
  - With `--no-validator`, nothing is called.
- **Execution:** `tests/integration/test_deploy_validator.py`. `host_slots_free` is also added
  to `tests/integration/test_deploy_slots.py`, and `slots_free` must still behave as it does
  today, including with the new `host attempts:` line present.

### INT-007: Status counts host attempts by their recorded backend

- **Covers:** `factory-operations` "Report host attempts in status", and the host-attempt guard
  in "Build the host Agent Validator from main on deploy". This includes the case where
  configuration switches while a host fix runs.
- **Boundary:** `agent-factory status` against a real SQLite claim store.
- **Setup:** a store holding:
  - a running fix run whose saved plan has `backend: host`;
  - a running Fly eval run;
  - a reserved feature run with no plan yet.

  The local config has `[fix] execution = "docker"`, as if it had been switched after the host
  fix launched.
- **Action:** run `agent-factory status` through the CLI entry point. Then settle the host fix
  and the reserved run, and run it again.
- **Assertions:** the first output contains `host attempts: 2`, counting the host fix and the
  reserved run but not the eval. The second contains `host attempts: 0`. The existing slot lines
  are unchanged.
- **Execution:** `tests/integration/test_operations_status_sync.py`.

### INT-003: The Fly claim build command and the generated Dockerfile

- **Covers:** `factory-fly-execution` "Build the sandbox image once per claim": install the
  claim's pinned Validator, build a claim without a Validator revision, and a Validator that
  does not match its revision (the build failure is surfaced).
- **Boundary:** `build_claim_image` and the launcher's `_image`, driving the fixture `flyctl`
  (`tests/fixtures/fly/flyctl.py`) as a real subprocess.
  - The fixture must find `FACTORY_CLI_REFRESH` among repeated `--build-arg` options, rather
    than taking the first one.
  - It must also copy the file passed to `--dockerfile` into its log.
- **Setup:** a Runner worktree fixture whose `docker/dev/Dockerfile` ends with `USER pwuser`,
  `WORKDIR /workspace`, and `CMD`. Use a manifest with `commits.validator` and
  `validator_repository` (taken from a frozen `sources.validator`), and one without.
  The local Validator checkout's origin is deliberately set to an SSH URL, to prove the
  launcher uses the frozen HTTPS source rather than the live origin.
- **Action:** run the build for each manifest, plus one where the fixture `flyctl` exits
  non-zero with a version-mismatch message.
- **Assertions:**
  - **With a Validator:**
    - `--dockerfile` is an absolute path to `claim.Dockerfile` in the claim's factory directory.
    - That file begins with the Runner Dockerfile byte for byte, contains the tail, and ends
      with the Runner Dockerfile's last `USER` and `WORKDIR`.
    - The build arguments include `FACTORY_CLI_REFRESH=<claim id>`,
      `AGENT_VALIDATOR_REVISION=<sha>`, and
      `AGENT_VALIDATOR_REPOSITORY=https://github.com/<owner>/<repo>.git`. The URL is the frozen
      one, not the checkout's SSH origin, and carries no credentials.
    - The Runner worktree's Dockerfile is unchanged.
  - **Without a Validator:** the argv is identical to today's.
  - **On failure:** no digest is recorded, and the error carries the builder's diagnostic.
- **Execution:** `tests/integration/test_fly_launcher.py`, or a new
  `test_fly_validator_image.py`.

### INT-004: Eval admission freezes the Validator against real git remotes

- **Covers:** `factory-eval-intake` "Freeze accepted evaluation inputs":
  - freeze the Validator for a Fly eval;
  - the Validator cannot be resolved;
  - request a Validator ref;
  - continue after refs change;
  - continue a claim admitted before Validator pinning.

  It also covers `factory-operations` "Leave the Validator branch unset".
- **Boundary:** `EvalHandler.accept` → `resolve_revisions` → `runtime._resolve_revision`,
  against real bare git origins and local clones. The controller's admission path runs with the
  existing fake GitHub client.
- **Setup:** origins and clones for Runner, Skills, Evals, and Validator. There is a local
  config for Fly and one for Docker execution.
- **Action:**
  1. Admit under Fly. Advance the Validator's `main` and admit again.
  2. Admit under Docker.
  3. Admit under Fly with the Validator checkout removed, then restore it and run another
     cycle.
  4. Submit a request whose block contains `agent_validator_ref`.
  5. Load a claim whose frozen spec was stored without `validator`, and plan its next
     repetition.
- **Assertions:**
  - Under Fly, `revisions.validator` is the `main` commit at admission, and the second claim
    records the new commit. The first claim's revision is unchanged.
  - Under Docker, no `validator` key is recorded.
  - With the checkout missing, no claim is created, and one "Waiting for revision readiness"
    comment names the Validator checkout and sets the attention label. The next cycle, with the
    checkout back, admits the eval.
  - With the checkout's origin set to `git@github.com:Codagent-AI/agent-validator.git`, the
    frozen `sources.validator` is `https://github.com/Codagent-AI/agent-validator.git`.
  - With the origin set to a `file://` URL or local path, admission is held with the
    "not a GitHub repository the Fly builder can fetch" readiness comment, and no claim is
    created.
  - A Fly-pinned claim prepared after `eval.execution` switches to `docker` is held with the
    explanation, and no attempt launches. Once execution is `fly` again, it launches with its
    original frozen inputs.
  - The `agent_validator_ref` request gets the unsupported-setting correction.
  - The legacy claim plans without error, and its frozen spec is unchanged.
- **Execution:** `tests/integration/test_revision_resolution.py` and
  `tests/integration/test_fly_intake.py`.

### INT-005: Host provenance records the Validator that actually ran

- **Covers:** `factory-fix-execution` "Record host provenance": inspect provenance, record the
  build and not the checkout, and a Validator that reports no commit. Feature attempts inherit
  this.
- **Boundary:** `build_host_plan` and `backends/host.py`. They run with a stub
  `agent-validator` executable on `PATH` and a real git Validator checkout.
- **Setup:** the checkout has commits `A` and then `B` (`HEAD`). Stubs print:
  - `<short A> subject`;
  - `1.14.0`;
  - nothing, with a non-zero exit.

  A fourth case has no `agent-validator` on `PATH` at all.
- **Action:** build a host plan for a fix and for a feature, then record the run.
- **Assertions:**
  - With the `<short A>` stub, `host-provenance.json` and the run record carry
    `validator_executable` (its real path), `validator_version`, and `validator_commit` equal to
    the full SHA of `A`, not `B`. The evidence note names that commit.
  - The npm stub gives `validator_commit: "unavailable"`, and the attempt is still planned.
  - A failing or missing executable is recorded as unknown, and nothing is raised.
- **Execution:** `tests/integration/test_host_launch.py` and
  `tests/integration/test_feature_launch.py`.

### INT-006: Doctor checks the Validator against real links and the LaunchAgent definition

- **Covers:** `factory-operations` "Diagnose the Agent Validator build":
  - a current build;
  - a build that is behind;
  - an npm Validator on the host;
  - a missing checkout for Fly evals.

  It also covers "Run Docker-only without a checkout".
- **Boundary:** the `doctor` diagnostics (`_host_diagnostics` and `FlyBackend.readiness`) run
  against real filesystem symlinks, a real git checkout with an `origin/main` ref, a stub
  `agent-validator`, and a plist fixture at an injected LaunchAgent path.
- **Setup:** one checkout with a stub `dist/index.js` that prints `<short sha> subject`. It has
  these links:
  - the service `PATH` and the plist `PATH` both link to it;
  - the plist `PATH` links to a separate "npm" copy;
  - the service `PATH` links to the npm copy.

  Configurations for host fixes, Fly evals, and Docker only.
- **Action:** run the grouped doctor diagnostics.
- **Assertions:**
  - Matching links pass and show the checkout and the commit.
  - A build behind `origin/main` is informational and passes.
  - The npm copy on either `PATH` fails the host group, naming the resolved path, the expected
    path, and the action.
  - A missing checkout fails `eval-fly` under Fly.
  - Docker-only configurations print no Validator line.
  - The checkout, its refs, and the links are unchanged afterwards.
- **Execution:** `tests/integration/test_fix_readiness.py` and
  `tests/integration/test_fly_readiness.py`.

## End-to-End Tests

### E2E-001: A Fly eval pins, builds, runs, and reports its Validator

- **Covers:** the whole Fly journey:
  - `factory-eval-intake` freezing;
  - `factory-fly-execution` build and Machine provenance;
  - `factory-eval-reporting` "Report a pinned Validator".
- **Surface:** the factory cycle (`tick` or `resident`), as driven by the existing
  `tests/e2e/test_fly_eval_cycle.py` harness, with the fixture Fly API and `flyctl`.
- **Setup:** extend the harness with a local Validator origin and checkout. The guest runs
  locally with a stub `agent-validator` on its `PATH` that prints the frozen short SHA.
- **Journey:** an eval request is admitted under Fly, the claim image is built, a repetition
  runs and completes, and the report is delivered.
- **Assertions:**
  - The frozen spec has `revisions.validator`.
  - The fixture `flyctl` log shows the Validator build arguments.
  - The attempt's provenance lists an `agent-validator` version.
  - The board `Refs` value ends with `validator@<7>`.
  - The frozen-inputs comment names the full Validator SHA.
  - A second harness case, with a claim frozen without `validator`, still reports
    `runner@ skills@ evals@` and "not pinned".
- **Execution:** `tests/e2e/test_fly_eval_cycle.py`, run by `uv run pytest`.

### E2E-002: A host fix launch records Validator provenance

- **Covers:** `factory-fix-execution` "Inspect a host attempt's provenance", end to end through
  the launch and the run record.
- **Surface:** the existing host-launch journey in `tests/e2e/test_host_fix_launch.py`.
- **Setup:** the existing harness, plus a stub `agent-validator` and a Validator checkout.
- **Journey:** a fix is admitted and launched on the host, and runs to completion.
- **Assertions:** the stored run record and `host-provenance.json` carry the Validator's
  executable, version, and full commit, and the operator's `HOME` is untouched, as today.
- **Execution:** `tests/e2e/test_host_fix_launch.py`. It is `darwin`-marked where the existing
  tests are.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - The feature's own worktree and its `uv` virtual environment.
  - Temporary directories for local configs, clones of `Codagent-AI/agent-validator` and
    `Codagent-AI/agent-runner`, plist copies, and `PATH` directories.
  - `bun` from `~/.bun/bin` for real local Validator builds in those temporary clones.
  - `flyctl` for the single authorized remote build below.
- **Credentials and secrets:**
  - The Fly deploy token at the path in `~/.agent-factory/config.toml` (`[fly] token_file`).
  - The GitHub App key and model logins used by the live factory, which may be used only by
    read-only `doctor` runs.
  - No other credentials are needed.
- **Authorized effects:**
  - Real local Validator builds (`bun install` and `bun run build:local`) in temporary clones.
  - Read-only `agent-factory doctor` runs from the worktree, against `~/.agent-factory/config.toml`
    or temporary copies of it.
  - At most two real `flyctl deploy --build-only --push` builds (one plus one retry) of
    `claim.Dockerfile`, against the configured Fly app from a temporary Runner worktree at
    `origin/main`, tagged `claim-accept<8 random hex>`. These use a few cents of remote-builder
    time and a registry tag that is not removed (#15). Record the tag and digest in the
    evidence.
- **Off limits:**
  - Running `scripts/deploy.sh`. It restarts the live LaunchAgent.
  - `launchctl`, and editing `~/Library/LaunchAgents/com.codagent.agent-factory.plist`.
  - `~/.agent-factory/releases`, the service clone, the live SQLite store, and pausing or
    resuming the factory.
  - Fast-forwarding, building, or relinking `/Users/paul/codagent/agent-validator`,
    `/Users/paul/codagent/agent-runner`, or `~/.local/bin/agent-validator`.
  - Creating Fly Machines.
  - Posting to GitHub issues or the Project board.
  - Changing `config/codagent.toml` beyond the committed `agent_validator_ref`.
- **Permitted substitutes:**
  - Stub `agent-validator`, `bun`, and `flyctl` executables, and fixture status text, wherever a
    real one would touch live state.
  - If the Fly builder or registry is unavailable, a local `docker build` of `claim.Dockerfile`
    only if Docker Desktop is already running. Otherwise record the remote build as not run.
- **Known risk areas:**
  - `flyctl` rejecting a `--dockerfile` path outside the build context. The design's fallback
    is to write the file into the worktree's excluded `.factory/` area.
  - `bun install --frozen-lockfile` failing under the pinned `BUN_VERSION`.
  - The fixture `flyctl` taking the first `--build-arg`, which is a regression trap for
    argument order.
  - A LaunchAgent `PATH` that differs from the interactive one. This is a past defect cluster
    for host executables.
  - A partial build seen by someone running `agent-validator` by hand during a deploy. This is
    an accepted limitation.
  - Old eval claims without `revisions.validator` must keep reporting and building.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| factory-operations: Build the host Agent Validator from main on deploy | INT-001, INT-002, INT-007 | — | — |
| factory-operations: Report host attempts in status | INT-007 | — | — |
| factory-operations: Locate the Agent Validator checkout | INT-002, INT-006 | — | — |
| factory-operations: Diagnose the Agent Validator build | INT-006 | — | — |
| factory-operations: Apply shared deployment changes (Validator branch default) | INT-004 | — | — |
| factory-eval-intake: Freeze accepted evaluation inputs | INT-004 | E2E-001 | — |
| factory-fly-execution: Build the sandbox image once per claim | INT-003 | E2E-001 | — |
| factory-fly-execution: Record Machine provenance | — | E2E-001 | — |
| factory-eval-reporting: Report traceable inputs and per-repetition results | — | E2E-001 | — |
| factory-fix-execution: Record host provenance | INT-005 | E2E-002 | — |
