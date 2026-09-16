## Context

The factory runs two work kinds. Evals run the and-scene suite, which launches Docker containers itself. Fixes run the packaged Agent Runner workflow at `src/agent_factory/work_kinds/fix/workflow/factory-fix-v1.0.yaml` inside a sandbox container built by the Runner's `scripts/sandbox-run.sh`. All fix launch logic lives in `src/agent_factory/work_kinds/fix/launch.py` (`build_plan`, `container_script`, `stage_workflow`, `validated_credential_copy`, `check_runner_contract`); the handler (`handler.py`) calls it from `plan()` and reads `fix-outcome.json` in `read_result`.

Relevant current behaviour that this design builds on:

- The supervisor (`supervisor.py`) launches every plan with `subprocess.Popen(..., start_new_session=True)`, restricts the environment to PATH/HOME/TMPDIR/LANG/LC_ALL plus `plan.allowed_environment`, persists the plan verbatim into SQLite (`_plan_document`), and terminates by process group (`os.killpg`). Container discovery (`_discovers_container`) is enabled by plan hints `suite == "and-scene"` or `sandbox == "docker"` and identifies a container by its `/artifacts` mount.
- The runtime cycle (`runtime.py::cycle`) applies `check_memory_headroom` and a global `doctor(local, include_fix=False)` gate before any kind, then per-kind `handler.readiness()`. Holds are stored as settings `runtime/readiness:<kind>` and `runtime/memory`. The per-claim loop calls `handler.cleanup(claim, board_status=...)`.
- `operations.py` defines `Diagnostic(name, available, detail, action)`; `format_doctor` groups by a `fix ` name prefix and always prints an action line. `status()` lists every claim. `_PLIST_TEMPLATE` sets only `AGENT_FACTORY_ROOT` and `AGENT_FACTORY_GITHUB_APP_KEY`.
- `config.py` has `LimitsConfig(minimum_free_gib, inactivity_seconds, execution_seconds, total_seconds, codex_reset_fallback_seconds, memory_reservation_gib)`, `FixLocalConfig(limits, schedule)`, `CredentialsConfig(fix_environment)`, and shared `FixConfig(contract="factory-fix/1")`.
- The packaged workflow hardcodes `/artifacts` (the outcome path passed to `record-triage.sh` and `record-outcome.sh`, the verify step, and `/artifacts/logs/...`). The scripts already take an `outcome_path` argument.
- Agent Runner (`/Users/paul/codagent/agent-runner`, installed as `/Users/paul/.local/bin/agent-runner -> bin/agent-runner`, version string `dev`) discovers workflows from `<projectDir>/.agent-runner/workflows/` first, then `~/.agent-runner/workflows/`, then builtins. `AGENT_RUNNER_NO_TUI=1` makes `run` headless without a TTY. The user settings file is already `autonomous_backend: headless`, `autonomous_permission_mode: yolo`. The library `runner.Options.SessionDir` overrides the session directory (created with `MkdirAll`, not removed on failure, no project metadata written) but the `run` command has no flag for it; `run` flags are parsed in `parseRunCommandArgs` in `cmd/agent-runner/main.go` and listed by `printRunUsage`.
- The ClaimStore schema is `user_version` 4; claims carry `cleanup_json`, `preparation_json`, `reporting_json`.

Binding decisions from the proposal: host mode reuses the operator's real HOME and installed codagent plugin; the separate fix token stays; the installed Runner binary is used; readiness failure holds the bug with no Docker fallback; the workflow contract change is made in this repository.

## Goals / Non-Goals

**Goals:**

- Run fix attempts on the host through the operator's installed Runner while evals keep using Docker, selected per kind by configuration.
- Keep the fix token out of every persisted or logged place in both modes.
- Leave the operator's user-level git, Runner, and CLI configuration untouched by a host attempt.
- Make readiness, admission holds, memory, and disk checks per kind so a Docker outage holds only Docker-dependent work.
- Bound evidence growth with age-based retention and make `status` show only live work.

**Non-Goals:**

- Running evals on the host.
- Honouring the recorded Runner or Skills commits in host mode (provenance records the discrepancy instead).
- Enforcing repository rulesets or token scopes; those remain conventions and are documented as such.
- Any SQLite schema migration.

## Approach

### Companion Runner change (agent-runner repository)

Add a `--session-dir <path>` flag to `agent-runner run`, parsed in `parseRunCommandArgs` alongside `--until`, carried on `runCommandOptions`, and passed to `runner.Options.SessionDir` in `handleRunWithRunOptions` (and the onboarding path that shares it). `printRunUsage` lists the flag. Existing library semantics apply unchanged: the Runner creates the directory, writes no `meta.json` under `~/.agent-runner/projects/`, and never removes a caller-provided directory. This PR lands and the installed binary is rebuilt before the factory change is implemented, as was done for the workflow move.

### Configuration

`config.py` gains:

- `FixLocalConfig.execution: Literal["docker", "host"]`, default `"docker"`, TOML key `[fix] execution`.
- `FixLocalConfig.minimum_free_gib: float | None`, TOML key `[fix] minimum_free_gib`; when unset the shared `limits.minimum_free_gib` applies.
- `LimitsConfig.evidence_retention_days: int`, default 14, TOML key `[limits] evidence_retention_days`.

`config/local.example.toml` documents all three. An `[eval] execution = "host"` key is rejected at load with a message naming eval host execution as unsupported.

### Workflow contract

The packaged workflow declares an optional parameter `artifact_dir` with default `/artifacts`. Every hardcoded `/artifacts` becomes `{{ artifact_dir }}`: the `outcome_path` argument to both scripts, the verify step, and the log paths. The contract stays `factory-fix/1` because the default reproduces today's behaviour exactly. Both launchers pass the parameter explicitly: Docker passes `/artifacts`, host passes the attempt's evidence directory. `check_runner_contract` gains a check that the packaged workflow declares `artifact_dir`.

### Host launch

`launch.py` gains `build_host_plan(...)` beside `build_plan(...)`; `handler.plan()` selects by `local.fix.execution`. The host plan is assembled as follows.

1. **Workflow staging.** `stage_workflow(clone / ".agent-runner" / "workflows", contract)` copies `factory-fix-v1.0.yaml` and its two scripts into the attempt's target clone. The Runner's project-scope discovery finds it there. The path is added to the clone's `.git/info/exclude` next to the repo-local profile config the launcher already writes, so the agents never see or commit it. The clone is deleted at Done, taking the staging with it. Nothing is written under `~/.agent-runner`.
2. **Private directory.** `<storage_root>/private/<run_id>/` (owner-only) holds `fix.env` (the existing validated credential copy), `gitconfig`, `askpass.sh`, and `host-run.sh`. `gitconfig` sets `user.name`, `user.email`, `credential.helper=` (empty, disabling helpers), `core.askPass=<askpass.sh>`, and blank `url.*.insteadOf`/`http.extraHeader` overrides. `askpass.sh` prints the token from `GH_TOKEN` for the username and password prompts.
3. **Wrapper.** `host-run.sh` (mode 0700) sources `fix.env`, exports `GH_TOKEN` and `GITHUB_TOKEN`, exports `GIT_CONFIG_GLOBAL=<gitconfig>`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_ASKPASS=<askpass.sh>`, `GIT_TERMINAL_PROMPT=0`, `AGENT_RUNNER_NO_TUI=1`, changes to the clone, and `exec`s the Runner:

   ```
   agent-runner run factory-fix \
     --session-dir <evidence>/agent-runner-session \
     --param issue_file=<evidence>/issue.json \
     --param branch_name=<branch> \
     --param contract_version=factory-fix/1 \
     --param artifact_dir=<evidence>
   ```

   The Runner executable path is resolved at plan time with `shutil.which("agent-runner")` on the factory's own PATH and embedded as an absolute path; its `-version` output is captured and recorded.
4. **Plan.** argv `["/bin/bash", "<private>/host-run.sh"]`, `allowed_environment` empty (HOME and PATH are already passed by the supervisor), `credential_files=[fix.env path]`, hints `{"sandbox": "host", "branch_name": ..., "runner_executable": ..., "runner_version": ..., "session_dir": ...}`, no `image_tag`. The persisted plan therefore contains paths only.
5. **Not done in host mode.** No HOME symlink, no Runner settings write, no Skills marketplace bootstrap, no image build.

Docker launch is unchanged apart from passing `--param artifact_dir=/artifacts`.

### Supervision

`_discovers_container(plan)` returns false when `plan.hints.get("sandbox") == "host"`. Termination for a host plan is the existing process-group kill; the Runner and every agent it spawns share the session created by `start_new_session=True`. Ownership verification for a host run checks the recorded PID and start time only and never consults Docker. Progress sources for a host run are the attempt's evidence directory (which now contains the session directory) plus subprocess output, so no new watcher is needed. Restart reconciliation keeps its existing rule: process gone and outcome absent is a technical failure; process alive and verified resumes monitoring.

### Readiness matrix

`Diagnostic` gains `group: Literal["shared", "eval-sandbox", "fix-sandbox", "fix-host"]`. `doctor()` produces:

- `shared`: storage root, free space (with the per-kind floors both reported), GitHub App auth, board sync, Docker reclaimable space (informational, never a hold).
- `eval-sandbox`: Docker daemon, suite prerequisites, memory allowance.
- `fix-sandbox`: `sandbox-run.sh` flags, Docker, credential file, packaged workflow contract, Runner-branch `finalize-pr` feature check.
- `fix-host`: `agent-runner` on PATH and `-version` succeeds, `run --help` lists `--session-dir`, `-validate` passes on the packaged workflow, `git`, `gh`, `jq`, `python3`, `agent-validator` on PATH, `gh auth status` succeeds with the fix token, the configured role CLIs are installed and logged in, the codagent plugin is installed in each, Runner user settings are headless and yolo, credential file, packaged workflow contract.

`format_doctor` prints a heading per group and omits the `action:` line when `available` is true. `readiness.py` builds the fix group for the configured mode only; in host mode `_launch_diagnostic` and the sandbox contract check are not run.

### Runtime gating

`cycle()` replaces the global doctor gate with a per-kind gate: a kind is held when any diagnostic in `shared` or in the groups applicable to that kind under its configured mode is unavailable. The memory probe runs only when a sandbox kind is a candidate for admission. The disk floor check uses the kind's floor. Stored holds are recomputed every poll from the current configuration, so changing `fix.execution` clears a stale Docker hold on the fix kind at the next successful poll while leaving the eval hold in place. `blocked.py` receives the per-kind results instead of a single `memory_available`.

### Provenance and reporting

`read_result` copies `sandbox`, `runner_executable`, `runner_version`, and `session_dir` hints into the run record. `write_evidence` for a host run adds a `host-provenance.json` stating the mode, executable, version, session directory, and that the recorded Runner and Skills commits were not executed. The reporting module adds one sentence to the outcome comment (`pull-request`, `needs-input`, `failed`, exhausted recovery) when the reported run's mode is `host`; the admission comment is untouched.

### Retention

A new `retention.py` in `src/agent_factory/` owns the rule for both kinds. State lives in the claim's existing `cleanup_json`:

```json
{"done_observed_at": "<iso>", "retention": {"pruned_at": "<iso>", "removed": [...], "errors": [...]}}
```

The runtime per-claim loop, which already has the card's board status, calls `retention.reconcile(store, local, claim, board_status, now)` for every claim of the card, including superseded ones (the call is placed before the loop's `superseded` skip). There is no separate sweep: every card on the board is visited each tick, which is what makes evidence predating the rule eligible. `reconcile` does two things.

1. **Observe.** When `board_status == "Done"` and `done_observed_at` is unset, record it. When the status is anything else, clear `done_observed_at`. A claim whose card is not on the board is never visited and therefore never pruned.
2. **Prune** when all of: `board_status == "Done"` in this poll; `now - done_observed_at >= evidence_retention_days`; no run of the claim is non-terminal or of unverified ownership; no pending reporting events; sync completed or not applicable; and, for a settled claim, `cleanup.complete` true (a superseded claim skips this condition because the factory never cleans up its clones or images); and `retention.pruned_at` unset.

Pruning removes an enumerated list of paths under each attempt's evidence directory and keeps everything else, unknown files included. The lists come from the actual layouts written today:

- fix attempt (`artifacts/<claim>/attempt-N/`): `logs/`, `factory-suite.log`, `agent-runner/` (the Docker HOME redirect holding staged workflows and Runner projects), `agent-runner-session/` (the host session), `.runtime/`. Kept: `input/issue.json`, `fix-outcome.json`, `host-provenance.json`, and anything else.
- eval repetition (`artifacts/<claim>-rep-N[-rescore-M]/`): `logs/`, `factory-suite.log`, `.runtime/agent-runner-projects/`, `.runtime/agent-session-state/`, `.runtime/judge-workspace/`, `.runtime/judge/`. Kept: `result.json`, `run-state.json`, `phases/`, `evidence/`, `neutral/`, `report.html`, diffs and manifests, and anything else. `.runtime/candidate-worktree/` is a git worktree governed by the existing worktree cleanup requirement and is not touched by retention.

Removed paths and failures are recorded under `cleanup_json.retention` (`pruned_at`, `removed`, `errors`); a failure leaves `pruned_at` unset so the next tick retries, and success is logged once.

### Status

`status()` includes a claim when its lifecycle is non-terminal, or it has a non-terminal run, pending reporting events, a pending sync, or `cleanup.complete` is false after Review. Superseded claims are never live. `status --all` lists everything as today. The header line reports the number of hidden settled claims.

### Service PATH

Installation stays manual: the operator renders `packaging/launchd/com.codagent.agent-factory.plist` from the template as today. `_PLIST_TEMPLATE` gains a `PATH` entry in `EnvironmentVariables` with a `__PATH__` token, and `docs/installation.md` tells the operator to fill it with the PATH that resolves `agent-runner`, `gh`, and the role CLIs. There is no `install-service` command and none is added.

Two `shared` diagnostics close the gap between an interactive `doctor` and the service: `doctor` prints the PATH it resolved executables against, and when `~/Library/LaunchAgents/com.codagent.agent-factory.plist` exists it parses that file with `plistlib`, reads `EnvironmentVariables.PATH`, and checks that every host-mode executable (`agent-runner`, `git`, `gh`, `jq`, `python3`, `agent-validator`, the configured role CLIs) resolves on it, naming the file and the missing executable on failure. When the plist is absent the line is informational. Host readiness for admission still resolves on the running process's PATH, which under launchd is the plist PATH.

### Docs

`docs/installation.md` gains a "Host execution for fixes" section under fix-kind prerequisites (PATH, Runner flag requirement, plugin, logins). `docs/operations.md` documents the mode setting, the readiness groups, retention, `status --all`, and states plainly that the fix token and the repository ruleset are conventions the factory cannot enforce in host mode. `docs/github-setup.md` is corrected to describe the harness as a branch.

## Decisions

- **Project-local workflow staging.** Chosen over staging into `~/.agent-runner/workflows` (violates the no-user-level-writes rule) and over passing the workflow file path directly (the workflow calls `builtin:core/finalize-pr`, and project scope is the discovery mode the Runner PR #89 was written for). Removal is automatic with the clone.
- **Runner `--session-dir` flag rather than discovery.** The library option already exists; the flag makes the session location deterministic, keeps it inside the evidence directory, and removes any host-specific progress or retention path handling. Discovery by encoded project path and glob was considered and rejected as heuristic and fragile against manual Runner runs in the same clone.
- **Optional `artifact_dir` under the same contract.** The default reproduces current behaviour so `factory-fix/1` remains truthful. A contract bump would force a coordinated change for no consumer benefit since the workflow ships with the factory.
- **Wrapper script sourcing the credential.** The supervisor persists `allowed_environment`, so the token cannot travel through the plan. The wrapper reads the private file at exec time; the plan records only paths. This is the same trust boundary as the Docker `container_script`.
- **Per-attempt `GIT_CONFIG_GLOBAL`.** Process-local, inherited by every child including the agents' own git calls, and it prevents the operator's global config (helpers, `insteadOf`, signing) from being read at all. Per-command `-c` flags would not reach agent-issued git commands.
- **PATH-only Runner resolution.** Matches how `gh` and the CLIs are found; a config override was rejected to keep one source of truth for "installed".
- **Retention state in `cleanup_json`.** Avoids a schema migration; the record is small and already claim-scoped.
- **Retention removes an enumerated list and keeps the rest.** The spec names what to remove (logs, session state, agent output); an allowlist would have deleted anything a suite adds later, and the eval layout spreads provenance across `phases/`, `evidence/`, and `neutral/`. A removal list is the conservative inverse.
- **Current-Done from the same poll.** The board observation that prunes is the one taken in that tick, so a card moved back out of Done can never be pruned on a stale timestamp.
- **Live-only status by default.** The handoff reported the full list as noise; settled claims remain reachable with `--all`.

## Risks / Trade-offs

- **Host mode has no isolation.** Agents run with the operator's user, HOME, and logins. Mitigated by the yolo permission mode already being the operator's choice, the token being the only credential injected, and documentation stating what is not enforced. Alternative rejected: per-attempt HOME, which the operator ruled out to reuse existing logins.
- **Token exposure to agents.** In host mode the token is in the process environment of every agent, as it already is inside the container. Unchanged risk profile; documented.
- **Installed Runner drift.** The recorded Runner commit is not what executes. Mitigated by recording the executable path and version on the run and stating it on the outcome comment. The `dev` version string limits usefulness; readiness could later hash the binary if needed.
- **Readiness cannot fully prove CLI login state.** `gh auth status` and plugin presence are checked; model-provider logins are probed only as far as each CLI exposes. A failure inside the attempt surfaces as a normal workflow failure with evidence.
- **Retention deletes evidence.** Eligibility is strict and observation-based; the first sweep after upgrade records observation and waits the full period, so nothing is removed on upgrade day.
- **Companion Runner PR ordering.** The factory's host readiness refuses to launch until the installed binary lists `--session-dir`, so a stale binary yields a named hold rather than a broken attempt.

## Migration Plan

1. Land and build the Runner `--session-dir` PR. Done: Codagent-AI/agent-runner PR #90 (commit `ca87072`) is merged and the installed binary lists the flag.
2. Ship this change. Default `fix.execution = "docker"` keeps behaviour identical for existing deployments; the only visible differences are grouped doctor output, live-only status, the PATH token in the plist template for the next manual render, and retention starting its 14-day clock from first observation.
3. Operators opting into host mode set `[fix] execution = "host"`, re-render and reload the LaunchAgent with the PATH filled in, and check `doctor` until the `fix-host` group passes.
4. Rollback is setting `execution` back to `docker`; running host attempts finish in host mode, later attempts use Docker. No data migration to reverse.

## Open Questions

None.
