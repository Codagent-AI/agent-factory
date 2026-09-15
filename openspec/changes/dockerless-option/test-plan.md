## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration, end-to-end, agent-acceptance, and exceptional human-only obligations.

Repository conventions: integration tests live in `tests/integration/`, end-to-end tests in
`tests/e2e/`, and run with `pytest` (`--strict-markers`). Tests needing macOS process-session
semantics carry `@pytest.mark.darwin`; tests needing Docker carry `@pytest.mark.docker` and skip with
an explicit "not passing evidence" message when `AGENT_FACTORY_DOCKER_SOURCES` is unset. The host
launch end-to-end test follows the same skip pattern keyed on the installed Runner.

The companion Runner change (`agent-runner run --session-dir`) is tested in the agent-runner
repository; here it is exercised only through E2E-001 and AT-001.

## Integration Tests

### INT-001: Host plan build writes paths, never the token
- Covers: Run a fix attempt on the host; Keep the fix credential out of persisted state; Record host provenance
- Boundary: `launch.build_host_plan` against a real storage root, target clone, validated credential copy, and a stub `agent-runner` executable on PATH that answers `-version` and `run --help`
- Setup: `tmp_path` storage with `private/`, a git-initialised clone, `fix.env` containing `GH_TOKEN=dummy-fix-token`, PATH pointing at a directory holding the stub
- Action: build the host plan for one run id, then serialise it the way the supervisor persists plans
- Assertions: `host-run.sh`, `gitconfig`, and `askpass.sh` exist under `private/<run_id>/` with owner-only modes; `gitconfig` sets the factory identity, an empty `credential.helper`, and the askpass path; the workflow file and both scripts exist only under `<clone>/.agent-runner/workflows/` and that path is listed in `.git/info/exclude`; nothing was written under the test HOME's `.agent-runner`; the persisted plan document, argv, and wrapper text contain the credential file path and the string `dummy-fix-token` appears in none of them; argv is `["/bin/bash", <wrapper>]` and the wrapper execs the stub's absolute path with `--session-dir <evidence>/agent-runner-session` and `--param artifact_dir=<evidence>`; hints carry `sandbox: "host"`, `runner_executable`, `runner_version`, `session_dir`, `branch_name`, and no `image_tag`; `allowed_environment` is empty
- Execution: `tests/integration/test_host_launch.py`

### INT-002: Packaged workflow declares the artifact directory
- Covers: Invoke the versioned fix workflow (artifact directory passed in both modes)
- Boundary: `check_runner_contract` and the real packaged workflow file
- Setup: the shipped `factory-fix-v1.0.yaml`; a copied variant with the `artifact_dir` parameter removed; a variant with a literal `/artifacts` path in a step
- Action: run the contract check on each
- Assertions: the shipped workflow passes and declares `artifact_dir` with default `/artifacts`; the shipped workflow contains no literal `/artifacts` outside that default; the variant without the parameter and the variant with a literal path each fail with a diagnostic naming the problem
- Execution: `tests/integration/test_fix_workflow.py`

### INT-003: Readiness matrix gates each kind under its mode
- Covers: Diagnose readiness; Classify admission holds by scope; Check memory headroom before admission; Recheck unavailable prerequisites
- Boundary: `operations.doctor`, `format_doctor`, fix `readiness`, and `runtime.cycle` over a real ClaimStore with a stubbed PATH (stub `agent-runner`, `gh`, `git`, `jq`, `python3`, `agent-validator`, optional `docker`) and stubbed Docker and memory probes
- Setup: local config variants (`fix.execution` docker and host, optional `fix.minimum_free_gib`), two eligible claims (one eval, one fix), stub binaries whose presence and `run --help` output are controlled per case
- Action: run doctor and one runtime cycle per case
- Assertions: doctor output has `shared`, `eval-sandbox`, `fix-sandbox` or `fix-host` headings and passing lines carry no `action:`; a stub Runner whose `run --help` omits `--session-dir` fails the host group with an action naming the flag; with Docker absent and host mode, the eval claim is held with Docker named and the fix claim is admitted; with free space between the fix floor and the eval floor, only the eval claim is held for disk; with host mode the memory probe is never invoked and the fix is admitted; a recorded Docker hold on the fix kind clears on the first successful poll after switching to host while the eval hold remains; and, table-driven over the host group, each of these produces a `fix-host` failure with a named action, a fix hold, and no launch of any kind: a role CLI missing from PATH, a role CLI whose auth probe fails, a role CLI without the codagent plugin, Runner user settings that are not headless or not yolo, a stub Runner whose `-validate` rejects the packaged workflow, a fix token whose `gh auth status` fails, and a fix token equal to the installation token
- Execution: `tests/integration/test_fix_readiness.py`, `tests/integration/test_admission_resources.py`, `tests/integration/test_mac_operations.py`

### INT-004: Host attempts are supervised by process only
- Covers: Supervise a host attempt by process; Verify execution ownership before termination; Enforce separate progress and runtime limits
- Boundary: `launch_supervisor` with a plan hinting `sandbox: "host"` whose argv is a bash script that spawns a sleeping child and periodically touches a file under `<evidence>/agent-runner-session/`
- Setup: PATH without any `docker` executable; short `SupervisionLimits`
- Action: launch, observe progress, then terminate; separately launch a variant whose subprocess output is silent while the session directory keeps changing
- Assertions: no container discovery is attempted (no `docker` lookup failure recorded, supervisor state shows no container id); termination leaves neither the script nor its child running; the silent variant is not stopped for inactivity while session-directory writes continue and is stopped once they cease; after termination the run's ownership check reports the process as gone without consulting Docker
- Execution: `tests/integration/test_supervision_hardening.py`, marked `darwin`

### INT-005: Retention prunes only settled evidence
- Covers: Retain evidence for a bounded period; Preserve fix evidence; Prune a settled eval's evidence
- Boundary: `retention.observe` and `retention.sweep` over a real ClaimStore and a real evidence tree
- Setup: realistic evidence trees copied from the layouts the launchers and suite write today: a fix claim with a Docker attempt (`input/issue.json`, `logs/`, `factory-suite.log`, `agent-runner/projects/...`, `fix-outcome.json`) and a host attempt (`agent-runner-session/`, `host-provenance.json`, `.runtime/agent-session-state/...`), plus an eval claim with a repetition holding `result.json`, `run-state.json`, `phases/*.json`, `evidence/candidate/manifest.json`, `neutral/provenance/manifest.json`, `report.html`, `logs/`, `.runtime/agent-runner-projects/`, `.runtime/agent-session-state/`, `.runtime/judge-workspace/`, and a `.runtime/candidate-worktree/` marker; each tree also contains an unknown file `future.json`; retention set to 14 days; a controllable clock; a superseded claim with its own attempt tree
- Action: reconcile with board status per case, before and after the period
- Assertions: the first Done observation writes `done_observed_at` and removes nothing; at 13 days nothing is removed; at 14 days with status Done the enumerated paths are gone and `input/issue.json`, `fix-outcome.json`, `host-provenance.json`, `result.json`, `run-state.json`, `phases/`, `evidence/`, `neutral/`, `report.html`, `.runtime/candidate-worktree/`, and every `future.json` remain; observing the card in Review after Done clears `done_observed_at` and a later Done restarts the clock; a claim not visited (card absent from the board) is never pruned; each of these leaves evidence untouched: a non-terminal run, an unverified run, pending reporting events, sync not completed, `cleanup.complete` false on a settled claim; the superseded claim is pruned without `cleanup.complete`; a directory made unremovable records an error under `retention.errors`, leaves `pruned_at` unset, and the next reconcile succeeds once it is removable; a second reconcile after success removes nothing and records nothing new
- Execution: `tests/integration/test_retention.py`

### INT-006: Status lists live work only
- Covers: Report status
- Boundary: `operations.status` and the CLI `status` command over a real ClaimStore
- Setup: claims covering: running; blocked; Review with cleanup incomplete; Done with pending reporting event; Done with pending sync; fully settled Done; superseded
- Action: run `status` and `status --all`
- Assertions: default output includes the first five and omits the settled and superseded claims, with a header count of hidden claims; `--all` lists all seven
- Execution: `tests/integration/test_cli_operations.py`, `tests/integration/test_operations_status_sync.py`

### INT-007: Configuration and service template
- Covers: Configure deployment; Install as a Mac service
- Boundary: `LocalConfig.from_toml` and the plist renderer
- Setup: TOML variants; the plist template rendered with a `__PATH__` value; a temporary HOME holding `Library/LaunchAgents/com.codagent.agent-factory.plist` variants whose PATH does or does not resolve a stub `agent-runner`
- Action: load each variant; render the plist; run doctor in host mode against each installed-plist variant and with no plist
- Assertions: `fix.execution` defaults to `docker` and accepts `host`; `fix.minimum_free_gib` is optional and falls back to the shared floor; `limits.evidence_retention_days` defaults to 14; `[eval] execution = "host"` is rejected with a message naming eval host execution as unsupported; the rendered plist parses and its `EnvironmentVariables` contains `PATH` equal to the supplied value; doctor prints the PATH it resolved against; with a plist whose PATH lacks the Runner, doctor fails a `shared` check naming the plist file and `agent-runner` although the interactive resolution passed; with a plist whose PATH resolves everything the check passes; with no plist the line is informational and does not fail
- Execution: `tests/integration/test_fix_config.py`, `tests/integration/test_mac_operations.py`

### INT-008: Outcome comments carry the host note
- Covers: Comment on fix activity
- Boundary: fix reporting over a real ClaimStore with a recording GitHub client double
- Setup: one claim whose run record has mode `host`, one whose run has mode `docker`, each producing `pull-request`, `needs-input`, and `failed`
- Action: deliver reporting events
- Assertions: every host-run outcome comment contains the host sentence; no docker-run comment contains it; the admission comment text is identical across modes
- Execution: `tests/integration/test_fix_sync_reporting.py`

## End-to-End Tests

### E2E-001: A host attempt runs through the installed Runner without touching HOME
- Covers: Run a fix attempt on the host; Supply the workflow through the clone; Preserve fix evidence; Record host provenance
- Surface: `launch.build_host_plan` plus `launch_supervisor`, executing the real installed `agent-runner`
- Setup: skip with "Required separate host check: install agent-runner with --session-dir; not passing evidence" unless `agent-runner` is on PATH and its `run --help` lists `--session-dir`. Temporary HOME containing a planted `.gitconfig` (a credential helper and an `insteadOf` rewrite), a planted `~/.agent-runner/workflows/factory-fix-v1.0.yaml` that would write a marker file if selected, and Runner user settings for headless execution. A model-free stand-in `factory-fix` workflow (like the Docker e2e test's `TEST_WORKFLOW`, using `{{artifact_dir}}`) staged by the real host plan into a fresh target clone. Its steps record `env` names, `git config --get user.name`, `git config --get credential.helper`, `git config --get-regexp url`, and write `fix-outcome.json`
- Journey: build the host plan, launch under the supervisor, wait for exit, read the outcome
- Assertions: `fix-outcome.json` and `agent-runner-session/` exist under the attempt's evidence directory; the planted user-level workflow's marker was not written; the recorded identity is the factory identity, the credential helper is empty, and no URL rewrite is reported; `GH_TOKEN` is among the environment names; the planted HOME files are byte-identical before and after and no new entries appeared under the temporary `~/.agent-runner/projects`; the run record carries mode `host`, the Runner path and version, and the session directory. A second case launches a stand-in workflow whose step records its PID, spawns a grandchild that records its own PID, and blocks; the supervisor terminates the attempt; assert that the Runner process, the step process, and the grandchild are all gone within the termination grace period and that no `docker` executable was consulted
- Execution: `tests/e2e/test_host_fix_launch.py`, marked `darwin`

### E2E-002: Fix journey in host mode from admission to pruning
- Covers: Invoke the versioned fix workflow (host); Isolate concurrent sandbox builds (host cleanup); Comment on fix activity; Retain evidence for a bounded period; Report status
- Surface: the runtime cycle and CLI as exercised by the existing fix-cycle end-to-end harness
- Setup: the fix-cycle harness with `fix.execution = "host"`, PATH without `docker`, and a stub `agent-runner` that honours `--session-dir` and `--param artifact_dir` and writes a `pull-request` outcome; a controllable clock
- Journey: cycle admits the bug, launches, reads the outcome, reports, observes Review then Done, cleans up, then cycles again after the retention period
- Assertions: the admitted run has no image tag and mode `host`; the PR comment carries the host note; cleanup removes the clone and the credential copy without any Docker invocation; `status` omits the claim once settled and `status --all` shows it; after the retention period the attempt's logs and session directory are gone and `fix-outcome.json` remains. Two further journeys cover mixed-mode recovery in each direction: attempt 1 launches in one mode and fails technically, the operator switches `fix.execution`, and the recovery retry launches in the other mode (Docker stubbed through the existing harness). Assert that each run record keeps the mode it launched with, that only the Docker-mode run has an image tag, that the outcome comment of each run carries the host note only when that run was host, that Done cleanup removes the Docker run's image and attempts no image removal for the host run, and that both attempts' credential copies are deleted
- Execution: `tests/e2e/test_fix_cycle.py`

## Agent Acceptance Tests

### AT-001: Real host fix on the operator's Mac
- Classification: Required
- Covers: Run a fix attempt on the host; Record host provenance; Comment on fix activity; Report status
- Actor and surface: operator using the `agent-factory` CLI (`doctor`, `run-once` or one supervised cycle, `status`)
- Setup: Runner rebuilt with `--session-dir`; local config `fix.execution = "host"`; a throwaway issue labelled for the factory in one of the configured Codagent-AI target repositories describing a trivial, verifiable fix; the real fix token; hashes of `~/.gitconfig` and a listing of `~/.agent-runner/workflows` and `~/.agent-runner/projects` captured beforehand
- Steps: run `doctor`; run one factory cycle; wait for the attempt; run `status`; move the card to Done after review; run `status` and `status --all`
- Expected: `doctor` shows the `fix-host` group passing with no action lines; the attempt opens a PR authored by the fix credential's identity; the outcome comment contains the host sentence; the run record shows mode `host`, the Runner path and version, and a session directory under the attempt's evidence; `~/.gitconfig` hash and both Runner listings are unchanged; after Done, `status` hides the claim and `--all` shows it
- Evidence: `doctor` and `status` output, the PR and comment links, the run record query, before-and-after hashes and listings
- Effects and cleanup: one model-billed agent run; a PR and an issue in a Codagent-AI repository, both closed afterwards with the branch deleted; the claim left to normal cleanup
- Permitted substitutes: None

### AT-002: Docker outage holds evals only
- Classification: Required
- Covers: Classify admission holds by scope; Diagnose readiness
- Actor and surface: operator using `doctor` and `status`
- Setup: `fix.execution = "host"`, Docker Desktop stopped, an eligible eval card and an eligible fix card present
- Steps: run `doctor`; run one cycle; run `status`
- Expected: `doctor` marks Docker unavailable under `eval-sandbox` only; `status` shows an eval hold naming Docker and no fix hold; the fix is admitted
- Evidence: `doctor` and `status` output
- Effects and cleanup: the admitted fix may be the AT-001 attempt; restart Docker afterwards
- Permitted substitutes: None

### AT-003: Docker mode is unchanged
- Classification: Required
- Covers: Invoke the versioned fix workflow (docker); Isolate concurrent sandbox builds
- Actor and surface: operator running the Docker-marked end-to-end test
- Setup: Docker running, `AGENT_FACTORY_DOCKER_SOURCES` set to the checkouts of agent-runner and agent-skills, at least the configured free disk
- Steps: `pytest -m docker tests/e2e/test_docker_fix_launch.py`
- Expected: the test passes, showing the workflow received `artifact_dir=/artifacts` and wrote its outcome to the mounted evidence directory
- Evidence: pytest output
- Effects and cleanup: test-owned images removed by the test
- Permitted substitutes: None

### AT-004: The service resolves the Runner through the plist PATH
- Classification: Conditional: the factory LaunchAgent is installed on the operator's Mac
- Covers: Install as a Mac service
- Actor and surface: operator following `docs/installation.md` and using `launchctl` and `doctor`
- Setup: current shell PATH containing `agent-runner`; the existing installed plist backed up
- Steps: render the template per the docs with `__PATH__` filled in; run `doctor` and note the service-PATH line; temporarily render a variant whose PATH omits the Runner's directory, run `doctor` again; restore the correct plist, reload the service with `launchctl`, and confirm the service's own log shows host readiness passing
- Expected: `doctor` passes the service-PATH check with the correct plist and fails it, naming the plist file and `agent-runner`, with the broken variant; the reloaded service resolves the Runner and CLIs under `fix-host`
- Evidence: plist excerpt and service log excerpt
- Effects and cleanup: the service is left installed as before
- Permitted substitutes: None

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | AT | HT |
| --- | --- | --- | --- | --- |
| Run a fix attempt on the host | INT-001 | E2E-001 | AT-001 | — |
| Keep the fix credential out of persisted state | INT-001 | E2E-001 | — | — |
| Record host provenance | INT-001 | E2E-001 | AT-001 | — |
| Invoke the versioned fix workflow | INT-002 | E2E-002 | AT-003 | — |
| Isolate concurrent sandbox builds | — | E2E-002 | AT-003 | — |
| Preserve fix evidence | INT-005 | E2E-001 | — | — |
| Supervise a host attempt by process | INT-004 | E2E-001 | — | — |
| Verify execution ownership before termination | INT-004 | — | — | — |
| Enforce separate progress and runtime limits | INT-004 | — | — | — |
| Classify admission holds by scope | INT-003 | — | AT-002 | — |
| Fail host readiness (fail-closed, no Docker fallback) | INT-003 | — | — | — |
| Check memory headroom before admission | INT-003 | — | — | — |
| Recheck unavailable prerequisites | INT-003 | — | — | — |
| Diagnose readiness | INT-003 | — | AT-001, AT-002 | — |
| Configure deployment | INT-007 | — | — | — |
| Install as a Mac service | INT-007 | — | AT-004 | — |
| Change the mode while an attempt runs (recovery in the configured mode) | — | E2E-002 | — | — |
| Retain evidence for a bounded period | INT-005 | E2E-002 | — | — |
| Prune a settled eval's evidence | INT-005 | — | — | — |
| Report status | INT-006 | E2E-002 | AT-001 | — |
| Comment on fix activity | INT-008 | E2E-002 | AT-001 | — |
