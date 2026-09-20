# Task: Fly execution mode — configuration, Machines API client, intake, readiness, doctor, and status

## Goal

Make `fly` a supported value of `eval.execution` everywhere the factory *decides* and *reports*,
without yet launching anything on Fly: configuration and defaults, the Fly Machines REST client,
Cursor refusal at eval intake, mode-conditional readiness holds (no Docker or memory holds under
`fly`, a new `eval-fly` class), `doctor`'s `eval` and `eval-fly` groups with no Docker output when no
kind uses Docker, and `status` lines for Machines. After this task an operator can set
`eval.execution = "fly"`, run `doctor` against Fly, and see evals held or admitted for the right
reasons.

## Background

Evals are down because `eval.execution` only accepts `"docker"` and Docker is disabled on the
factory Mac. The change `openspec/changes/fly/` adds Fly Machines as an eval execution mode. Read
`openspec/changes/fly/proposal.md` and `openspec/changes/fly/design.md` (sections "Execution backend
concept", "Configuration", "Doctor and status", "Reconciliation" for the setting keys) before
starting. This task delivers the decision/reporting half; nothing in it creates a Machine.

The package has **no third-party runtime dependencies** (`pyproject.toml` `dependencies = []`); the
Fly API is called with `urllib`. Keep it that way. Automated tests never contact Fly.

### Module layout (from the design; create what this task needs)

```
src/agent_factory/
  backends/__init__.py   ExecutionBackend protocol, ExecutionIdentity, Probe, Disposal
  fly/__init__.py
  fly/api.py             Machines REST client (urllib), registry manifest lookup
  fly/backend.py         FlyMachineBackend — this task implements readiness() only
```

The design places the Fly modules at `src/agent_factory/fly/`. `test-plan.md` names the boundary
`backends/fly/api.py` in one place; use the design's `src/agent_factory/fly/api.py`.

Define the full `ExecutionBackend` protocol now, exactly as the design gives it, so later work fills
in methods without reshaping it:

```python
class ExecutionBackend(Protocol):
    name: str                                   # "fly-machine"
    def readiness(self, local, shared) -> list[Diagnostic]: ...   # group "eval-fly"
    def identity_from_plan(self, plan, run) -> Mapping | None: ...  # reads .factory/machine.json
    def probe(self, identity) -> Probe: ...     # alive | stopped | gone | mismatch | unknown
    def terminate(self, identity) -> bool: ...  # stop the owned job, keep the Machine
    def dispose(self, identity, decision) -> None: ...   # "destroy" | "stop" | "keep"
    def attach_argv(self, plan, run) -> tuple[str, ...]: ...  # re-spawn transport after restart
    def reconcile(self, store) -> list[str]: ...  # stale-Machine pass, returns report lines
    def provenance(self, identity) -> Mapping: ...  # image digest, size, region
```

In `FlyMachineBackend`, implement `readiness()` in this task; the other methods may raise
`NotImplementedError`. Docker and host execution are **not** migrated behind this interface.

### Configuration (`src/agent_factory/config.py`, `config/codagent.toml`)

- `LocalConfig` currently rejects any `eval.execution` other than `"docker"` (around line 237).
  Accept `"docker"` (default) or `"fly"`; keep rejecting `"host"` with the existing
  unsupported-setting error reported at startup and in doctor.
- Add a local `[fly]` table, required only when `eval.execution = "fly"`; startup reports each
  missing required setting by name. Follow the existing frozen-dataclass and `_positive_int`-style
  validation idiom in `config.py`.

  ```toml
  [eval]
  execution = "fly"

  [fly]
  app = "agent-factory-sandbox"            # required
  region = "ewr"                           # default
  cpu_kind = "shared"                      # default
  cpus = 4                                 # default
  memory_mb = 8192                         # default
  image = "registry.fly.io/agent-factory-sandbox:base"   # required
  token_file = "/Users/paul/.agent-factory/credentials/fly-deploy-token"  # required
  collection_grace_seconds = 900           # default
  heartbeat_seconds = 20                   # default
  ```
- In `config/codagent.toml` the eval role defaults (around line 58) become
  `lead = "claude:opus:medium"`, `implementor = "codex:gpt-5.6-luna:medium"`,
  `tester = "codex:gpt-5.6-luna:medium"`. Only the implementor changes (from
  `claude:sonnet:medium`). Do not touch the fix role defaults further down the file. Public example
  configuration must contain no personal paths or credentials.

### Machines API client (`src/agent_factory/fly/api.py`)

A small typed client over `urllib` against `https://api.machines.dev` (base URL injectable for
tests), authenticated with the bearer token read from `fly.token_file`. Operations: create Machine,
get Machine, list Machines filtered by `metadata.<key>=<value>`, set one metadata key
(`POST /v1/apps/{app}/machines/{id}/metadata/{key}`), update the config of a stopped Machine (used to
change `env.FACTORY_DEADLINE_EPOCH`), stop, start, destroy, `GET /v1/apps/{app}`, and registry
manifest resolution (`HEAD https://registry.fly.io/v2/<repo>/manifests/<tag>` with the token,
returning the digest).

The create helper builds the body the design specifies — callers pass values, the client owns the
shape:

```
{image, guest{cpu_kind, cpus, memory_mb, persist_rootfs: "always"}, auto_destroy: true,
 restart: {policy: "no"}, region,
 metadata{factory-owner, run_id, claim_id, nonce, deadline_epoch, unit_key},
 env{FACTORY_DEADLINE_EPOCH, FACTORY_RUN_ID, FACTORY_NONCE},
 init{exec: ["bash", "-c", <guest init script>]}}
```

Rules: a 404 on destroy is success (idempotent); 5xx and other failures raise a typed error carrying
the request path; the token never appears in logs, exception text, or `repr`. Fly semantics relied
on: `auto_destroy: true` destroys only when the main process exits (a manual stop leaves the Machine
stopped); metadata is set per key without a restart; a config update is allowed while stopped.

### Intake (`src/agent_factory/work_kinds/eval/__init__.py`, `work_kinds/eval/handler.py`)

`parse_request(body, defaults)` must learn the eval execution mode (extend `EvalDefaults` or add a
parameter; `EvalHandler.from_config` has `local`). Under `fly`, a `cursor` CLI in any *effective*
role — supplied or inherited from defaults — is an invalid request that follows the existing
needs-input correction path, with a comment naming the role and saying Cursor is unavailable on
Fly. Under `docker` Cursor stays valid. For a claim already frozen with a Cursor role,
`EvalHandler.prepare` raises `ReadinessError` (from `suites/and_scene/__init__.py`) naming the role
when the mode is `fly`: the claim is held, frozen inputs are never mutated, and other eligible eval
claims may proceed.

### Readiness classes (`src/agent_factory/operations.py`, `src/agent_factory/runtime.py`)

- `DiagnosticGroup` and `_GROUP_ORDER` in `operations.py` gain `"eval"` and `"eval-fly"`.
- Checks now labelled `eval-sandbox` that are mode-neutral — harness branch resolution, host
  Codex/Claude login (`model_authentication`), the suite environment file, and free disk space
  against the eval floor — move to the new `eval` group, which holds the eval kind under every mode.
  `eval-sandbox` keeps only the Docker launcher, Docker availability, and memory checks.
- `eval-fly` (only when `eval.execution == "fly"`) is `FlyMachineBackend.readiness()`: token file
  owner-readable and containing only a single-line token; `GET /v1/apps/{app}` succeeds with the
  token; the configured image resolves to a digest; `flyctl` is executable on the service PATH;
  `flyctl ssh` can reach the app (an `ssh issue`-style check that needs no Machine). Every failing
  diagnostic carries a specific remedy. Readiness and doctor never issue a create call.
- `runtime._kind_failures` for eval filters on `{"shared", "eval", <mode group>}`; under `fly` it
  appends `backend.readiness()` failures and **skips the `sandbox_memory()` probe**. A recorded hold
  whose check no longer applies after a configuration change clears on the next successful poll
  (existing behavior — keep it working for the new groups).
- `doctor()` receives the kinds' modes; `_command_check("Docker")`, the memory check, launcher
  checks, and the reclaimable-space line are constructed only when some kind is configured for
  Docker. With evals on `fly` and fixes on `host`, doctor output has no line mentioning Docker.
- A host Codex or Claude login found invalid holds eval admission under `fly` with the provider
  named (this is the existing prerequisite hold, now in the `eval` group).

### Status (`src/agent_factory/operations.py`)

Status renders saved state; other parts of the change write it. Render these shapes (they are the
contract — seed them directly in tests):

- `run.progress["machine"]` = `{app, id, nonce, deadline_epoch, state, image_ref, guest, region}` →
  `_progress_lines` prints Machine id, state, and deadline for an active run.
- setting `("runtime", "fly:machine:<claim_id>")` = `{machine_id, decision, deadline_epoch, state}` →
  `_hold_lines` prints `stopped (quota hold), deadline …` for a quota-held claim.
- settings `("runtime", "fly:unknown")`, `("runtime", "fly:mismatch")`,
  `("runtime", "fly:cleanup-failed")` → printed under blocking conditions with the Machine id and
  reason/remedy until cleared. `ClaimStore.set_setting` values are mappings; use
  `{"machines": [{machine_id, deadline_epoch, reason, ...}]}` for the list-valued ones and
  `{machine_id, run_id, expected, observed, remedy}` for `fly:mismatch`.
- `status` may issue one read-only `GET` per recorded Machine to show live state and tolerates API
  failure by printing the recorded state with a note. It never starts work or changes anything.

### Test support

Create reusable fakes under `tests/fixtures/fly/` (there is no `conftest.py` today; existing tests
build fixtures inline — follow the style of `tests/integration/test_cli_operations.py` and
`tests/integration/test_mac_operations.py`):

- a local `http.server`-based fake of the Machines REST API and registry manifest endpoint with an
  in-memory Machine table that records every request;
- a minimal fake `flyctl` executable placed on `PATH` sufficient for the doctor transport check.

Keep both importable and general; more Fly tests will build on them.

## Spec

_From `specs/factory-operations/spec.md`:_

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. The eval kind SHALL accept `docker` (the default) or `fly` execution and SHALL NOT accept host execution. Fly configuration SHALL be local and SHALL include the Fly app, region (default `ewr`), Machine CPU kind, CPU count, and memory (default shared, 4 CPUs, 8 GiB), the sandbox image reference, the deploy-token file location alongside the other controller credentials, and the collection grace period. The execution mode, fix disk floor, Fly settings, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration whose eval role defaults are `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`, and `tester = codex:gpt-5.6-luna:medium`. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific and workflow-specific repository and executable locations SHALL be supplied to the relevant handler rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the Eval and Bug behavior and the field names, options, and defaults described by the active specifications

#### Scenario: Configure fix targets

- **WHEN** an operator configures five target repositories with mirror and working-clone paths and leaves branches unset
- **THEN** fixes resolve `main` for each repository and the merge sync targets each configured working clone

#### Scenario: Leave the execution mode unset

- **WHEN** the local configuration names no fix execution mode
- **THEN** fixes run in the sandbox exactly as before this change

#### Scenario: Leave the eval execution mode unset

- **WHEN** the local configuration names no eval execution mode
- **THEN** evals run under Docker exactly as before this change and no Fly setting is required

#### Scenario: Configure host execution for evals

- **WHEN** the local configuration requests host execution for the eval kind
- **THEN** the factory reports the unsupported setting at startup and in doctor and admits no eval

#### Scenario: Configure Fly execution for evals

- **WHEN** the local configuration requests `fly` execution for the eval kind with an app, image, and deploy-token location and leaves the region, size, and grace unset
- **THEN** evals run in Fly Machines in `ewr` at shared 4 CPUs and 8 GiB with the default collection grace, and startup reports any missing required Fly setting

_From `specs/factory-operations/spec.md`:_

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval, eval-sandbox, eval-fly, fix-sandbox, or fix-host and label each so the operator can see which kind a failure holds; the eval group holds the mode-neutral eval checks that apply under every eval execution mode. It SHALL run only the groups that apply to a kind under its configured execution mode. Docker availability, memory allowance against one reservation, sandbox launcher checks, and reclaimable Docker space SHALL be checked and reported only under kinds configured for Docker execution; when no kind is configured for Docker, doctor SHALL neither probe Docker nor print any Docker line. The eval-fly group SHALL verify that the Fly API is reachable with the configured deploy token, the configured app exists, the configured image resolves to an immutable digest, the deploy-token file is owner-readable and contains only that token, and `flyctl` is executable on the service PATH for transport. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, and the packaged fix and review workflows each declare a compatible contract version. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When a kind is configured for Docker and Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt, create a Machine, or attempt to repair credentials or configuration.

Shared diagnostics SHALL remain distinct from checks supplied by each work kind and suite.

#### Scenario: Diagnose an unavailable prerequisite

- **WHEN** the operator runs doctor with Docker stopped under a Docker-configured kind, invalid required authentication, or an invalid Project mapping
- **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an attempt

#### Scenario: Diagnose suite readiness

- **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
- **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites

#### Scenario: Diagnose fix readiness

- **WHEN** the fix credential is missing, contains additional variables, or the packaged fix or review workflow lacks a compatible contract
- **THEN** doctor reports the fix-specific problem and shows eval readiness independently

#### Scenario: Diagnose Docker with fixes on the host

- **WHEN** Docker is stopped, the eval kind is configured for Docker execution, and the fix kind is configured for host execution
- **THEN** doctor reports Docker as an eval-sandbox problem and reports the fix kind ready when its host checks pass

#### Scenario: Run doctor with no kind on Docker

- **WHEN** the eval kind is configured for Fly execution and the fix kind for host execution
- **THEN** doctor probes nothing about Docker and prints no Docker line, and reports the shared, eval, eval-fly, and fix-host groups

#### Scenario: Diagnose an unresolvable Fly image

- **WHEN** the eval kind is configured for Fly execution and the configured image cannot be resolved to a digest
- **THEN** doctor fails the eval-fly group naming the image and the action to take, and shows fix readiness independently

#### Scenario: Diagnose a missing host executable

- **WHEN** the fix kind is configured for host execution and `jq` is not on the service PATH
- **THEN** doctor reports the missing executable under the fix-host group with the action to take

#### Scenario: Diagnose Runner settings

- **WHEN** the operator's Runner user settings do not select the headless backend and yolo permission mode
- **THEN** doctor reports the fix-host problem and names the required values without changing the settings

#### Scenario: Report reclaimable Docker space

- **WHEN** a kind is configured for Docker execution, Docker is running, and it holds reclaimable images or build cache
- **THEN** doctor prints the reclaimable amount and the trim command and does not run it

#### Scenario: Pass a check

- **WHEN** a check passes
- **THEN** its line shows the result and no repair action

_From `specs/factory-operations/spec.md`:_

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix claims, settled fix claims with eligible review comments waiting for the slot, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's state including whether it is stopped for a quota hold, and the attempt's recorded deadline; it SHALL also list Machines that reconciliation reported as unknown to the store or as failed cleanup. It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a merge sync, or holding a recorded cleanup failure; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a waiting review round, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but the fix slot is busy
- **THEN** status names the claim, the PR, and that it waits for the slot

#### Scenario: Inspect an installation with history

- **WHEN** the database holds many Done and superseded claims and one running claim
- **THEN** status lists the running claim and none of the settled ones
- **AND** `status --all` lists every saved claim

#### Scenario: Inspect a Fly attempt

- **WHEN** the operator requests status while an eval repetition runs in, or is stopped in, a Fly Machine
- **THEN** status shows the Machine identity, whether it is running or stopped for a quota hold, and the attempt's deadline

#### Scenario: Inspect a reconciliation finding

- **WHEN** reconciliation has reported a Machine unknown to the store or a cleanup that could not be verified
- **THEN** status lists that Machine and the reported reason until it is resolved

This task's portion of the status requirement: render Machine identity/state/deadline and the
reconciliation findings from the saved shapes above.

_From `specs/factory-eval-intake/spec.md`:_

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `lead`, `implementor`, `tester`, `skip_validator`, and `repetitions`. Other keys, including the legacy `lead_profile`, `implementor_profile`, `reviewer`, `reviewer_profile`, and `tester_profile` aliases, SHALL be rejected. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean. Omitted settings SHALL use configured defaults. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

When the eval kind is configured for Fly execution, a request whose effective lead, implementor, or tester profile uses the `cursor` CLI, whether supplied in the block or inherited from the configured defaults, SHALL be an invalid request and SHALL follow the correction behavior below with a comment naming the affected role and stating that Cursor is unavailable on Fly. Under Docker execution Cursor profiles SHALL remain valid.

Request-level revision selection SHALL apply to Agent Runner and Agent Skills. The `agent-evals` harness SHALL follow the configured harness branch (default `main`); request-level selection of harness revisions remains unsupported. Recording the resolved harness commit SHALL identify the test environment used for the result.

Repetitions SHALL be a positive integer. An optional configured maximum SHALL reject excessive requests rather than reduce them silently. A repetition ceiling is not required.

#### Scenario: Override selected defaults

- **WHEN** a valid block supplies a Runner ref and a complete lead profile while omitting other settings
- **THEN** those supplied settings replace their respective defaults
- **AND** all omitted settings retain their configured defaults

#### Scenario: Supply invalid execution settings

- **WHEN** a block is malformed, a supplied role profile is incomplete, an unsupported key is supplied, or repetitions is not a positive integer
- **THEN** the request is invalid and no evaluation starts

#### Scenario: Exceed an optional repetition limit

- **WHEN** a repetition maximum is configured and a request exceeds it
- **THEN** the factory rejects the request settings and explains the limit
- **AND** it does not silently run fewer repetitions

#### Scenario: Select Cursor under Fly execution

- **WHEN** the eval kind runs under Fly execution and a request's effective role profiles include a `cursor` CLI
- **THEN** the request is invalid, stays in Ready with `needs-input`, and the comment names the role and that Cursor is unavailable on Fly
- **AND** no claim is created

#### Scenario: Replace Cursor with a supported CLI

- **WHEN** the user changes the flagged role to a complete Codex or Claude profile
- **THEN** the factory removes `needs-input` and the request can become eligible for admission

_From `specs/factory-eval-intake/spec.md`:_

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve the requested Runner and Skills refs and the configured `agent-evals` harness branch to immutable commits from the remote at admission. `agent-evals` is the evaluation harness and may contain multiple suites; `and-scene` is the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs. Configuration SHALL name the harness branch, not a commit.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches, the harness branch, or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

#### Scenario: Admit two evals on different days

- **WHEN** the harness branch advances between two admissions
- **THEN** each claim records the harness commit it resolved at its own admission and the difference is visible in its Refs and results

#### Scenario: Switch to Fly with a frozen Cursor claim

- **WHEN** a claim admitted with a Cursor role profile has unfinished repetitions and the eval execution mode changes to Fly
- **THEN** its frozen inputs are not mutated and it is held under the eval readiness behavior in `factory-claim-lifecycle` with an explanation naming the Cursor role

_From `specs/factory-claim-lifecycle/spec.md`:_

### Requirement: Recheck unavailable prerequisites without retrying execution

Before accepting work for execution, the factory SHALL check the readiness that applies to that kind and, before launch, the selected suite's or mode's readiness. Readiness checks SHALL be classified as shared, eval, eval-sandbox, eval-fly, fix-sandbox, or fix-host, and only the classes that apply to a kind under its configured execution mode SHALL hold that kind. The eval class SHALL cover the mode-neutral eval prerequisites, namely harness branch resolution, the factory host's Codex and Claude logins, the suite environment file, and free disk space, and SHALL hold the eval kind under every execution mode. The eval-sandbox class SHALL cover only Docker availability, the Docker launcher, and the memory allowance. The eval-fly class SHALL cover Fly API access with the configured deploy token, the configured app, a resolvable configured image, the SSH transport the factory uses for delivery and collection, and the absence of a Cursor role on the claim being admitted. An unavailable prerequisite SHALL hold affected work without launching an attempt, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. A recorded hold whose check no longer applies to a kind after a configuration change SHALL clear on the next successful poll. These checks SHALL NOT attempt to repair credentials or configuration.

#### Scenario: Wait for Docker or authentication

- **WHEN** Docker is unavailable or required authentication is invalid before launch
- **THEN** the factory reports the prerequisite problem and starts no affected attempt
- **AND** it rechecks availability without consuming attempts or retries

#### Scenario: Detect a repaired prerequisite

- **WHEN** the operator fixes a prerequisite and a subsequent readiness check succeeds
- **THEN** the associated hold clears and affected work may proceed under the other admission controls without restarting the factory

#### Scenario: Switch fixes to the host under a Docker hold

- **WHEN** a Docker hold is recorded for both kinds and the operator changes the fix kind to host execution
- **THEN** the next successful poll clears the fix hold, evaluates host readiness for fixes, and leaves the eval hold in place

#### Scenario: Lose Fly access

- **WHEN** the eval kind runs under Fly execution and the Fly API is unreachable or rejects the deploy token
- **THEN** eval admission is held with the Fly problem named, no Machine is created, and no attempt or retry is consumed

#### Scenario: Hold a frozen Cursor claim under Fly

- **WHEN** a claim with a frozen Cursor role profile is next in line and the eval kind runs under Fly execution
- **THEN** that claim is held with the Cursor role named, its frozen inputs are unchanged, and other eligible eval claims may proceed

_From `specs/factory-claim-lifecycle/spec.md`:_

### Requirement: Classify admission holds by scope

Pause SHALL apply to every kind. The free-disk floor SHALL apply to every kind using the floor configured for the kind being admitted. Docker availability and the memory headroom check SHALL apply only to kinds whose configured execution mode is Docker. Fly readiness SHALL hold only eval work configured for Fly execution. A provider quota hold SHALL apply only to attempts whose configured roles use that provider. Eval suite readiness SHALL hold only eval work; fix readiness (mirrors, workflow contract, fix credential, and in host mode the host prerequisites) SHALL hold only fix work. A hold on one kind SHALL NOT prevent admission of the other kind.

#### Scenario: Hold Codex while fixing with Cursor

- **WHEN** a Codex quota hold is active and the fix roles use Cursor
- **THEN** an eligible bug can still be admitted while eval work using Codex waits

#### Scenario: Lose suite readiness

- **WHEN** the eval suite's prerequisites are unavailable
- **THEN** eval admission is held and fix admission is unaffected

#### Scenario: Stop Docker while fixes run on the host

- **WHEN** Docker is unavailable and the fix kind is configured for host execution
- **THEN** an eligible bug is admitted and eval admission is held with Docker named as the reason

#### Scenario: Apply the fix disk floor

- **WHEN** free space is below the eval floor but at or above the lower floor configured for fixes
- **THEN** an eligible bug is admitted and eval admission is held for disk

#### Scenario: Run without Docker

- **WHEN** Docker is stopped, the eval kind is configured for Fly execution, and the fix kind is configured for host execution
- **THEN** both an eligible eval and an eligible bug are admitted subject to their other holds and Docker is not named as a reason for either

_From `specs/factory-claim-lifecycle/spec.md`:_

### Requirement: Check memory headroom before admission

Before admitting an attempt that will run in the Docker sandbox, the factory SHALL compare Docker's memory allowance minus the memory in use by running containers against a configured per-attempt reservation (default 3 GiB). Insufficient headroom SHALL hold the attempt without recording it or consuming a retry, and status SHALL report the shortfall. Host free memory alone SHALL NOT satisfy the check. An attempt that will run on the host or in a Fly Machine SHALL NOT be subject to the Docker memory probe and SHALL be admitted without it.

#### Scenario: Admit a second attempt with headroom

- **WHEN** one container is running and the remaining Docker allowance exceeds the reservation
- **THEN** the second kind's attempt may be admitted

#### Scenario: Wait for memory

- **WHEN** the remaining Docker allowance is below the reservation
- **THEN** no new sandbox attempt starts and status names memory as the blocking condition

#### Scenario: Admit a host fix while Docker is stopped

- **WHEN** the fix kind is configured for host execution and Docker is not running
- **THEN** the memory probe is not performed for the fix and the bug is admitted subject to the other holds

#### Scenario: Admit a Fly eval while Docker is stopped

- **WHEN** the eval kind is configured for Fly execution and Docker is not running
- **THEN** the memory probe is not performed for the eval and it is admitted subject to the other holds

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Treat in-Machine authentication failure as attempt failure

A model login failure inside the Machine SHALL end the attempt as a technical failure with its diagnostic preserved, subject to the ordinary recovery policy. It SHALL NOT modify credentials on the factory host. When a subsequent readiness check finds a factory-host model login invalid, the existing prerequisite hold SHALL apply with an explanation naming the provider.

#### Scenario: Reject a copied login in the Machine

- **WHEN** a provider rejects the delivered credential inside the Machine
- **THEN** the attempt fails as a technical failure and the factory host's credential files are unchanged

#### Scenario: Find the host login invalid afterwards

- **WHEN** the next readiness check finds the factory host's Codex or Claude login invalid
- **THEN** eval admission is held with the provider named and no attempt is launched

This task's portion: the "Find the host login invalid afterwards" scenario (eval admission held
under `fly` with the provider named).

## Test Plan

Automated tests never contact Fly; they use the fake REST server and fake `flyctl` described above.
Unit cases are your TDD decisions; the obligations below are required.

### INT-002: Machines API client against a fake REST server
- Boundary: `src/agent_factory/fly/api.py` over urllib to a local `http.server` fake recording
  requests.
- Setup: fake server with an in-memory Machine table; token file with owner-only permissions.
- Action: create, get, update metadata for one key, update the config env of a stopped Machine,
  list with metadata filter, stop, start, destroy, destroy again.
- Assertions: create body carries image, guest size, `persist_rootfs = "always"`,
  `auto_destroy = true`, restart policy `no`, `init.exec`, and metadata with owner marker, run id,
  claim id, nonce, deadline epoch, unit key; the bearer token is sent and never logged; metadata
  update changes only the named key; list filtering returns only marked Machines; a 404 on destroy
  is treated as success; a 5xx surfaces as a typed error with the request path.
- Execution: `tests/integration/test_fly_api.py`.

### INT-007: Configuration, intake, doctor, and status
- Boundary: `config.py`, eval intake, `operations.doctor`/`status` with the fake API and a fake
  `flyctl` on `PATH`.
- Setup: local config with evals on `fly` and fixes on `host`; shared config with the Codagent
  defaults; requests naming a Cursor role; a frozen claim with a Cursor role from Docker days.
- Action: load config; parse requests; run doctor and status; run one cycle with the Cursor claim.
- Assertions: `[fly]` defaults are region `ewr`, shared 4 CPUs, 8192 MiB, grace 900 s; shared
  defaults are opus lead and Luna implementor and tester; the Cursor request becomes needs-input
  with a comment naming the role and replacing it clears the flag; the frozen claim is held, not
  mutated; doctor output contains the `eval-fly` group and no line mentioning Docker; doctor
  never issues a create call; with `docker` configured the Docker group still runs; the
  mode-neutral checks appear under the `eval` group in both modes and a bad host Codex login
  holds eval under `fly`; status shows Machine id, state, and deadline for an active run and
  `stopped (quota hold)` with its deadline for a quota-held claim.
- Execution: `tests/integration/test_fly_operations.py`, `tests/integration/test_fly_intake.py`.

## Done When

- Every scenario in the Spec section that falls in this task's portion is covered by a passing test,
  and INT-002 and INT-007 pass at the named paths.
- With `eval.execution` unset, behavior and doctor output for Docker deployments are unchanged
  apart from the `eval` group relabelling; existing tests are updated only for that relabelling and
  for the implementor default.
- No code path in this task creates, stops, starts, or destroys a Machine outside the API client's
  own tests.
- `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest` passes.
