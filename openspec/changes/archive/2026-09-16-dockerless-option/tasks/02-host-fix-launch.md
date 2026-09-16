# Task: Host execution mode for fix attempts: workflow artifact directory, host launcher, process-only supervision, provenance, and host readiness

## Goal

Let the fix kind run its packaged workflow directly on the operator's Mac through the installed Agent Runner when local configuration sets `fix.execution = "host"`, while `docker` (the default) keeps today's sandbox launch byte-for-byte. The host launch reuses the operator's real HOME and logins but writes nothing user-level, keeps the fix token out of every persisted place, is supervised by process ownership alone, records honest provenance (which Runner actually ran, where its session is, and that the recorded Runner and Skills commits were not executed), and adds a host sentence to the outcome comment. The packaged workflow stops hardcoding `/artifacts` so one workflow serves both modes. Fix-kind readiness evaluates the host prerequisites in host mode and holds the bug, with no Docker fallback, when any is missing.

## Background

Planning sources in this repository: `openspec/changes/dockerless-option/proposal.md`, `design.md` (sections "Configuration", "Workflow contract", "Host launch", "Supervision", "Readiness matrix" for the `fix-host` list, "Provenance and reporting", "Decisions"), the spec deltas under `openspec/changes/dockerless-option/specs/`, and `test-plan.md`. Read all of them before starting.

Paul, the operator, has made these decisions; do not reopen them: host attempts run as his user with his real HOME, his CLI logins, and his installed codagent plugin, in the Runner's headless yolo mode; the installed `agent-runner` binary runs rather than the recorded Runner commit; the separate fix token stays; a host readiness failure holds the bug rather than falling back to Docker; the contract stays `factory-fix/1`.

Precondition in the sibling repository: `agent-runner run` accepts `--session-dir <path>` (plumbed to the library's `runner.Options.SessionDir`), and the operator's installed binary (`/Users/paul/.local/bin/agent-runner`, `-version` prints `dev`) lists it in `run --help`. If the installed binary lacks the flag, host readiness must report that as the hold; do not work around it.

### Current code you build on

- `src/agent_factory/work_kinds/fix/launch.py`: `build_plan(...)` assembles the Docker `ExecutionPlan` (argv = `scripts/sandbox-run.sh --image <tag> --artifact-dir <evidence> --no-default-secrets --env-file <credential copy> ... -- <container_script>`), hints `{"artifact_path", "image_tag", "sandbox": "docker", "branch_name"}`, `credential_files=(credential copy,)`, empty `allowed_environment`. `container_script(...)` is the bash body run in the sandbox: it redirects `$HOME/.agent-runner` into `/artifacts/agent-runner`, writes Runner settings, an askpass helper, `git config --global` identity, bootstraps the Skills marketplace into each CLI, writes `/workspace/repo/.agent-runner/config.yaml` (role profiles) and adds it to `.git/info/exclude`, then runs `agent-runner run factory-fix --param issue_file=/artifacts/input/issue.json --param branch_name=... --param contract_version=...`. `stage_workflow(evidence, contract)` copies `factory-fix-v1.0.yaml` plus `record-triage.sh`, `record-outcome.sh`, `read-regression-marker.sh` into `<evidence>/agent-runner/workflows/` (the sandbox's redirected user-level catalog). `validated_credential_copy(local, destination)` writes the single-line `GH_TOKEN=` file. `check_runner_contract(runner_clone, contract)` verifies the packaged workflow's contract marker, `finalize-pr`'s `ci_fix_cycles` parameter, and the `sandbox-run.sh` flags.
- `src/agent_factory/work_kinds/fix/handler.py`: `FixHandler.plan()` creates the attempt evidence directory (`attempt_evidence(run)` = `<run.evidence_path>/attempt-N`), writes `input/issue.json`, stages the workflow, writes the credential copy to `<storage_root>/private/<run_id>/fix.env`, and calls `launch.build_plan`. `read_result()` copies `image_tag` and `branch_name` hints into the run result. `readiness()` wraps `readiness.check_readiness`. `cleanup()` delegates to `cleanup.FixCleanup.reconcile`, which removes clones, calls `images.remove_images(run_image_tags(...))`, and deletes credential copies (`plan.credential_files` and `<private>/<run_id>`).
- `src/agent_factory/work_kinds/fix/readiness.py`: `check_readiness(local, shared, installation_token=...)` returns `[_launch_diagnostic, _credential_diagnostic, _contract_diagnostic]`; `_launch_diagnostic` inspects `sandbox-run.sh` at the Runner branch head and requires `docker` on PATH.
- `src/agent_factory/supervisor.py`: `_launch_and_observe` runs `plan.argv` with `subprocess.Popen(..., start_new_session=True)` and an environment restricted to PATH/HOME/TMPDIR/LANG/LC_ALL plus `plan.allowed_environment`; `_plan_document` persists the plan verbatim into SQLite; `_discovers_container(plan)` is true for hints `suite == "and-scene"` or `sandbox == "docker"` and drives `discover_container` (`docker ps --filter volume=/artifacts`), `stop_owned_container`, and ownership checks; termination is `os.killpg` on the process group via `_terminate`; progress is tracked from `plan.progress_sources` (paths and `glob:` patterns) plus subprocess output.
- `src/agent_factory/operations.py`: `Diagnostic(name, available, detail, action)`; `format_doctor` prints every diagnostic with an `action:` line and starts a `-- fix --` section at the first name prefixed `fix `.
- `src/agent_factory/config.py`: `FixLocalConfig(limits, schedule)` parsed by `_fix_local_config`; `LocalConfig.from_toml`; `ConfigurationError`. `config/local.example.toml` documents local keys.
- `src/agent_factory/controller.py`: `ExecutionPlan(argv, working_directory, allowed_environment, credential_files, progress_sources, ownership_hints, resume)`; `Controller` delivers report events by calling `handler.attempt_message(run, stored_result, stage=...)` with stages `complete`, `retry`, `exhausted`; the `pull-request`, `needs-input`, and `failed` bodies are built by `FixHandler.settle` (`_pr_message`, `_failed_message`) and the `needs-input` event recorded there.
- Packaged workflow `src/agent_factory/work_kinds/fix/workflow/factory-fix-v1.0.yaml` hardcodes `/artifacts` in: the `outcome_path: /artifacts/fix-outcome.json` argument to both `record-triage.sh` and `record-outcome.sh`, `mkdir -p /artifacts/logs` and the validator log redirects in two validator steps, and the final verify step (`[ ! -s /artifacts/fix-outcome.json ]`, the `jq`/`python3` reads). Both scripts default `outcome_path` to `/artifacts/fix-outcome.json` when the argument is absent.
- Agent Runner discovers workflows from `<projectDir>/.agent-runner/workflows/` first, then `~/.agent-runner/workflows/`, then builtins; `AGENT_RUNNER_NO_TUI=1` makes `run` headless; the operator's user settings file already has `autonomous_backend: headless` and `autonomous_permission_mode: yolo`; `agent-runner -validate <file>` validates a workflow file; `agent-runner -version` prints the version.

### What to build

**Configuration.** `FixLocalConfig.execution: Literal["docker", "host"]`, default `"docker"`, TOML `[fix] execution`; any other value is a `ConfigurationError` naming the key. An `[eval] execution` key set to `"host"` is rejected at load with a message stating that eval host execution is unsupported (`"docker"` is accepted as the only value). Document `execution` in `config/local.example.toml`.

**Workflow contract.** The packaged workflow declares an optional parameter `artifact_dir` with default `/artifacts`; every hardcoded `/artifacts` above becomes `{{ artifact_dir }}` (outcome path for both scripts, the log directory and redirects, the verify step). The contract marker stays `# factory-contract: factory-fix/1`. The Docker `container_script` passes `--param artifact_dir=/artifacts` explicitly. `check_runner_contract` (and whatever readiness check reads the packaged workflow) additionally fails when the packaged workflow does not declare `artifact_dir` or still contains a literal `/artifacts` outside that parameter's default, with a diagnostic naming the problem.

**Host plan.** Add `build_host_plan(...)` in `launch.py` beside `build_plan`, and have `FixHandler.plan()` choose by `local.fix.execution`. Generalise `stage_workflow` to take the destination directory. The host plan:

1. Stages the workflow and its scripts into `<target clone>/.agent-runner/workflows/` and appends `/.agent-runner/workflows/` (and the existing `/.agent-runner/config.yaml`) to the clone's `.git/info/exclude`; writes the role-profile config to `<target clone>/.agent-runner/config.yaml` exactly as the container script does today. Nothing is written under `~/.agent-runner`.
2. Writes, under `<storage_root>/private/<run_id>/` (directory mode 0700, files 0600 or 0700): `fix.env` (the existing validated credential copy), `gitconfig` (sets `user.name`, `user.email`, an empty `credential.helper` entry that disables inherited helpers, `core.askPass` pointing at the askpass script, and blank overrides that neutralise any `url.*.insteadOf` and `http.extraHeader` from the operator's global config), `askpass.sh` (answers the username prompt with `x-access-token` and the password prompt with `$GH_TOKEN`), and `host-run.sh`.
3. `host-run.sh` (mode 0700) is the argv target: `set -euo pipefail`; source `fix.env`; export `GH_TOKEN` and `GITHUB_TOKEN`; export `GIT_CONFIG_GLOBAL=<gitconfig>`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_ASKPASS=<askpass.sh>`, `GIT_TERMINAL_PROMPT=0`, `AGENT_RUNNER_NO_TUI=1`; `cd` to the target clone; then `exec <absolute runner path> run factory-fix --session-dir <evidence>/agent-runner-session --param issue_file=<evidence>/input/issue.json --param branch_name=<branch> --param contract_version=factory-fix/1 --param artifact_dir=<evidence>`, with output appended to `<evidence>/logs/agent-runner.log` in the same way the Docker path tees it. The git identity comes from `gh api user -q .login` with the fix token, falling back to `agent-factory`, as the container script does today; performing that lookup inside the wrapper is acceptable.
4. Resolves the Runner at plan time with `shutil.which("agent-runner")` on the factory's own PATH and embeds the absolute path; captures `agent-runner -version` output.
5. Returns `ExecutionPlan(argv=("/bin/bash", "<private>/host-run.sh"), working_directory=<target clone>, allowed_environment={}, credential_files=(<fix.env>,), progress_sources=(<evidence>/factory-suite.log, <evidence>/logs/agent-runner.log, glob patterns for <evidence>/agent-runner-session/** state, audit, and output, plus the Cursor and Claude session globs the Docker plan already lists), ownership_hints={"artifact_path": <evidence>, "sandbox": "host", "branch_name": ..., "runner_executable": ..., "runner_version": ..., "session_dir": <evidence>/agent-runner-session}, resume=False)`. No `image_tag`.
6. Does not: symlink HOME, write Runner user settings, bootstrap the Skills marketplace, build an image, or read the token into Python memory beyond validating the credential file.

The persisted plan document, argv, wrapper, and factory logs must never contain the token value.

**Supervision.** `_discovers_container(plan)` returns false when `plan.ownership_hints.get("sandbox") == "host"`; every Docker call in the supervisor (discovery, inspection, stop, ownership matching, the recorded container in progress) is skipped for such plans, and ownership verification uses the recorded PID and start time only. Termination remains the process-group kill, which covers the Runner and the agents it spawns. Restart reconciliation keeps its rule: process gone and outcome absent is a technical failure; process alive and verified resumes monitoring. Progress from the session directory globs keeps a quiet attempt alive while the Runner writes state.

**Provenance and reporting.** `read_result` copies `sandbox`, `runner_executable`, `runner_version`, and `session_dir` hints into the run result alongside `image_tag` and `branch_name`. Write `<evidence>/host-provenance.json` when the host plan is built, before launch, with the mode, executable path, reported version, session directory, and a statement that the claim's recorded Runner and Skills commits were not the versions that executed. Every fact it holds is known at plan time, and writing it there guarantees it exists for every host attempt however it ends, including attempts cancelled by issue closure or interrupted by a limit, which the runtime's result consumption never revisits for cancelled claims. Do not make it depend on `read_result`. The claim's frozen revisions are unchanged. The outcome messages for `pull-request`, `needs-input`, `failed`, and the exhausted-recovery message gain one sentence when the reported run's `sandbox` is `host`: that the attempt ran on the host with the operator's installed Runner and Skills rather than the recorded commits. The admission comment and every Docker-run comment are unchanged.

**Cleanup.** `FixCleanup.reconcile` removes clones (which removes the staged workflow) and credential copies for host claims and invokes Docker only when the claim has recorded image tags; a claim whose attempts all ran on the host never calls `docker`.

**Host readiness.** In `readiness.py`, when `local.fix.execution == "host"`, `check_readiness` builds the `fix-host` diagnostics and runs neither `_launch_diagnostic` nor any check of the recorded Runner branch. Split today's `_contract_diagnostic`, which both reads the packaged workflow and resolves the Runner branch head to inspect its `finalize-pr` workflow, into three parts: a packaged-workflow check common to both modes (contract marker present, `artifact_dir` declared, no stray literal `/artifacts`); a Docker-only check of the recorded Runner branch's `finalize-pr` parameter and `sandbox-run.sh` flags (group `fix-sandbox`); and a host-only check that the installed executable's `-validate` accepts the packaged workflow (group `fix-host`). Host admission must never depend on the compatibility of the recorded Runner commit, because that commit does not execute in host mode. The `fix-host` diagnostics are: `agent-runner` resolves on PATH and `-version` succeeds; `run --help` lists `--session-dir`; `-validate` accepts the packaged workflow; `git`, `gh`, `jq`, `python3`, and `agent-validator` resolve on PATH; `gh auth status` succeeds with the fix token (`GH_TOKEN` from the credential file, in the subprocess environment only); each CLI selected by the configured fix roles is installed, authenticated, and carries the codagent plugin, using this table (the role profile names the Runner adapter, whose executable differs from the adapter name for Cursor, see `internal/cli/cursor.go::ExecutableName` in agent-runner; the supplied `config/codagent.toml` selects `cursor` for all three fix roles):

| Adapter | Executable | Authentication probe | Plugin evidence |
| --- | --- | --- | --- |
| `claude` | `claude` | `claude auth status` exits 0 | `claude plugin list` output names `codagent` |
| `codex` | `codex` | `codex login status` exits 0 | `codex plugin list --json` contains an installed plugin named `codagent` |
| `cursor` | `agent` | `agent status` exits 0 and reports a login | `agent plugin marketplace list` names the `codagent` marketplace |

All probes are read-only and run with a short timeout; the operator's Runner user settings (`~/.agent-runner/settings.yaml`) must select `autonomous_backend: headless` and `autonomous_permission_mode: yolo`; plus the existing credential diagnostics. Every failure names the action, and the existing runtime already turns fix readiness failures into a `readiness:fix` hold with no launch. Doctor never edits the settings file. Export the adapter-to-executable table from one place (for example a small mapping in `readiness.py` or `operations.py`) so that doctor's service-PATH check can reuse the same executable names. Add a `group` field to `Diagnostic` (`"shared" | "eval-sandbox" | "fix-sandbox" | "fix-host"`, default `"shared"`) if it does not exist yet, and set `fix-host` on these diagnostics and `fix-sandbox` on the Docker-mode fix launch diagnostics; grouping the remaining diagnostics and the doctor headings are outside this task, but `format_doctor` must keep printing these new diagnostics.

## Spec

From `specs/factory-operations/spec.md`, requirement "Configure deployment without Codagent-specific controller code" (this task's portion: the execution mode and the eval rejection):

> [...] the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. The eval kind SHALL accept only sandbox execution. The execution mode, fix disk floor, and retention period SHALL be local configuration.

> #### Scenario: Leave the execution mode unset
> - **WHEN** the local configuration names no fix execution mode
> - **THEN** fixes run in the sandbox exactly as before this change
>
> #### Scenario: Configure host execution for evals
> - **WHEN** the local configuration requests host execution for the eval kind
> - **THEN** the factory reports the unsupported setting at startup and in doctor and admits no eval

From `specs/factory-fix-execution/spec.md`:

> ### Requirement: Invoke the versioned fix workflow
>
> The factory SHALL run the packaged fix workflow in the execution mode configured for the fix kind, `docker` or `host`, passing the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, and the attempt's artifact directory. In `docker` mode the workflow runs in the existing sandbox through the Runner sandbox script with a per-run image tag; in `host` mode it runs as described by the host execution requirement. The workflow SHALL write its outcome and any intermediate records under the supplied artifact directory and SHALL NOT assume a fixed container path. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the Runner in use does not support what the workflow requires, and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure. A running attempt SHALL keep the mode it launched with; a recovery retry SHALL launch in the currently configured mode.
>
> #### Scenario: Launch in Docker mode
> - **WHEN** the fix kind is configured for `docker` and the packaged workflow declares the expected contract
> - **THEN** the attempt starts under its own supervisor in the sandbox with the configured roles and a run-specific image tag
> - **AND** the workflow receives the sandbox's artifact mount as its artifact directory
>
> #### Scenario: Launch in host mode
> - **WHEN** the fix kind is configured for `host` and host readiness passes
> - **THEN** the attempt starts under its own supervisor on the host with the configured roles and no image tag
> - **AND** the workflow receives the attempt's artifact directory under the storage root and writes `fix-outcome.json` there
>
> #### Scenario: Launch with an incompatible workflow
> - **WHEN** the packaged workflow declares an unsupported contract version or the Runner in use lacks a feature the workflow requires
> - **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility
>
> #### Scenario: Finish without an outcome
> - **WHEN** the workflow exits without writing a structured outcome
> - **THEN** the factory records a technical failure and applies the recovery policy
>
> #### Scenario: Change the mode while an attempt runs
> - **WHEN** the operator changes the fix execution mode while a fix attempt is running
> - **THEN** the running attempt continues in the mode it launched with
> - **AND** a later recovery retry or new attempt launches in the newly configured mode
>
> ### Requirement: Isolate concurrent sandbox builds
>
> Each Docker-mode attempt SHALL build and run its sandbox under a unique image tag derived from its run identity, so an eval attempt and a fix attempt building from different Runner checkouts never overwrite each other's image. The run record SHALL store the image tag used. Run-specific images SHALL be removed together with the claim's clones when its card reaches Done, and SHALL be retained while the claim is running, waiting, blocked, or in Review. A host-mode attempt SHALL record no image tag and cleanup SHALL attempt no image removal for it.
>
> #### Scenario: Build while an eval is running
> - **WHEN** a Docker-mode fix attempt starts while an eval attempt's container is running
> - **THEN** the fix builds and runs under its own tag and the eval's image and provenance are unaffected
>
> #### Scenario: Clean up a host attempt
> - **WHEN** a claim whose attempts all ran in host mode reaches Done
> - **THEN** cleanup removes its clones and does not invoke Docker
>
> ### Requirement: Preserve fix evidence
>
> The factory SHALL retain each attempt's workflow output, structured outcome, validator and CI results as available, and the PR reference under the attempt's artifact directory, recorded in SQLite. For a host-mode attempt it SHALL additionally record the Runner session directory the attempt used and treat that directory as part of the attempt's evidence. Evidence SHALL survive clone cleanup and SHALL be retained until the evidence retention rule in `factory-operations` removes it.
>
> #### Scenario: Inspect a declined attempt
> - **WHEN** an operator inspects a `needs-input` attempt after clone cleanup
> - **THEN** the attempt's reasons and workflow output remain available under its artifact directory
>
> #### Scenario: Locate a host attempt's session
> - **WHEN** an operator inspects a host-mode attempt
> - **THEN** the run record names the Runner session directory and the attempt's evidence references it
>
> ### Requirement: Run a fix attempt on the host
>
> In `host` mode the factory SHALL run the packaged fix workflow through the operator's installed Agent Runner, from the attempt's target clone, with the operator's own HOME so the selected CLIs use the operator's existing logins and installed codagent plugin. The launch SHALL write nothing to the operator's user-level configuration: git identity, the askpass helper, suppression of credential helpers and terminal prompts, and suppression of URL rewriting and extra headers SHALL be applied as process-local settings of the launched process; role profiles SHALL be written repo-locally in the clone; the packaged workflow SHALL be supplied to the Runner through the attempt's target clone, in the clone's project-local Runner workflow directory excluded from version control, so that it is removed with the clone and no file is left in the operator's Runner configuration; the Runner SHALL be given a session directory under the attempt's artifact directory; and the Skills plugin bootstrap SHALL be skipped. Git and `gh` operations of the launched process SHALL authenticate with the fix token. The Runner SHALL be told not to open an interactive interface. A host readiness failure SHALL hold the bug without falling back to Docker.
>
> #### Scenario: Supply the workflow through the clone
> - **WHEN** a host-mode attempt launches
> - **THEN** the packaged workflow and its scripts are present only in the attempt's clone, ignored by git there, and the Runner resolves `factory-fix` from that project scope
> - **AND** removing the clone at Done leaves no copy of the workflow behind
>
> #### Scenario: Launch without touching the operator's configuration
> - **WHEN** a host-mode attempt launches and finishes
> - **THEN** the operator's global git configuration, Runner user settings, Runner workflow directory, and CLI plugin configuration are unchanged
>
> #### Scenario: Push with the fix token
> - **WHEN** the workflow pushes the fix branch and opens the PR in host mode
> - **THEN** the push and the PR are authenticated as the fix credential's identity, not the operator's stored login
>
> #### Scenario: Skip the Skills bootstrap
> - **WHEN** a host-mode attempt launches
> - **THEN** no plugin marketplace is added or replaced in any CLI
> - **AND** the agents use the operator's installed codagent plugin
>
> #### Scenario: Fail host readiness
> - **WHEN** the fix kind is configured for `host` and a host prerequisite is unavailable
> - **THEN** the bug is held with the reason and no Docker attempt is launched in its place
>
> ### Requirement: Keep the fix credential out of persisted state
>
> In both execution modes the fix token SHALL NOT appear in the persisted execution plan, the SQLite run record, the launched command line, factory logs, or attempt evidence. The plan MAY record the path of the factory's private credential copy. The credential copy SHALL be deleted when the claim reaches Done, as today.
>
> #### Scenario: Inspect the run record after launch
> - **WHEN** an operator inspects the persisted plan of a host-mode attempt
> - **THEN** it names the credential file path and contains no token value
>
> ### Requirement: Supervise a host attempt by process
>
> A host-mode attempt SHALL be owned through its recorded process identity alone; the factory SHALL NOT attempt container discovery, container inspection, or container termination for it. Stopping owned execution for cancellation, a limit, or recovery cleanup SHALL stop the Runner process and the agent processes it started. Restart reconciliation SHALL treat a host attempt whose process is gone and whose outcome is absent as a technical failure subject to the recovery policy, and SHALL resume monitoring a verified surviving process. Progress SHALL include activity in the attempt's artifact directory and in the recorded Runner session directory.
>
> #### Scenario: Cancel a host attempt
> - **WHEN** the issue of a running host-mode fix is closed
> - **THEN** the factory stops the Runner and its child agent processes and no agent process of that attempt keeps running
>
> #### Scenario: Restart while a host attempt runs
> - **WHEN** the controller restarts while a host-mode attempt's process is still running
> - **THEN** the factory resumes monitoring that process without launching another attempt or consuming a retry
>
> #### Scenario: Make progress through the session directory
> - **WHEN** subprocess output is quiet but the Runner session directory continues to advance
> - **THEN** the factory recognizes progress rather than stopping the attempt for inactivity
>
> ### Requirement: Record host provenance
>
> For a host-mode attempt the run record SHALL store the execution mode, the path and reported version of the Runner executable that ran, and the Runner session directory. The attempt's evidence SHALL state that the attempt ran on the host and that the claim's recorded Runner and Skills commits were not the versions that executed. The claim's recorded commits SHALL be unchanged.
>
> #### Scenario: Inspect a host attempt's provenance
> - **WHEN** an operator inspects a host-mode attempt
> - **THEN** the run record shows mode `host`, the Runner executable and version used, and the session directory
> - **AND** the evidence states that the recorded Runner and Skills commits were not executed

From `specs/factory-fix-reporting/spec.md`:

> ### Requirement: Comment on fix activity
>
> The factory SHALL comment on the issue when it admits a bug (including the resolved refs and attempt number), when an attempt is declined, fails, is retried, is cancelled, or produces a PR, and when a post-merge sync succeeds or is blocked. Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state. When the attempt being reported ran in host mode, the comment reporting its outcome (`pull-request`, `needs-input`, `failed`, or exhausted recovery) SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The admission comment SHALL be unchanged.
>
> #### Scenario: Admit a bug
> - **WHEN** the factory admits a bug
> - **THEN** the issue receives one comment naming the target, Runner, and Skills commits and the attempt number
>
> #### Scenario: Report a host-mode PR
> - **WHEN** a host-mode attempt returns `pull-request`
> - **THEN** the PR comment links the PR and states that the attempt ran on the host with the operator's installed Runner and Skills rather than the recorded commits
>
> #### Scenario: Report a Docker-mode outcome
> - **WHEN** a Docker-mode attempt returns any outcome
> - **THEN** its outcome comment carries no host note

From `specs/factory-claim-lifecycle/spec.md`, requirement "Verify execution ownership before termination" (this task's portion: the host clause):

> A sandbox attempt SHALL be verified through its process and container; a host attempt SHALL be verified through its recorded process identity alone and no container evidence SHALL be expected for it.
>
> #### Scenario: Find a host process that no longer matches
> - **WHEN** the process recorded for a host attempt is gone or its identity no longer matches the recorded attempt
> - **THEN** the factory terminates nothing, treats the attempt's execution as unverified, and resolves it through reconciliation before launching new work of that kind

and requirement "Enforce separate progress and runtime limits" (this task's portion): "Progress SHALL include activity in known suite logs and saved state as well as subprocess output, and for a host attempt activity in its recorded Runner session directory."

## Test Plan

Obligations from `openspec/changes/dockerless-option/test-plan.md` assigned to this task. Read each entry there for the full setup and assertion lists; the summaries below name the boundary and the completion signal.

- `INT-001` (`tests/integration/test_host_launch.py`): build the host plan against a real storage root, git-initialised clone, `fix.env` with `GH_TOKEN=dummy-fix-token`, and a stub `agent-runner` on PATH answering `-version` and `run --help`; serialise it the way the supervisor persists plans. Passes when the private files exist with owner-only modes and the expected git settings, the workflow lives only under `<clone>/.agent-runner/workflows/` and is listed in `.git/info/exclude`, nothing appears under the test HOME's `.agent-runner`, the token string appears nowhere in the plan document, argv, or wrapper, argv is `["/bin/bash", <wrapper>]`, the wrapper execs the stub's absolute path with `--session-dir` and `--param artifact_dir`, hints carry `sandbox: "host"`, `runner_executable`, `runner_version`, `session_dir`, `branch_name` and no `image_tag`, and `allowed_environment` is empty.
- `INT-002` (`tests/integration/test_fix_workflow.py`): the shipped workflow passes the contract check, declares `artifact_dir` with default `/artifacts`, and contains no other literal `/artifacts`; a variant without the parameter and a variant with a literal path each fail with a diagnostic naming the problem.
- `INT-003`, host-group portion only (`tests/integration/test_fix_readiness.py`): table-driven over the `fix-host` checks, each of these yields a failing `fix-host` diagnostic with a named action, a `readiness:fix` hold, and no launch: a role CLI executable missing from PATH (for the `cursor` adapter that executable is `agent`), a role CLI whose auth probe fails, a role CLI without the codagent plugin, Runner user settings not headless or not yolo, a stub Runner whose `run --help` omits `--session-dir`, a stub Runner whose `-validate` rejects the packaged workflow, a fix token whose `gh auth status` fails, a fix token equal to the installation token. The per-kind doctor headings and the Docker-versus-host gating of the eval kind are covered elsewhere.
- `INT-004` (`tests/integration/test_supervision_hardening.py`, marked `darwin`): a plan hinting `sandbox: "host"` whose argv script spawns a sleeping child and touches files under `<evidence>/agent-runner-session/`, with no `docker` on PATH. Passes when no container discovery is attempted, termination leaves neither script nor child, a silent variant survives inactivity while session writes continue and is stopped once they cease, and the post-termination ownership check reports the process gone without consulting Docker.
- `INT-008` (`tests/integration/test_fix_sync_reporting.py`): one claim with a `host` run and one with a `docker` run, each producing `pull-request`, `needs-input`, and `failed`; every host outcome comment carries the host sentence, no Docker comment does, and the admission comment is identical across modes.
- `INT-007`, configuration portion only (`tests/integration/test_fix_config.py`): `fix.execution` defaults to `docker` and accepts `host`; `[eval] execution = "host"` is rejected with a message naming eval host execution as unsupported.
- `E2E-001` (`tests/e2e/test_host_fix_launch.py`, marked `darwin`): skips with "Required separate host check: install agent-runner with --session-dir; not passing evidence" unless the real `agent-runner` is on PATH and lists `--session-dir`. With a temporary HOME holding a planted `.gitconfig` (credential helper and `insteadOf`), a planted `~/.agent-runner/workflows/factory-fix-v1.0.yaml` that would write a marker if selected, and headless Runner settings, a model-free stand-in `factory-fix` workflow (like `TEST_WORKFLOW` in `tests/e2e/test_docker_fix_launch.py`, using `{{artifact_dir}}`) is staged by the real host plan into a fresh clone and run under the real supervisor. Passes when `fix-outcome.json` and `agent-runner-session/` exist under the evidence directory, the planted workflow's marker was not written, the recorded identity is the factory identity with an empty credential helper and no URL rewrite, `GH_TOKEN` is among the recorded environment names, the planted HOME files are byte-identical and nothing appeared under the temporary `~/.agent-runner/projects`, and the run record carries mode `host`, the Runner path and version, and the session directory. A second case whose step records its PID, spawns a blocking grandchild, and is terminated by the supervisor passes when the Runner, step, and grandchild are all gone within the grace period and no `docker` executable was consulted.

Unit tests for configuration parsing, the wrapper and gitconfig text, the workflow parameter substitution, `read_result` provenance fields, the host sentence in messages, and cleanup's Docker-avoidance are implementation-time TDD decisions.

## Done When

- Every scenario copied above passes under automated tests, and the assigned `INT-001`, `INT-002`, `INT-003` (host group), `INT-004`, `INT-007` (configuration), `INT-008`, and `E2E-001` obligations are implemented in the named files and pass. E2E-001 carries the documented skip for machines whose installed Runner lacks `--session-dir`, but on this Mac the installed Runner is expected to carry it, and a skip is not passing evidence: the completion report must show E2E-001 executed and passed here. If the installed Runner lacks the flag, the task is blocked on that precondition and must say so rather than report done.
- With `fix.execution` unset or `docker`, the Docker plan is unchanged except for the added `--param artifact_dir=/artifacts`; the existing Docker-marked end-to-end test `tests/e2e/test_docker_fix_launch.py` still passes when run with Docker (`AGENT_FACTORY_DOCKER_SOURCES` set).
- The packaged workflow and both outcome scripts contain no literal `/artifacts` except the `artifact_dir` parameter default; `record-outcome.sh` and `record-triage.sh` still accept an explicit `outcome_path`.
- A host plan document persisted in SQLite contains only paths and never the token; `grep` of the storage root outside `private/` for the token value finds nothing after a host launch.
- `FixCleanup` deletes a host claim's clones and private directory without executing `docker`.
- `host-provenance.json` exists with the required fields for a host attempt that is cancelled or interrupted before writing an outcome, covered by a test that builds a host plan, launches it, cancels it, and checks the file; INT-001 also asserts the file exists immediately after the plan is built.
- `config/local.example.toml` documents `[fix] execution`.
- `uv run ruff check`, `uv run pyright`, and `uv run pytest` (excluding `docker`-marked tests) pass.
