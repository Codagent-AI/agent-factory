# Task: Per-kind readiness matrix, kind-scoped Docker and disk holds, grouped doctor output, service PATH, and host-mode documentation

## Goal

Make Docker a concern of the kinds that use it. Readiness checks are classified as shared, eval-sandbox, fix-sandbox, or fix-host; the runtime applies each kind's classes under its configured mode, so with fixes on the host and Docker stopped a bug is admitted while evals are held with Docker named. The disk floor becomes per kind with an optional lower `fix.minimum_free_gib`. Doctor prints one heading per group, prints no action on passing lines, reports Docker's reclaimable space and the trim command without running it, reports the PATH it resolved executables against, and checks the installed LaunchAgent's PATH so an interactive pass cannot hide a service failure. The LaunchAgent template carries an explicit PATH. Documentation covers host mode, what it keeps and gives up, the non-enforceable controls, and corrects the harness description.

## Background

Planning sources in this repository: `openspec/changes/dockerless-option/proposal.md`, `design.md` (sections "Readiness matrix", "Runtime gating", "Service PATH", "Docs", "Decisions", "Migration Plan"), the spec deltas under `openspec/changes/dockerless-option/specs/`, and `test-plan.md`. Read all of them before starting.

Repository state this task builds on. Local configuration has `[fix] execution = "docker" | "host"` (`FixLocalConfig.execution` in `src/agent_factory/config.py`) and rejects `[eval] execution = "host"`. `FixHandler.readiness()` (`src/agent_factory/work_kinds/fix/handler.py` → `work_kinds/fix/readiness.py`) already returns the host-prerequisite diagnostics in host mode (installed Runner and its `--session-dir` flag, `-validate`, `git`/`gh`/`jq`/`python3`/`agent-validator` on PATH, `gh auth status` with the fix token, role CLIs authenticated and carrying the codagent plugin, Runner user settings headless and yolo, credential, packaged workflow contract) and the sandbox-launch diagnostics in Docker mode, and `Diagnostic` in `src/agent_factory/operations.py` carries a `group` field (`"shared" | "eval-sandbox" | "fix-sandbox" | "fix-host"`, default `"shared"`) that those fix diagnostics already set. If any of that is absent when you start, add it as part of this task rather than working around it. Host launch itself (`launch.build_host_plan`, process-only supervision) exists and is not changed here.

Current runtime and operations behaviour to change:

- `src/agent_factory/runtime.py::cycle`: before considering any kind it runs `check_memory_headroom(local.limits.memory_reservation_gib)` and stores `settings("runtime","memory")`; in the admission loop it skips every kind when memory is unavailable, then runs one global `doctor(local, include_fix=False)` gate whose failures are stored under `settings("runtime", "readiness:<kind>")` for the first ready kind before breaking, then per-kind `handler.readiness(local, shared)`. `process_blocked_claim` in `work_kinds/fix/blocked.py` receives a single `memory_available: bool`. `_launch` raises `ReadinessError` on prerequisite problems and the loop records a `readiness` hold on the claim.
- `src/agent_factory/operations.py::doctor(config, *, include_fix=True)` returns a flat list: shared configuration and harness branch, GitHub App key, repository checks, suite environment, `Docker` (`docker info`), model authentication for the eval role profiles, `_free_space` (one floor, `limits.minimum_free_gib`), the fix diagnostics (`_fix_diagnostics`: mirrors, working clones, credential identity, plus the handler's readiness), and GitHub access. `format_doctor` prints `name: OK|FAIL — detail` and always an `  action:` line, with a `-- fix --` heading before the first `fix `-prefixed name. `check_memory_headroom(reservation_gib)` probes Docker memory. `render_launch_agent(...)` substitutes `__EXECUTABLE__`, `__CONFIG__`, `__ROOT__`, `__LOG__`, `__CREDENTIAL__` into `_PLIST_TEMPLATE`, whose `EnvironmentVariables` holds only `AGENT_FACTORY_ROOT` and `AGENT_FACTORY_GITHUB_APP_KEY`. `packaging/launchd/com.codagent.agent-factory.plist` is the same template as a file and is force-included in the wheel by `pyproject.toml`.
- The eval handler's readiness (`src/agent_factory/work_kinds/eval/` and `src/agent_factory/suites/`) contributes suite prerequisite diagnostics; the and-scene suite launches Docker itself, so its readiness is `eval-sandbox`.
- `docs/installation.md` ("Fix-kind prerequisites", "Configuration", "Install the LaunchAgent"), `docs/operations.md` ("The fix work kind", "Service management and storage"), and `docs/github-setup.md` (which still describes the `agent-evals` harness setting as an immutable SHA pin, while the configuration and `_harness_branch_diagnostic` treat it as a branch resolved at admission).

### What to build

**Readiness matrix.** `doctor()` assigns every diagnostic a group. `shared` holds only what every kind needs: storage root and free space (reporting both floors), the shared configuration file and Project mappings, GitHub App key and access, the Agent Runner and Agent Skills repository checks (both kinds clone them), the Docker reclaimable-space line (informational, `available` always true), the resolved-PATH line, and the LaunchAgent-PATH check. `eval-sandbox` holds everything only evals need plus the sandbox: the Docker daemon, the memory allowance against the reservation, the eval role model authentication, the harness branch diagnostic (`_harness_branch_diagnostic`), the `agent_evals` repository check, the suite entry point and launcher check, the suite environment file (`_suite_environment`, the suite candidate credential), and suite prerequisites. Split `_repository_checks` accordingly so no eval-only failure can enter the shared gate: with fixes on the host, a missing evals checkout, harness branch, suite entry point, or suite credential holds evals only. `fix-sandbox` holds the Docker-mode fix diagnostics; `fix-host` the host-mode ones. Doctor includes the fix group for the configured mode only. `format_doctor` prints a heading per group in the order shared, eval-sandbox, fix-sandbox or fix-host, prints `action:` only when `available` is false, and never prints an empty action. `[eval] execution` set to anything but `docker` is rejected by `LocalConfig.from_toml` with a `ConfigurationError`, which correctly stops `tick`, `resident`, `status`, `pause`, and `resume` at startup (`cli.py::_load_local` exits with `invalid local configuration: ...`). The `doctor` command must not exit there: change `cli.py` so that for `doctor` a `ConfigurationError` from loading the local configuration becomes a single failing `shared` diagnostic (name `local configuration`, detail the error message, action to correct the file), printed through `format_doctor` with exit status 1. Other configuration errors take the same doctor path, so a broken local file always yields a grouped doctor report rather than a bare exit.

**Reclaimable space.** When `docker info` succeeds, run `docker system df --format '{{json .}}'` (or the equivalent that yields reclaimable bytes per category), sum the reclaimable amounts, and add a `shared` diagnostic stating the amount and `docker system prune` / `docker builder prune` as the commands. Never run a prune. When Docker is not running, the line says so and does not fail.

**Per-kind disk floor.** `FixLocalConfig.minimum_free_gib: float | None`, TOML `[fix] minimum_free_gib`, optional; when unset the fix floor is `limits.minimum_free_gib`. Document it in `config/local.example.toml`. The free-space diagnostic reports the free space and both floors, and readiness gating uses the floor of the kind being admitted. A floor failure is a hold for that kind only.

**Runtime gating.** Replace the global gate in `cycle()` with a per-kind gate: a kind is held when any diagnostic in `shared` or in the groups applicable to it under its configured mode is unavailable (eval: `eval-sandbox`; fix in Docker mode: `fix-sandbox`; fix in host mode: `fix-host`). The Docker memory probe runs only when a sandbox kind is a candidate for admission this tick, and its result holds sandbox kinds only; `settings("runtime","memory")` keeps its meaning for status. Recompute and store `settings("runtime","readiness:<kind>")` every poll from the current configuration, so a Docker hold recorded on the fix kind clears on the first successful poll after switching to host while the eval hold remains; a hold whose reason no longer applies is cleared, not left stale. `process_blocked_claim` receives the fix kind's own admission result (memory only when the fix kind uses the sandbox) instead of the global `memory_available`. Docker diagnostics run once per cycle, not once per kind.

**Service PATH.** `_PLIST_TEMPLATE` and `packaging/launchd/com.codagent.agent-factory.plist` gain a `PATH` entry under `EnvironmentVariables` with a `__PATH__` token; `render_launch_agent` takes the PATH value and substitutes it. Installation stays manual; add no install command. Two `shared` diagnostics: (1) the PATH doctor resolved executables against, printed as detail, always available; (2) when `~/Library/LaunchAgents/com.codagent.agent-factory.plist` exists, parse it with `plistlib`, read `EnvironmentVariables.PATH`, and check that every executable host mode needs (`agent-runner`, `git`, `gh`, `jq`, `python3`, `agent-validator`, and the executable of each CLI adapter selected by the fix roles, using the adapter-to-executable mapping the fix readiness module exports: `claude` → `claude`, `codex` → `codex`, `cursor` → `agent`) resolves on that PATH, failing with the plist path and the missing executable named; when the plist is absent the line is informational. This check applies when the fix kind is configured for host; in Docker mode it may be informational. Admission readiness continues to resolve on the running process's PATH, which under launchd is the plist PATH.

**Documentation.** `docs/installation.md`: a "Host execution for fixes" section under the fix-kind prerequisites covering the `[fix] execution` setting, the installed Runner and its `--session-dir` requirement, the codagent plugin in each role CLI, CLI logins, Runner user settings, the LaunchAgent `__PATH__` value (the PATH that resolves `agent-runner`, `gh`, and the role CLIs), and `fix.minimum_free_gib`. `docs/operations.md`: the readiness groups and what each holds, doctor's reclaimable-space line and that the factory never trims Docker, the host-mode evidence layout including the Runner session directory under the attempt, and a plain statement that in host mode on a machine where the operator's own GitHub login is available the separate fix credential and the PR-only rulesets are conventions the launched process follows rather than boundaries an autonomous agent cannot cross, that host attempts run yolo as the operator's user with no filesystem boundary, that neither the recorded Runner nor Skills commit executes, and that trusted-writer admission and human merge are the enforceable controls. `docs/github-setup.md`: describe the harness setting as a branch resolved at admission, not a commit pin, and remove the SHA-pin wording. Update the example plist rendering instructions to include the PATH.

## Spec

From `specs/factory-claim-lifecycle/spec.md`:

> ### Requirement: Classify admission holds by scope
>
> Pause SHALL apply to every kind. The free-disk floor SHALL apply to every kind using the floor configured for the kind being admitted. Docker availability and the memory headroom check SHALL apply only to kinds whose configured execution mode uses the sandbox. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential, and in host mode the host prerequisites) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.
>
> #### Scenario: Hold Codex while fixing with Cursor
> - **WHEN** a Codex quota hold is active and the fix roles use Cursor
> - **THEN** an eligible bug can still be admitted while eval work using Codex waits
>
> #### Scenario: Lose suite readiness
> - **WHEN** the eval suite's prerequisites are unavailable
> - **THEN** eval admission is held and fix admission is unaffected
>
> #### Scenario: Stop Docker while fixes run on the host
> - **WHEN** Docker is unavailable and the fix kind is configured for host execution
> - **THEN** an eligible bug is admitted and eval admission is held with Docker named as the reason
>
> #### Scenario: Apply the fix disk floor
> - **WHEN** free space is below the eval floor but at or above the lower floor configured for fixes
> - **THEN** an eligible bug is admitted and eval admission is held for disk
>
> ### Requirement: Check memory headroom before admission
>
> Before admitting an attempt that will run in the sandbox, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check. An attempt that will run on the host SHALL NOT be subject to the Docker memory probe and SHALL be admitted without it.
>
> #### Scenario: Admit a second attempt with headroom
> - **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
> - **THEN** the second kind's attempt may be admitted
>
> #### Scenario: Wait for memory
> - **WHEN** the remaining Docker allowance is below the reservation
> - **THEN** no new sandbox attempt starts and status names memory as the blocking condition
>
> #### Scenario: Admit a host fix while Docker is stopped
> - **WHEN** the fix kind is configured for host execution and Docker is not running
> - **THEN** the memory probe is not performed for the fix and the bug is admitted subject to the other holds
>
> ### Requirement: Recheck unavailable prerequisites without retrying execution
>
> Before accepting work for execution, the factory SHALL check the readiness that applies to that kind and, before launch, the selected suite's or mode's readiness. Readiness checks SHALL be classified as shared, eval-sandbox, fix-sandbox, or fix-host, and only the classes that apply to a kind under its configured execution mode SHALL hold that kind. An unavailable prerequisite SHALL hold affected work without launching an attempt, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. A recorded hold whose check no longer applies to a kind after a configuration change SHALL clear on the next successful poll. These checks SHALL NOT attempt to repair credentials or configuration.
>
> #### Scenario: Wait for Docker or authentication
> - **WHEN** Docker is unavailable or required authentication is invalid before launch
> - **THEN** the factory reports the prerequisite problem and starts no affected attempt
> - **AND** it rechecks availability without consuming attempts or retries
>
> #### Scenario: Detect a repaired prerequisite
> - **WHEN** the operator fixes a prerequisite and a subsequent readiness check succeeds
> - **THEN** the associated hold clears and affected work may proceed under the other admission controls without restarting the factory
>
> #### Scenario: Switch fixes to the host under a Docker hold
> - **WHEN** a Docker hold is recorded for both kinds and the operator changes the fix kind to host execution
> - **THEN** the next successful poll clears the fix hold, evaluates host readiness for fixes, and leaves the eval hold in place

From `specs/factory-operations/spec.md`, requirement "Configure deployment without Codagent-specific controller code" (this task's portion: the fix disk floor): "[...] and an optional fix-specific minimum free disk space that defaults to the shared minimum. [...] The execution mode, fix disk floor, and retention period SHALL be local configuration."

> ### Requirement: Run as a recoverable per-user Mac service
>
> The change SHALL provide a launchd LaunchAgent configuration and setup instructions that start the factory for the configured Mac user on login and restart the controller if it crashes. Execution SHALL use explicit executable, configuration, and credential paths suitable for the service environment, and the LaunchAgent SHALL supply an explicit PATH that includes every executable host-mode fixes need; the resident controller and the supervisors it launches SHALL resolve executables against that environment. Doctor SHALL report the PATH it resolved executables against and, when the LaunchAgent definition is installed at its documented location, SHALL check that every executable host mode needs also resolves on the PATH that definition carries, naming the definition and the missing executable on failure. Controller restarts SHALL preserve running evaluations as required by `factory-claim-lifecycle`; restarting the controller SHALL NOT itself restart or terminate those evaluations.
>
> The service SHALL poll GitHub every five minutes while independently supervising active execution. A long-running repetition SHALL NOT block queue reconciliation, cancellation checks, or pending report delivery.
>
> #### Scenario: Start the user service
> - **WHEN** the configured Mac user logs in with the LaunchAgent installed and prerequisites available
> - **THEN** the factory starts using its configured paths and credentials and begins normal polling
>
> #### Scenario: Recover a controller crash
> - **WHEN** the controller crashes during an evaluation
> - **THEN** launchd restarts the controller and it reconciles the surviving evaluation without terminating it merely because of the controller restart
>
> #### Scenario: Poll during a long evaluation
> - **WHEN** an evaluation runs for several hours
> - **THEN** the service continues its five-minute GitHub checks and pending reporting independently of that evaluation
>
> #### Scenario: Launch a host fix from the service
> - **WHEN** doctor run interactively reports host readiness and the resident service admits a bug
> - **THEN** the service launches the host attempt with the same resolved executables doctor checked
>
> #### Scenario: Detect a service PATH that lacks the Runner
> - **WHEN** the fix kind is configured for host execution, `agent-runner` resolves on the interactive PATH, and the installed LaunchAgent definition carries a PATH on which it does not resolve
> - **THEN** doctor fails a shared check naming the definition file and `agent-runner`, even though the interactive resolution succeeded
>
> ### Requirement: Diagnose readiness with doctor
>
> `agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval-sandbox, fix-sandbox, or fix-host and label each so the operator can see which kind a failure holds. Docker availability, memory allowance against one reservation, and sandbox launcher checks SHALL be reported under the kinds that use the sandbox; when the fix kind runs on the host they SHALL hold only evals. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, and the packaged fix workflow declares a compatible contract version. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt or attempt to repair credentials or configuration.
>
> Shared diagnostics SHALL remain distinct from checks supplied by each work kind and suite.
>
> #### Scenario: Diagnose an unavailable prerequisite
> - **WHEN** the operator runs doctor with Docker stopped, invalid required authentication, or an invalid Project mapping
> - **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an attempt
>
> #### Scenario: Diagnose suite readiness
> - **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
> - **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites
>
> #### Scenario: Diagnose fix readiness
> - **WHEN** the fix credential is missing, contains additional variables, or the packaged workflow lacks a compatible contract
> - **THEN** doctor reports the fix-specific problem and shows eval readiness independently
>
> #### Scenario: Diagnose Docker with fixes on the host
> - **WHEN** Docker is stopped and the fix kind is configured for host execution
> - **THEN** doctor reports Docker as an eval-sandbox problem and reports the fix kind ready when its host checks pass
>
> #### Scenario: Diagnose a missing host executable
> - **WHEN** the fix kind is configured for host execution and `jq` is not on the service PATH
> - **THEN** doctor reports the missing executable under the fix-host group with the action to take
>
> #### Scenario: Diagnose Runner settings
> - **WHEN** the operator's Runner user settings do not select the headless backend and yolo permission mode
> - **THEN** doctor reports the fix-host problem and names the required values without changing the settings
>
> #### Scenario: Report reclaimable Docker space
> - **WHEN** Docker is running and holds reclaimable images or build cache
> - **THEN** doctor prints the reclaimable amount and the trim command and does not run it
>
> #### Scenario: Pass a check
> - **WHEN** a check passes
> - **THEN** its line shows the result and no repair action

From the same file, requirement "Persist pause and enforce configured admission controls" (this task's portion): "The free-space minimum configured for the kind being admitted and, for sandbox attempts, the memory reservation SHALL be checked before admission; insufficient space or memory SHALL hold new affected work without consuming an execution retry."

> #### Scenario: Run below the free-space minimum
> - **WHEN** free disk space is below the minimum configured for the kind being admitted
> - **THEN** the factory starts no affected attempt, reports the storage problem, and rechecks readiness without consuming a recovery retry

> ### Requirement: Document installation and service operation
>
> The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, mirrors and working clones, Docker startup and memory allowance, the fix execution mode and what host mode keeps and gives up, model authentication, GitHub routing and board permissions, suite prerequisites, the packaged fix workflow and its contract version, explicit service paths including the LaunchAgent PATH, login behavior, preventing idle sleep, and the evidence retention period. Documentation SHALL state plainly that in host mode, on a machine where the operator's own GitHub login is available, the separate fix credential and the PR-only rulesets are conventions the launched process follows rather than boundaries an autonomous agent cannot cross, and that trusted-writer admission and human merge are the enforceable controls. Documentation SHALL explain doctor, status and `--all`, tick, pause, resume, service installation and restart, evidence locations including a host attempt's Runner session directory, the human-review handoff, the blocked-bug loop, and the merge sync. The GitHub setup documentation SHALL describe the harness setting as a branch resolved at admission, not a commit pin.
>
> Credentials for the suite's candidate branch, the fix PR credential, and board/routing credentials SHALL remain separately configured. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.
>
> #### Scenario: Set up the supported deployment
> - **WHEN** an operator follows the installation instructions with the required credentials, suite behavior, and a Runner branch that can run the packaged fix workflow
> - **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution of both kinds, inspect progress, pause and resume work, run a posted human-review command, and review a factory fix PR
>
> #### Scenario: Reuse the public example
> - **WHEN** another organization follows the public setup documentation
> - **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
> - **AND** the documentation clearly identifies any additional handler, suite, or workflow implementation needed for different work behavior
>
> #### Scenario: Set up host execution
> - **WHEN** an operator follows the instructions to run fixes on the host
> - **THEN** the documentation tells them the setting, the doctor checks to pass, the PATH the service needs, what the sandbox guarantees they lose, and that the credential and ruleset are not enforceable against the agent

The retention period and `status --all` parts of the documentation requirement are delivered with the retention and status work, not here.

## Test Plan

Obligations from `openspec/changes/dockerless-option/test-plan.md` assigned to this task. Read each entry there for the full setup and assertions.

- `INT-003`, all parts except the table-driven `fix-host` failure cases (`tests/integration/test_fix_readiness.py`, `tests/integration/test_admission_resources.py`, `tests/integration/test_mac_operations.py`): `operations.doctor`, `format_doctor`, fix readiness, and `runtime.cycle` over a real ClaimStore with stubbed PATH binaries (`agent-runner`, `gh`, `git`, `jq`, `python3`, `agent-validator`, optional `docker`) and stubbed Docker and memory probes, config variants for `fix.execution` and `fix.minimum_free_gib`, one eligible eval claim and one eligible fix claim. Passes when doctor output has `shared`, `eval-sandbox`, and `fix-sandbox` or `fix-host` headings and passing lines carry no `action:`; a stub Runner whose `run --help` omits `--session-dir` fails the host group with an action naming the flag; with Docker absent and host mode the eval claim is held with Docker named and the fix claim is admitted; with free space between the fix floor and the eval floor only the eval claim is held for disk; in host mode the memory probe is never invoked and the fix is admitted; a recorded Docker hold on the fix kind clears on the first successful poll after switching to host while the eval hold remains.
- `INT-007`, service-template portion (`tests/integration/test_mac_operations.py`): the plist rendered with a `__PATH__` value parses and its `EnvironmentVariables` contains `PATH` equal to the supplied value; doctor prints the PATH it resolved against; with a temporary HOME whose `Library/LaunchAgents/com.codagent.agent-factory.plist` carries a PATH lacking the stub Runner, doctor in host mode fails a `shared` check naming the plist file and `agent-runner` although interactive resolution passed; with a plist whose PATH resolves everything the check passes; with no plist the line is informational and does not fail. `fix.minimum_free_gib` is optional and falls back to the shared floor (`tests/integration/test_fix_config.py`).

Unit tests for group assignment, heading rendering, the reclaimable-space parser, the per-kind floor selection, and hold recomputation are implementation-time TDD decisions. `AT-002` (Docker outage holds evals only) and `AT-004` (service PATH) are operator acceptance steps run later, not by this task.

## Done When

- Every scenario copied above passes under automated tests, and the assigned `INT-003` (matrix and gating portions) and `INT-007` (service template and fix floor portions) obligations are implemented in the named files and pass.
- `agent-factory doctor` output on a machine with Docker stopped and `fix.execution = "host"` shows Docker failing under `eval-sandbox`, the `fix-host` group evaluated, no `action:` line on any `OK` line, a reclaimable-space line that never runs a prune, the resolved PATH, and the LaunchAgent PATH check.
- No diagnostic in the `shared` group depends on the evals checkout, the harness branch, the suite entry point, or the suite credential file; a test makes each of those unavailable in host mode and asserts the fix kind is still admitted while the eval kind is held.
- `agent-factory doctor --config <file>` with `[eval] execution = "host"` prints a failing `shared` diagnostic whose detail names eval host execution as unsupported and exits 1, while `agent-factory tick` with the same file still exits at startup with the configuration error; both covered by CLI-level tests in `tests/integration/test_cli_operations.py`.
- `runtime.cycle` no longer calls `doctor(local, include_fix=False)` as a global gate or probes Docker memory before considering kinds; `settings("runtime","readiness:<kind>")` is rewritten every poll from current configuration; `process_blocked_claim` no longer takes a global `memory_available`.
- `packaging/launchd/com.codagent.agent-factory.plist`, `_PLIST_TEMPLATE`, and `render_launch_agent` carry and substitute `__PATH__`; `docs/installation.md` tells the operator what to put there.
- `config/local.example.toml` documents `[fix] minimum_free_gib`.
- `docs/installation.md`, `docs/operations.md`, and `docs/github-setup.md` contain the sections described under "Documentation", and no document still describes the harness setting as a commit pin.
- Existing tests covering Docker-mode admission, memory holds, and doctor output are updated for the grouped output rather than deleted.
- `uv run ruff check`, `uv run pyright`, and `uv run pytest` (excluding `docker`-marked tests) pass.
