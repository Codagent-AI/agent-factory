# factory-operations Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. Feature configuration SHALL include the feature role profiles, feature limits, the feature admission window, and the feature workflow contract; the feature kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new feature work SHALL be enabled only when the feature configuration is present; when it is removed, existing feature claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The feature kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. Task configuration SHALL include the native task type in routing configuration (default `Task`), the task role profiles (lead, implementor, tester), the task workflow contract (default `factory-task/1`), task limits, the task admission window, and an optional task-specific minimum free disk space; the task kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new task work SHALL be enabled only when the shared task configuration is present; when it is removed, existing task claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The task kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. The eval kind SHALL accept `docker` (the default) or `fly` execution and SHALL NOT accept host execution. Fly configuration SHALL be local and SHALL include the Fly app, region (default `ewr`), Machine CPU kind, CPU count, and memory (default shared, 4 CPUs, 8 GiB), the sandbox image reference, the deploy-token file location alongside the other controller credentials, and the collection grace period. The execution mode, fix disk floor, Fly settings, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration whose eval role defaults are `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`, and `tester = codex:gpt-5.6-luna:medium`, and which enables the feature kind with role defaults matching its fix role defaults and the task kind with a Sonnet-class lead. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific and workflow-specific repository and executable locations SHALL be supplied to the relevant handler rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the Eval, Bug, Feature, and Task behavior and the field names, options, and defaults described by the active specifications

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

#### Scenario: Configure Docker execution for features

- **WHEN** the configuration selects Docker or Fly execution for the feature kind
- **THEN** configuration loading fails and names the unsupported mode

#### Scenario: Leave the feature kind unconfigured

- **WHEN** the configuration has no feature section
- **THEN** no Feature-typed issue is handed off or admitted and the other kinds behave as before

#### Scenario: Remove the feature section with feature claims in flight

- **WHEN** the feature section is removed while one feature attempt is running and another feature claim is settled with an open pull request
- **THEN** the running attempt is still supervised and its result reported, the settled claim still receives review rounds, merge sync, and cleanup, and no new feature is handed off or admitted

#### Scenario: Leave the task kind unconfigured

- **WHEN** the configuration has no task section
- **THEN** no Task-typed issue is handed off or admitted and the other kinds behave as before

#### Scenario: Configure Docker execution for tasks

- **WHEN** the configuration selects Docker or Fly execution for the task kind
- **THEN** configuration loading fails and names the unsupported mode

#### Scenario: Leave task settings at their defaults

- **WHEN** the shared configuration has a task section with role profiles only and the local configuration sets nothing for tasks
- **THEN** tasks use issue type `Task`, contract `factory-task/1`, host execution, an always-open window, and limits of 15 minutes without progress, two hours of execution, and three hours in total

#### Scenario: Reject a malformed task section

- **WHEN** the task section's defaults or limits have a value of the wrong type
- **THEN** configuration loading fails and names the offending task setting

#### Scenario: Remove the task section with task claims in flight

- **WHEN** the task section is removed while one task attempt is running and another task claim is settled with an open pull request
- **THEN** the running attempt is still supervised and its result reported, the settled claim still receives review rounds, merge sync, and cleanup, and no new task is handed off or admitted

### Requirement: Apply shared deployment changes through explicit updates

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, fix defaults, and the branch names for the `agent-evals` harness, Agent Runner, Agent Skills, Agent Validator (the eval default `agent_validator_ref`), and fix target repositories (each defaulting to `main`). Configuration SHALL NOT pin any of these repositories to a commit; commits are resolved per claim at admission and recorded on the claim. Machine-specific paths, schedule, execution limits, and credential-file locations SHALL be configured separately in local TOML. Secret values SHALL remain outside the versioned deployment configuration.

The installed factory SHALL use the shared configuration from its explicitly installed version and SHALL NOT automatically fetch configuration changes from main. Reusable routing workflows SHALL use shared configuration from their explicitly pinned factory revision. Deployment instructions SHALL cover updating the local factory and the caller workflows' routing revision together. Updating configuration SHALL NOT mutate frozen inputs or the execution configuration of an already-running attempt.

#### Scenario: Edit shared configuration in GitHub

- **WHEN** shared deployment configuration changes on main but the local factory has not been explicitly updated
- **THEN** the installed factory continues using its installed configuration
- **AND** ordinary queue polling and operational controls continue without waiting for a software update

#### Scenario: Deploy a shared configuration change

- **WHEN** the operator explicitly updates the local installation and routing workflow pins to the intended factory revision
- **THEN** subsequent routing and new claims use that revision's shared deployment configuration
- **AND** existing claims retain frozen inputs and already-running attempts retain their execution configuration

#### Scenario: Migrate a pinned harness configuration

- **WHEN** the installed configuration still contains a harness commit pin
- **THEN** the factory reports the obsolete setting at startup and in doctor instead of silently ignoring it

#### Scenario: Leave the Validator branch unset

- **WHEN** the shared configuration does not set `agent_validator_ref`
- **THEN** Fly eval claims resolve Agent Validator `main` at admission

### Requirement: Run as a recoverable per-user Mac service

The change SHALL provide a launchd LaunchAgent configuration and setup instructions that start the factory for the configured Mac user on login and restart the controller if it crashes. Execution SHALL use explicit executable, configuration, and credential paths suitable for the service environment, and the LaunchAgent SHALL supply an explicit PATH that includes every executable host-mode fixes need; the resident controller and the supervisors it launches SHALL resolve executables against that environment. Doctor SHALL report the PATH it resolved executables against and, when the LaunchAgent definition is installed at its documented location, SHALL check that every executable host mode needs also resolves on the PATH that definition carries, naming the definition and the missing executable on failure. The LaunchAgent definition, in both the packaged template and the definition the factory renders, SHALL set `USER` and `LOGNAME` in its environment to the installing user, so that the resident controller and every model CLI it launches use the operator's own login-Keychain account. When the installed definition lacks `USER`, doctor SHALL fail a shared check naming the definition file and giving adding `USER` and `LOGNAME` and reloading the service as the action. Controller restarts SHALL preserve running evaluations as required by `factory-claim-lifecycle`; restarting the controller SHALL NOT itself restart or terminate those evaluations.

The service SHALL poll GitHub every five minutes while independently supervising active execution. A long-running repetition SHALL NOT block queue reconciliation, cancellation checks, or pending report delivery.

#### Scenario: Start the user service

- **WHEN** the configured Mac user logs in with the LaunchAgent installed and prerequisites available
- **THEN** the factory starts using its configured paths and credentials and begins normal polling

#### Scenario: Recover a controller crash

- **WHEN** the controller crashes during an evaluation
- **THEN** launchd restarts the controller and it reconciles the surviving evaluation without terminating it merely because of the controller restart

#### Scenario: Poll during a long evaluation

- **WHEN** an evaluation runs for several hours
- **THEN** the service continues its five-minute GitHub checks and pending reporting independently of that evaluation

#### Scenario: Launch a host fix from the service

- **WHEN** doctor run interactively reports host readiness and the resident service admits a bug
- **THEN** the service launches the host attempt with the same resolved executables doctor checked

#### Scenario: Detect a service PATH that lacks the Runner

- **WHEN** the fix kind is configured for host execution, `agent-runner` resolves on the interactive PATH, and the installed LaunchAgent definition carries a PATH on which it does not resolve
- **THEN** doctor fails a shared check naming the definition file and `agent-runner`, even though the interactive resolution succeeded

#### Scenario: Render the service identity

- **WHEN** the LaunchAgent definition is rendered for an installing user
- **THEN** its environment sets `USER` and `LOGNAME` to that user alongside the factory root, App key, and PATH entries

#### Scenario: Detect a service definition without USER

- **WHEN** the installed LaunchAgent definition carries no `USER` entry
- **THEN** doctor fails a shared check naming the definition file and the missing `USER`, with the action to add `USER` and `LOGNAME` and reload the service

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval, eval-sandbox, eval-fly, fix-sandbox, fix-host, feature-host, task-host, or watch and label each so the operator can see which kind a failure holds; the eval group holds the mode-neutral eval checks that apply under every eval execution mode. It SHALL run only the groups that apply to a kind under its configured execution mode, and the watch group only when watching is enabled. The watch group SHALL verify that the installed Agent Runner, `git`, and `gh` are executable on the service PATH; that the default dispatch profile and every per-event profile are in `cli:model:effort` form, and each CLI they select is authenticated and carries the codagent plugin; that the packaged watch session workflow declares a compatible contract version; that the factory repository can be fetched for session checkouts; and that `gh` on the service PATH, which dispatched sessions use to file issues, is authenticated as a login that is not the factory bot and has write access to the factory repository, so the factory admits the issues it files. A failing watch group SHALL hold only the launch of dispatched sessions. Detection, queueing, and the other kinds' admission SHALL continue. Docker availability, memory allowance against one reservation, sandbox launcher checks, and reclaimable Docker space SHALL be checked and reported only under kinds configured for Docker execution; when no kind is configured for Docker, doctor SHALL neither probe Docker nor print any Docker line. The eval-fly group SHALL verify that the Fly API is reachable with the configured deploy token, the configured app exists, the configured image's repository (the configured `image` with any tag removed) is the configured app's `registry.fly.io` repository that the per-claim build pushes to, a Claude login is deliverable as defined in `factory-fly-execution` whenever an eval role uses Claude, using the same bounded Keychain read the launcher uses, the deploy-token file is owner-readable and contains only that token, the factory's own Fly launcher is resolvable, and `flyctl` is executable on the service PATH for transport. The factory SHALL resolve its launcher from the service PATH when present and otherwise from the directory holding the running factory, so that a service started without a bespoke PATH entry still finds the launcher shipped with it. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, the packaged fix and review workflows each declare a compatible contract version, and every fix role has a `cli:model:effort` profile. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When the feature kind is configured, it SHALL run the host checks of the fix-host group against the feature roles, verify that every feature role has a `cli:model:effort` profile, that the packaged feature and define workflows declare a compatible contract version, and that the installed Agent Runner provides the `core/verify-change` builtin workflow, and report each fix target without an `openspec/` directory or without an Agent Validator configuration as informational. When the task kind is configured, it SHALL run the host checks of the fix-host group, including the Agent Validator build checks, against the task roles in the task-host group, and verify that every task role has a `cli:model:effort` profile and that the packaged task and review workflows declare a compatible contract version. When a kind is configured for Docker and Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. On macOS, when a login-Keychain item with service `Claude Code-credentials` and account `unknown` exists, doctor SHALL report it as informational only, explaining that it is a stale login created by a process without `USER`; it SHALL NOT fail on it or delete it. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt, create a Machine, build an image, print any credential, or attempt to repair credentials, Keychain items, or configuration.

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

#### Scenario: Diagnose a mismatched Fly image repository

- **WHEN** the eval kind is configured for Fly execution and the configured image's repository is not the configured app's `registry.fly.io` repository
- **THEN** doctor fails the eval-fly group naming the image and the action to take, and shows fix readiness independently

#### Scenario: Accept the existing image setting

- **WHEN** `[fly] image` is `registry.fly.io/agent-factory-sandbox:base` and the configured app is `agent-factory-sandbox`
- **THEN** doctor passes the image check and per-claim builds push to `registry.fly.io/agent-factory-sandbox`

#### Scenario: Diagnose an unavailable Claude login for Fly

- **WHEN** the eval kind is configured for Fly execution, an eval role uses Claude, the suite environment file has no Claude token, and neither the Keychain item for the service user nor `~/.claude/.credentials.json` is readable, or the Keychain read times out
- **THEN** doctor fails the eval-fly group naming the Claude login source it tried and the action to take, and prints no credential

#### Scenario: Report the stale unknown-account Keychain item

- **WHEN** a `Claude Code-credentials` Keychain item with account `unknown` exists
- **THEN** doctor reports it as informational, does not fail, and leaves the item in place

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

#### Scenario: Diagnose a Runner without verify-change

- **WHEN** the feature kind is configured and the installed Agent Runner lacks the `core/verify-change` builtin workflow
- **THEN** doctor fails the feature-host group naming the missing workflow and shows the other kinds' readiness independently

#### Scenario: List targets without OpenSpec

- **WHEN** the feature kind is configured and a fix target has no `openspec/` directory
- **THEN** doctor reports that target as informational without failing the feature-host group

#### Scenario: List targets without Agent Validator configuration

- **WHEN** the feature kind is configured and a fix target has no `.validator/config.yml`
- **THEN** doctor reports that target as informational without failing the feature-host group

#### Scenario: Missing role profile

- **WHEN** a configured pull-request kind lacks a profile for one of its roles, or a role is not in `cli:model:effort` form
- **THEN** doctor fails that kind's group naming the role and the configuration section to fix, and the kind admits no new claim until it is fixed

#### Scenario: Diagnose a watch dispatch without a writer login

- **WHEN** watching is enabled and `gh` on the service PATH is not authenticated, is authenticated as the factory bot, or lacks write access to the factory repository
- **THEN** doctor fails the `watch issue login` check naming the login problem and the action to take, reports the other groups independently, and pending PR-READY check and triage dispatches wait without starting a session

#### Scenario: Run doctor with watching disabled

- **WHEN** watching is disabled or unconfigured
- **THEN** doctor runs no watch check and prints no watch group

#### Scenario: Diagnose task readiness

- **WHEN** the task kind is configured and the packaged task workflow lacks a compatible contract, or a task role has no `cli:model:effort` profile
- **THEN** doctor fails the task-host group naming the problem and shows the other kinds' readiness independently

#### Scenario: Run doctor without the task kind

- **WHEN** the task kind is not configured
- **THEN** doctor runs no task-host check and prints no task-host group

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind:

- each busy Priority lane with its holder and progress, and a summary line that starts with `<kind> slot: ` and reads exactly `<kind> slot: free` when no attempt of the kind is unfinished in any lane;
- waiting work and why it waits, including work waiting for its own lane and work held because a higher lane of its kind is busy, naming that lane and its holder;
- blocked fix, feature, and task claims;
- settled fix, feature, and task claims with eligible review comments waiting for their lane or for a higher lane of their kind;
- pending merge syncs and their last failure reason;
- pause state and blocking conditions;
- the next permitted start time, when it can be determined;
- the factory job cap: the attempts counted in its window against the cap and the window length;
  while the cap is reached, also that it is reached, its earliest clear time, and the reset
  command;
- the unclaimed Ready cards that wait only for the job cap in the current cap episode.

For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's
state including whether it is stopped for a quota hold, and the attempt's recorded deadline.
It SHALL also list Machines that reconciliation reported as unknown to the store or as failed
cleanup.

It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a
merge sync, or holding a recorded cleanup failure. A recorded cleanup failure includes a
failed terminal release, an undelivered human-review expiry report, and a failed,
refused, or skipped registry image deletion. For each such failure it SHALL show the
claim, the failed item, and the last reason, whether the claim is settled, cancelled, or
superseded. Claims whose card is Done with nothing pending, and superseded claims with
nothing pending, SHALL be omitted unless `--all` is given, which lists every saved claim.

It SHALL expose enough saved state to distinguish active execution, an admission-window
wait, a usage hold, a memory or disk hold, a job-cap hold, an unavailable prerequisite, a blocked claim, a
waiting review round, and unfinished reporting. Status SHALL remain usable while execution
is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval lane's holder, `fix slot: free`, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its lane, or a higher lane of its kind, is busy
- **THEN** status names the claim, the PR, and the busy lane it waits for

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

#### Scenario: Inspect a blocked feature

- **WHEN** a feature claim stopped during definition
- **THEN** status shows `feature slot: free` and the blocked feature with its stop reason and pushed branch

#### Scenario: Inspect a failed registry image deletion

- **WHEN** a superseded eval claim's registry image deletion failed on the last poll
- **THEN** plain `status` lists that claim with the image tag and the registry's reason until a later poll deletes the image

#### Scenario: Inspect a declined task

- **WHEN** a task claim was declined by triage
- **THEN** status shows `task slot: free` and the blocked task with its decline reason

#### Scenario: Inspect the task slot

- **WHEN** a task attempt is running
- **THEN** status shows the task lane's holder and progress beside the eval, fix, and feature slot lines

#### Scenario: Inspect the job cap below its limit

- **WHEN** 42 attempts have started in the last 24 hours and the cap is 100 attempts per 24 hours
- **THEN** status shows 42 of 100 attempts in the last 24 hours and no job-cap hold

#### Scenario: Inspect a reached job cap

- **WHEN** the job cap is reached, a fix claim's retry is held by it, and a Ready feature card waits only for it
- **THEN** status shows that the cap is reached, its earliest clear time, and the reset command; it shows the fix claim waiting on the job cap; and it lists the feature card's issue as waiting for the job cap

#### Scenario: Inspect several busy lanes of one kind

- **WHEN** a Low and a High fix attempt are running
- **THEN** status shows a `fix slot: ` summary line that does not read `free`, and each busy fix lane with its Priority, holder, and progress

#### Scenario: Inspect work held by a higher lane

- **WHEN** a High fix attempt is running and an eligible Medium bug waits in Ready with the Medium fix lane free
- **THEN** status shows the Medium bug waiting because the High fix lane is busy, and names that lane's holder

#### Scenario: Inspect an attempt that predates lanes

- **WHEN** a fix attempt with no recorded lane is unfinished
- **THEN** status shows that the attempt holds every fix lane until it finishes

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation, merge syncs, and pending reporting, and MAY start eligible work in any free lane. It SHALL apply the same admission windows, pause state, factory job cap, prerequisite, memory, and quota holds, and Priority lane guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work whose lane is free, no higher lane of its kind busy, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside a kind's window, while paused, while the factory job cap is reached, or while the work's lane or a higher lane of its kind is occupied
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work, syncs, and reporting

### Requirement: Persist pause and enforce configured admission controls

`agent-factory pause` SHALL save a factory-wide pause while allowing current attempts of every kind to finish. `agent-factory resume` SHALL clear that pause without clearing unrelated usage or prerequisite holds. The saved pause SHALL survive controller restarts. Under the default schedule, each eval repetition or recovery attempt SHALL start only from 00:00 up to but not including 15:00 local time; fix attempts SHALL follow the separately configured fix window, open by default. Already-running attempts SHALL follow the lifecycle limits rather than stop solely because a window closes.

Configuration SHALL support the approved default eval limits of 30 minutes without progress, six hours of execution excluding recognized quota waits, 12 hours total per attempt, the default fix limits of 15 minutes, two hours, and three hours, a five-hour fallback for Codex reset holds, and a default memory reservation of 3 GiB per attempt. These values SHALL be configurable. The free-space minimum configured for the kind being admitted and, for sandbox attempts, the memory reservation SHALL be checked before admission; insufficient space or memory SHALL hold new affected work without consuming an execution retry. The local configuration SHALL note that the fix window matters only when a fix role selects Codex.

#### Scenario: Resume while another hold remains

- **WHEN** the operator resumes a paused factory while a usage hold remains active
- **THEN** the pause clears but execution waits until the usage hold and other admission conditions permit it

#### Scenario: Run below the free-space minimum

- **WHEN** free disk space is below the minimum configured for the kind being admitted
- **THEN** the factory starts no affected attempt, reports the storage problem, and rechecks readiness without consuming a recovery retry

#### Scenario: Pause during a fix

- **WHEN** the operator pauses while a fix and an eval are both running
- **THEN** both finish under their limits and no new attempt of either kind starts until resume

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees and clones, target repository mirrors, and attempt artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations. The operator's working clones used by the merge sync SHALL be outside the root and are never created by the factory. A host-mode fix attempt's Runner session directory SHALL be placed under the attempt's artifact directory by the factory at launch, so it lies under the root and is covered by the same evidence and retention rules; the run record SHALL name it.

Evidence and candidate outputs SHALL be retained according to the evidence retention requirement below. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, state the disk and memory the machine needs to run one eval and one fix concurrently, explain the retention period and what it keeps, and describe operator-managed cleanup of anything outside that rule while preserving work still needed for execution, recovery, or human review. Factory-owned worktrees and clones follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees and clones, mirrors, and artifacts
- **AND** commands and result reports identify the actual paths in use

#### Scenario: Retain evidence after handoff

- **WHEN** an evaluation or fix is handed off for human review
- **THEN** its artifacts and required suite files remain available until the reviewed item moves to Done; evidence remains retained after worktree or clone cleanup until the retention rule removes it

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done,
the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees. For
a fix or feature it SHALL instead remove the per-attempt clones of every attempt of the
claim and, for sandbox attempts, run-specific images. It SHALL preserve results, logs,
SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention
requirement. Worktrees and clones SHALL remain available while work is running, waiting, or
blocked. They SHALL also remain available while a settled claim is in Review, until the
terminal release below applies. The per-attempt clones of a fix, feature, or task claim are
the exception: they follow "Slim finished attempts" below. When verified running work is dragged to Done,
reconciliation SHALL restore Running before cleanup is considered, and that edit SHALL NOT
remove worktrees. Done cleanup SHALL also wait until all of the claim's reporting has been
delivered, so that a human-review command is never published after its worktree has been
removed.

**Terminal release.** The factory SHALL release a claim outside the Review-then-Done path
when it is quiescent: its lifecycle is `settled`, `cancelled`, or `superseded`, no run of
the claim is non-terminal or of unverified ownership, all of the claim's reporting has been
delivered, and, for a settled claim, no post-merge sync is pending. Releasing a claim SHALL
remove the same recorded factory-owned worktrees or clones, run-specific images, and
credential copies that Done cleanup removes. It SHALL happen as follows:

- A `cancelled` or `superseded` claim SHALL be released on the first poll on which it is
  quiescent, whatever its card's status and whether or not its card is still on the
  Project. Its card may never pass through Review or Done.
- A `settled` claim whose card is not observed as Done on the current poll, including a
  claim whose card is no longer on the Project, SHALL be released once the unreviewed
  retention period has elapsed since the claim's recorded terminal time. That period is the
  local setting `[limits] unreviewed_retention_days`, a positive integer that defaults to
  30. Before releasing a settled eval claim that posted a human-review command, the factory
  SHALL first deliver that claim's human-review expiry report, as defined in
  `factory-eval-reporting`.
- A `settled` claim whose card is observed as Done, whose Done observation was durably
  recorded, and whose Review observation was not recorded SHALL be released on the first
  poll on which it is quiescent. No human-review expiry report SHALL be posted.

In each case the claim's evidence SHALL remain subject to the retention requirement.

**Pending post-merge sync.** For cleanup and retention, a settled pull-request claim has a
pending post-merge sync only when both hold:

- its recorded PR has been observed merged;
- its post-merge sync has not completed.

A PR that is still open, or that was closed without merging, leaves no sync pending for
cleanup or retention. If a merge happens later, the post-merge sync still runs as before.
When the PR's state cannot be read on a poll, the sync SHALL be treated as pending for that
poll. Terminal release and pruning SHALL NOT themselves run a post-merge sync. A claim whose
card is no longer on the Project, and whose merged PR has not been synced, therefore keeps
its files, and `status` continues to show it as pending a sync.

**Reopened cleanup.** When a new run starts on a claim whose cleanup had completed, for
example a review round on a settled pull-request claim, the factory SHALL mark that claim's
cleanup incomplete. The new run's clones, images, and credential copies SHALL then be
released under these same rules once the claim is quiescent again.

**Sweep and safety.** The factory SHALL evaluate Done cleanup and terminal release on every
tick. The terminal release SHALL cover every terminal claim in its store whose release is
incomplete, whether or not the Project query returns its card. The factory SHALL persist
cleanup progress and failures, retry incomplete cleanup on later polls, and continue
processing other jobs and claims. Repeated cleanup and controller restarts SHALL tolerate
already-removed owned worktrees, clones, images, and credential copies. Cleanup SHALL
operate only on recorded factory-owned worktrees, clones, images, and credential copies. It
SHALL NOT remove shared source checkouts, the operator's working clones, mirrors, or
another item's worktrees. A claim that is `active`, `waiting`, or `blocked` SHALL NOT be
released.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and any run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Closed settled claim reaches Done without routing

- **WHEN** the poll observes that a settled factory-owned claim's issue is closed and its card is not Done
- **THEN** the poll moves the card to Done

#### Scenario: Release a Done claim with no Review observation

- **WHEN** a settled claim's card is observed as Done with a durable Done observation but no recorded Review observation
- **THEN** the first quiescent poll releases its owned worktrees or clones without a human-review expiry report
- **AND** pending reporting or a pending post-merge sync delays release

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, or blocked, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution and recovery, however long ago the claim was admitted

#### Scenario: Keep a settled claim in Review within the period

- **WHEN** a settled eval claim has been in Review for 20 days under the default unreviewed retention period
- **THEN** its evals worktree and retained artifacts remain and its human-review command still works

#### Scenario: Release a cancelled fix claim

- **WHEN** a fix claim's issue is closed while an attempt is running and the attempt has since stopped
- **THEN** the next poll removes its clones, run-specific images, and credential copies without waiting for Review or Done
- **AND** its evidence remains until the retention rule removes it
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Release a cancelled eval claim

- **WHEN** an eval claim is cancelled by issue closure and its last repetition has stopped and its cancellation comment has been delivered
- **THEN** the next poll removes its Runner, Skills, and evals worktrees whatever its card's status

#### Scenario: Release a superseded claim

- **WHEN** a writer re-requests a settled claim's issue, a fresh claim supersedes it, and the superseded claim has no non-terminal run
- **THEN** the next poll removes the superseded claim's recorded worktrees or clones, run-specific images, and credential copies
- **AND** the fresh claim's worktrees and clones are untouched

#### Scenario: Release a settled claim left in Review

- **WHEN** a settled fix claim with an open PR has been in Review for longer than 30 days since it settled under the default unreviewed retention period, and its reporting is delivered
- **THEN** the next poll removes the clones of every attempt, any run-specific images, and credential copies
- **AND** its PR, branch, and outcome record remain

#### Scenario: Keep a merged claim whose sync failed

- **WHEN** a settled fix claim's PR merged, its post-merge sync is blocked by uncommitted changes in the working clone, and it has been outside Done for longer than the unreviewed retention period
- **THEN** its clones and evidence are kept, whether or not its card is still on the Project, and status shows the pending sync
- **AND** once a later poll completes the sync for a card still on the Project, the claim is released and pruned under the same rules

#### Scenario: Release a claim whose PR was closed without merging

- **WHEN** a settled fix claim's PR was closed without merging and it has been outside Done for longer than the unreviewed retention period with its reporting delivered
- **THEN** its clones are released and its evidence is pruned without waiting for a sync

#### Scenario: Hold Done cleanup for an undelivered review command

- **WHEN** posting a settled eval's human-review command failed and its card is then moved to Done
- **THEN** its worktrees are kept until a later poll delivers the command, and they are removed on that poll or the next

#### Scenario: Wait for undelivered reporting

- **WHEN** a claim is otherwise eligible for terminal release but a report for it has not yet been delivered
- **THEN** nothing is released until that report is delivered on a later poll

#### Scenario: Release a claim whose card left the Project

- **WHEN** a cancelled claim's card has been removed from the Project
- **THEN** the tick still releases the claim's recorded worktrees or clones from the store's record of the claim

#### Scenario: Reopen cleanup for a review round

- **WHEN** a settled fix claim's clones were released after the unreviewed period and a writer then posts eligible review comments on its still-open PR
- **THEN** the review round runs in fresh clones
- **AND** once the round's run ends and the claim is quiescent, those new clones are released under the same rules

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's or a terminal claim's worktrees fails, or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

### Requirement: Provision the initial GitHub deployment

The change SHALL include the GitHub setup for the Codagent example deployment: an organization-level Codagent Project, native Eval issue type, use of the native Bug issue type, `eval-request` and red `needs-input` labels in each configured source repository, regular Markdown eval issue template, shared routing rules and reusable workflow, source-repository caller workflows, required permissions, general issue/PR auto-add, and closure automation. For the fix kind it SHALL additionally provision a fine-grained repository credential with Contents, Pull requests, and Issues access to the target repositories and no workflow, administration, or Project access, preferably on a machine user, and a ruleset on each target repository requiring a pull request for changes to `main` with no bypass for the credential's owner.

The board SHALL have Backlog, Ready, Running, Review, and Done Status columns, horizontal groups by native issue Type, no field-based sorting, and views for the eval queue and active factory work. It SHALL expose configured Owner, Refs, and Verdict fields and visible attention labels. Verdict options SHALL support `pending-human-review`, `failed`, `quota-deferred`, and `infra-error`; the factory SHALL never assign `passed`.

The initial eval request source SHALL be `Codagent-AI/agent-evals`. General work from the configured Codagent repositories, Bug-typed issues included, SHALL enter Backlog; eval routing SHALL initialize factory ownership and Ready for authors with the required repository access as defined in `factory-routing`, without a general auto-add rule undoing that routing. Other authors' requests SHALL enter Backlog without factory assignment. The initial general-work repository set SHALL cover agent-runner, agent-skills, agent-validator, agent-plugin, and agent-evals, and SHALL be configurable. Issue/PR closure automation SHALL move associated cards to Done.

Routing and the local controller SHALL authenticate with an organization-owned GitHub App with organization Projects read/write, repository Issues read/write, and Contents, Pull requests, and Metadata read access at minimum. Suite and fix PR operations SHALL use their separate credentials. Installation SHALL cover the configured repositories. App private keys and the fix credential SHALL remain outside version control, with local owner-only file access and Actions secret storage for caller workflows.

#### Scenario: Create an eval request in the configured source repository

- **WHEN** a user with write, maintain, or admin access to the source repository creates an issue from the eval template
- **THEN** the configured automation places it in the Project's Ready column with native Eval type and factory ownership without a separate manual handoff
- **AND** general auto-add behavior does not reset the routed request to Backlog

#### Scenario: Add general work

- **WHEN** an ordinary issue or PR is created in a configured source repository without the eval request marker
- **THEN** it is added to the shared Project in Backlog without becoming a factory request

#### Scenario: Arrange the board manually

- **WHEN** the operator views the configured board
- **THEN** Status determines columns, native Type determines horizontal groups, and cards can be manually reordered without a field sort overriding their positions

#### Scenario: Close tracked work

- **WHEN** a tracked issue or PR is closed
- **THEN** its card moves to Done through closure automation

#### Scenario: Push to main with the fix credential

- **WHEN** the fix credential attempts a direct push to `main` on a target repository
- **THEN** the ruleset rejects it and only a pull request can change `main`

### Requirement: Document installation and service operation

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, mirrors and working clones, Docker startup and memory allowance where Docker execution is used, the fix execution mode and what host mode keeps and gives up, the eval execution mode and what Fly execution keeps and gives up compared with Docker, model authentication, GitHub routing and board permissions, suite prerequisites, the packaged fix workflow and its contract version, explicit service paths including the LaunchAgent PATH, login behavior, preventing idle sleep, and the evidence retention period. For Fly execution the documentation SHALL cover the one-time Fly organization, app, and deploy-token setup, the per-claim image build, that `[fly] image` names the repository builds push to, the Runner Dockerfile's `FACTORY_CLI_REFRESH` build argument and the Runner revision it requires, that old `claim-` tags are not removed from the registry, each Fly setting and its default, that Cursor role profiles are unavailable on Fly, that the human-review command runs on the Mac against the collected artifact directory, that a lost Machine costs its repetition, and the worst-case cost per attempt implied by the deadline. Documentation SHALL state plainly that in host mode, on a machine where the operator's own GitHub login is available, the separate fix credential and the PR-only rulesets are conventions the launched process follows rather than boundaries an autonomous agent cannot cross, and that trusted-writer admission and human merge are the enforceable controls. Documentation SHALL explain doctor, status and `--all`, tick, pause, resume, service installation and restart, evidence locations including a host attempt's Runner session directory, the human-review handoff, the blocked-bug loop, and the merge sync. The model authentication documentation SHALL, in the existing single section, explain for host fixes and Fly evals where each Claude and Codex login lives, which Keychain service and account Claude uses, that the service environment must contain `USER` and `LOGNAME`, how a Fly attempt's Claude login is resolved (suite-environment token first, then the Keychain, then the credential file), and how to recover each failure; it SHALL NOT instruct the operator to copy the Keychain login into a file. The GitHub setup documentation SHALL describe the harness setting as a branch resolved at admission, not a commit pin.

Credentials for the suite's candidate branch, the fix PR credential, the Fly deploy token, and board/routing credentials SHALL remain separately configured. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

#### Scenario: Set up the supported deployment

- **WHEN** an operator follows the installation instructions with the required credentials, suite behavior, and a Runner branch that can run the packaged fix workflow
- **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution of both kinds, inspect progress, pause and resume work, run a posted human-review command, and review a factory fix PR

#### Scenario: Reuse the public example

- **WHEN** another organization follows the public setup documentation
- **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
- **AND** the documentation clearly identifies any additional handler, suite, or workflow implementation needed for different work behavior

#### Scenario: Set up host execution

- **WHEN** an operator follows the instructions to run fixes on the host
- **THEN** the documentation tells them the setting, the doctor checks to pass, the PATH the service needs, what the sandbox guarantees they lose, and that the credential and ruleset are not enforceable against the agent

#### Scenario: Set up Fly execution

- **WHEN** an operator follows the instructions to run evals on Fly
- **THEN** the documentation tells them the Fly setup steps, the image build, the settings and defaults, the doctor checks to pass, the transport the service needs, what changes for human review, and what a lost Machine costs

#### Scenario: Recover model authentication

- **WHEN** an operator sees a Claude authentication failure in a host fix or a Fly eval
- **THEN** the documentation tells them where that login lives, which Keychain account applies, what the service environment must contain, and how to recover, without a manual Keychain-to-file copy step

### Requirement: Slim finished attempts

On every tick, once no run of a claim is non-terminal, the factory SHALL remove the bulk that
the claim's finished attempts leave on disk and that a later attempt rebuilds. It SHALL NOT
wait for release or retention to do this:

- For a fix, feature, or task claim, it SHALL remove:
  - the per-attempt clones under the local root of every attempt whose run was reserved;
  - each attempt's Runner source snapshots (`attempt-*/audit-*/snapshot/runner-source`).

  Every attempt and review round cuts fresh clones, so this SHALL apply whether the claim is
  open, blocked, in Review, or terminal. Clones cut for an attempt whose run is not yet
  reserved SHALL be kept.

  Before it removes a clone, the factory SHALL copy into that attempt's artifact directory
  what the clone holds that git cannot rebuild:
  - the target clone's `validator_logs`;
  - a `clone-state.patch` with the clone's status and uncommitted diff.

  If the copy fails, the factory SHALL keep the clone. The retention rule SHALL cover both
  copies.
- For an eval claim whose lifecycle is `settled`, `cancelled`, or `superseded`, it SHALL remove
  only the installed dependency directories (`node_modules`) at the top level of each
  repetition's `.runtime/candidate-worktree` and one level below it. Everything else SHALL be
  kept, because rescoring hashes the acceptance artifacts recorded in the checkout and the
  Runner output, and human review serves `dist`. An eval claim that is still open SHALL keep
  its dependencies, because a recovery attempt may resume a repetition from its checkpoint.

Slimming SHALL NOT follow a link to remove anything outside the attempt's artifact directory
or the claim's clone directory. It SHALL keep outcomes, results, provenance, diffs, logs,
Runner output, and session state for the retention rule. It SHALL record in the claim's cleanup
record the runs it slimmed and any failures, and it SHALL retry failures on later ticks. When a
later run of the claim finishes, the factory SHALL slim it too.

#### Scenario: Slim a blocked fix claim

- **WHEN** a fix claim's attempt ends with `needs-input` and no other run of the claim is non-terminal
- **THEN** the next tick copies the clone's validator logs and uncommitted state into the attempt's evidence
- **AND** the next tick removes the claim's clones and the attempt's Runner source snapshots
- **AND** its outcome, logs, and session state remain

#### Scenario: Slim a settled eval

- **WHEN** an eval claim settles as `pending-human-review`
- **THEN** the next tick removes the installed dependencies from each repetition's candidate checkout
- **AND** the human-review command can still serve the scored build, and the repetition can still be rescored

### Requirement: Retain evidence for a bounded period

The factory SHALL prune the attempt evidence of terminal claims after configurable retention
periods. The retention period is `[limits] evidence_retention_days`, default 3 days. The
unreviewed retention period is `[limits] unreviewed_retention_days`, default 30 days. Both
periods SHALL be local configuration.

A claim's evidence SHALL be eligible for pruning only when all of these hold:

- no run of the claim is non-terminal or of unverified ownership;
- all of the claim's reporting has been delivered;
- for a settled claim, no post-merge sync is pending, as defined in "Clean up worktrees after
  review";
- one of these paths applies:
  - **Done path.** The claim is settled and its card is observed as Done on the poll that
    prunes. The retention period has elapsed since the factory first durably recorded that
    Done observation, and either its Review-then-Done cleanup or its terminal release has
    completed.
  - **Cancelled or superseded path.** The claim is `cancelled` or `superseded`, the
    retention period has elapsed since its recorded terminal time, and its terminal release
    has completed. This path applies whatever the card's status and whether or not the card
    is still on the Project.
  - **Unreviewed path.** The claim is settled, its card is not observed as Done on the poll
    that prunes (including a card no longer on the Project), the unreviewed retention period
    has elapsed since its recorded terminal time, and its terminal release has completed.

The Done path SHALL be established from the board observation of the poll that prunes. A
card observed in any other state SHALL reset the recorded Done observation.

Pruning SHALL remove logs, Runner session state, agent session state, and agent output under
the attempt's artifact directory and, for a host attempt, its recorded Runner session
directory, and an eval repetition's remaining candidate checkout. It SHALL remove only enumerated evidence paths and SHALL keep any file or
directory it does not recognise. Pruning SHALL keep the fix or feature outcome, eval result
and provenance records, and the attempt's issue input. It SHALL NOT touch candidate
branches, PRs, mirrors, SQLite history, or the operator's working clones.

The factory SHALL record what it removed and any failures, and retry failed pruning on later
polls. Pruning SHALL run as a sweep on each tick over every terminal claim whose pruning is
incomplete, whether or not the Project query returns its card, so evidence that predates
this rule is covered. When a new run starts on a claim whose evidence was already pruned,
the new run's evidence SHALL become subject to this rule again. The earlier removal record
SHALL be kept.

#### Scenario: Prune after the retention period

- **WHEN** a claim's card was observed Done more than 3 days ago under the default retention and nothing still needs its evidence
- **THEN** the next tick removes its logs, session state, and agent output
- **AND** its outcome or result records, issue input, candidate branches, and PRs remain

#### Scenario: Prune a Done claim released without Review

- **WHEN** a settled claim reached Done without a recorded Review observation, its terminal release completed, and 3 days have passed since its first durable Done observation
- **THEN** the next tick prunes its attempt evidence under the Done path

#### Scenario: Skip a Done claim with a pending sync

- **WHEN** a fix claim's card is Done and its PR merged, but its post-merge sync has not completed
- **THEN** its evidence is not pruned however old the claim is

#### Scenario: Prune a cancelled claim that recorded a PR

- **WHEN** a cancelled fix claim recorded a PR and more than 3 days have passed since it was cancelled
- **THEN** its evidence is pruned whatever its card's status, without waiting on a post-merge sync, which only settled claims receive

#### Scenario: Prune a superseded claim outside Done

- **WHEN** a claim was superseded 15 days ago, its card is in Review for the fresh claim, and its terminal release has completed
- **THEN** the superseded claim's evidence is pruned and the fresh claim's evidence is untouched

#### Scenario: Keep a recently superseded claim whose runs finished long ago

- **WHEN** a claim's last run finished 40 days ago and the claim was superseded 1 day ago
- **THEN** its evidence is kept until 3 days after it was superseded

#### Scenario: Prune a settled claim left in Review

- **WHEN** a settled claim's card has stayed in Review for more than 30 days since it settled, its reporting is delivered, and its terminal release has completed
- **THEN** its evidence is pruned
- **AND** its outcome or result records and issue input remain

#### Scenario: Move a Review card to Done late

- **WHEN** a settled claim's card moves from Review to Done 25 days after it settled
- **THEN** the unreviewed path no longer applies and its evidence is kept until 3 days after that Done observation

#### Scenario: Reach Done after a long time

- **WHEN** a claim that was settled only today, after months of work, is moved to Done today
- **THEN** its evidence is retained for the full retention period from today's Done observation

#### Scenario: Sweep pre-existing history

- **WHEN** the factory first runs with this rule against a database whose old Done claims have no recorded Done observation
- **THEN** it records the observation on that poll and prunes those claims only after the retention period from that observation
- **AND** old cancelled, superseded, and unreviewed settled claims are judged from the terminal time recorded for them under `factory-claim-lifecycle`

#### Scenario: Fail to prune

- **WHEN** removal of an eligible claim's evidence fails partway
- **THEN** the factory records the failure and remaining work and retries on later polls without blocking other jobs

### Requirement: Locate the Agent Validator checkout

The local configuration SHALL accept an optional `[repositories] agent_validator` path to the operator's Agent Validator checkout. When the key is unset, the checkout SHALL be the `agent-validator` directory next to the configured `agent_runner` checkout. The public example configuration SHALL show the key without a personal path. A deployment whose kinds use only Docker execution SHALL NOT need a Validator checkout.

#### Scenario: Use the sibling default

- **WHEN** the local configuration sets `agent_runner = "/Users/paul/codagent/agent-runner"` and leaves `agent_validator` unset
- **THEN** the factory and the deploy use `/Users/paul/codagent/agent-validator` as the Agent Validator checkout

#### Scenario: Use an explicit path

- **WHEN** the local configuration sets `agent_validator` to a path
- **THEN** the factory and the deploy use that path and ignore the sibling default

#### Scenario: Run Docker-only without a checkout

- **WHEN** the eval and fix kinds use Docker execution, the feature kind is not configured, `agent_validator` is unset, and no sibling checkout exists
- **THEN** configuration loads, doctor reports no Validator checkout problem, and evals and fixes are admitted as before this change

### Requirement: Build the host Agent Validator from main on deploy

Unless the operator passes `--no-validator`, `scripts/deploy.sh` SHALL bring the Agent Validator checkout up to `origin/main` and build it, so that the `agent-validator` host fixes and features run is built from Agent Validator `main`. Before pausing the factory, the deploy SHALL fetch the checkout and check it, without changing its working tree. The deploy SHALL skip the whole Validator step with a warning, and continue, when the checkout is not on `main`, has uncommitted changes, or has commits not on `origin/main`. It SHALL never push the checkout. When `agent_validator` is unset and no checkout exists at the default path, the deploy SHALL skip the Validator step with a warning and continue. When `agent_validator` is set and the path is not a git checkout, the deploy SHALL stop before pausing the factory and deploy nothing. When the Validator step will run and `bun`, which the Validator's build needs, is not on the deploy's PATH, the deploy SHALL stop before pausing the factory, name `bun`, and deploy nothing; `--no-validator` deploys without it.

After pausing the factory, the deploy SHALL fast-forward the checkout to `origin/main` and build it with the Validator's local build, which records the built commit in `agent-validator --version`. The deploy SHALL skip both the fast-forward and the build with a warning, and continue, while any fix or feature attempt runs on the host. Whether an attempt runs on the host SHALL be decided by the backend recorded for that attempt, not by the current execution configuration; an attempt whose backend is not yet recorded SHALL count as a host attempt. A running eval, under any execution mode, SHALL NOT prevent the build. A failed build SHALL stop the deploy with the factory paused, as a failed Agent Runner build does. After a build, the deploy SHALL check that `agent-validator` on the PATH in the installed LaunchAgent definition resolves to the executable this build produced, and warn, naming both paths, when it does not. The deploy SHALL NOT create, replace, or remove the operator's `agent-validator` link. `--no-runner` SHALL continue to affect only the Agent Runner step. The existing rule that removes old releases only while every slot is free SHALL be unchanged.

#### Scenario: Deploy with the checkout on main

- **WHEN** the operator deploys, the Validator checkout is on `main` with no local changes, and `origin/main` has new commits
- **THEN** the checkout is fetched but unchanged before the pause, and is fast-forwarded to `origin/main` and built after the factory pauses
- **AND** `agent-validator --version` reports the new commit, and nothing is pushed

#### Scenario: Skip an unsafe checkout

- **WHEN** the Validator checkout is on another branch, has uncommitted changes, or has commits not on `origin/main`
- **THEN** the deploy warns that it is skipping the Agent Validator update and build, leaves the checkout and its build unchanged, and completes the rest of the deploy

#### Scenario: Defer the build while a host fix runs

- **WHEN** the operator deploys while a fix attempt runs on the host
- **THEN** the Validator fast-forward and build are both skipped with a warning, the checkout and its build are unchanged, the running attempt keeps using the Validator it started with, and the deploy completes

#### Scenario: Defer the build after the fix mode changes

- **WHEN** a fix attempt launched on the host is still running, and `[fix] execution` has since been changed to `docker`
- **THEN** the deploy still treats it as a host attempt and skips the Validator fast-forward and build

#### Scenario: Build while only an eval runs

- **WHEN** the operator deploys while a Fly eval runs and no fix or feature attempt runs on the host
- **THEN** the Validator is built

#### Scenario: Deploy without a default checkout

- **WHEN** `agent_validator` is unset and no `agent-validator` directory exists next to the Agent Runner checkout
- **THEN** the deploy warns that it is skipping the Agent Validator step and completes the rest of the deploy

#### Scenario: Deploy with a missing configured checkout

- **WHEN** `agent_validator` is set to a path that is not a git checkout
- **THEN** the deploy stops before pausing the factory, names the path, and deploys nothing

#### Scenario: Deploy without the build tool

- **WHEN** the Validator step will run and `bun` is not on the deploy's PATH
- **THEN** the deploy stops before pausing the factory, names `bun` and `--no-validator`, and deploys nothing

#### Scenario: Fail the Validator build

- **WHEN** the Validator build fails
- **THEN** the deploy stops with the factory paused and names the checkout

#### Scenario: Warn about a service PATH that uses another Validator

- **WHEN** the build succeeds but `agent-validator` on the LaunchAgent PATH resolves to an npm-installed copy
- **THEN** the deploy warns, naming the resolved executable and the built one, and does not change either

#### Scenario: Skip the Validator on request

- **WHEN** the operator deploys with `--no-validator`
- **THEN** the Validator checkout and its build are untouched, and the Agent Runner step runs as before

### Requirement: Report host attempts in status

`agent-factory status` SHALL include a line `host attempts: <n>`, where `<n>` counts the unfinished attempts of every kind whose recorded backend is `host`, plus unfinished attempts whose backend is not yet recorded. The line SHALL NOT change the existing slot lines.

#### Scenario: Count a host fix after a mode change

- **WHEN** a host fix attempt is running, a Fly eval is running, and `[fix] execution` has been changed to `docker`
- **THEN** status shows `host attempts: 1` and the existing eval and fix slot lines

#### Scenario: No host attempts

- **WHEN** no unfinished attempt runs on the host
- **THEN** status shows `host attempts: 0`

### Requirement: Diagnose the Agent Validator build

Doctor SHALL report the Agent Validator checkout path, the commit that the `agent-validator` on the service PATH reports, and whether that commit is behind the checkout's `origin/main`. A build that is behind SHALL be informational and SHALL NOT fail doctor. When the fix kind is configured for host execution, or the feature kind is configured, the fix-host and feature-host groups SHALL fail when the Validator checkout is missing, or when `agent-validator` on the service PATH, and on the PATH in the installed LaunchAgent definition when it is at its documented location, does not resolve to the checkout's built executable. The failure SHALL name the resolved executable, the expected one, and the action to take. When the eval kind is configured for Fly execution, the eval-fly group SHALL fail when the Validator checkout is missing or is not a git repository. Doctor SHALL NOT build, fetch into, or relink the Validator.

#### Scenario: Report a current build

- **WHEN** host fixes are configured and `agent-validator` on the service PATH is the checkout's build at the checkout's `origin/main`
- **THEN** doctor passes the Validator check and shows the checkout path and the reported commit

#### Scenario: Report a build that is behind

- **WHEN** a deploy skipped the Validator build and the build on PATH reports a commit behind the checkout's `origin/main`
- **THEN** doctor reports the reported commit and that it is behind, as informational, and does not fail

#### Scenario: Diagnose an npm Validator on the host

- **WHEN** host fixes or features are configured and `agent-validator` on the service PATH resolves to an npm-installed copy
- **THEN** doctor fails the host group, naming the resolved executable, the checkout's expected executable, and the action to link it

#### Scenario: Diagnose a missing checkout for Fly evals

- **WHEN** the eval kind is configured for Fly execution and the Validator checkout does not exist
- **THEN** doctor fails the eval-fly group, naming the expected path

### Requirement: Document the Agent Validator build

The installation and operations documentation SHALL explain:

- the Agent Validator checkout and its sibling default;
- how to link `agent-validator` on the service PATH to the checkout's build once;
- that deploys fast-forward and build the checkout under the same skip rules as the Agent Runner checkout, and skip the build while a host fix or feature runs;
- `--no-validator`;
- that Fly eval images install the Validator at the claim's recorded revision;
- that Docker execution still uses the published npm release;
- that changing the Validator can change eval results, and that existing results stay tied to the Validator revision they recorded.

#### Scenario: Set up the host Validator

- **WHEN** an operator follows the documentation to run fixes or features on the host
- **THEN** it tells them where the Validator checkout goes, how to build it and link it on the service PATH, and which doctor check confirms it

### Requirement: Configure service-driven watching

The shared configuration SHALL accept an optional `[watch]` section with these settings:

- `enabled` (default false);
- the factory `repository` (`owner/name`) that dispatched sessions check out, required when watching is enabled;
- the default dispatch `agent` profile in `cli:model:effort` form, required when watching is enabled;
- optional per-event profiles for `PR-READY` and `FAILURE`;
- the concurrency cap (default 2, at least 1);
- the failure grace period in minutes (default 7, zero or more);
- the session timeout in minutes (default 90, at least 1);
- `auto_merge`, a boolean (default false) that turns on risk rating and auto-merge of low-risk pull requests.

When watching is enabled, configuration loading SHALL fail on a missing repository or default profile, a profile that is not in `cli:model:effort` form, an unknown event name, a value out of range, or an `auto_merge` that is not a boolean, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior, with auto-merge off. The Codagent example configuration SHALL enable watching with the default profile `claude:claude-sonnet-5-5:medium` and `auto_merge = true`. Each cycle SHALL read the watch settings from the configuration it loads, so a changed profile, concurrency cap, grace period, timeout, or `auto_merge` applies to dispatches that start after the change. A session that is already running SHALL keep its profile and timeout. A merge that is waiting SHALL use the `auto_merge` value of the cycle that evaluates it, so turning it off stops every waiting merge. The checks a merge requires SHALL come from each repository's GitHub rulesets, not from this configuration.

The section SHALL have no per-day session budget. A `daily_sessions` key left in the section SHALL NOT fail configuration loading and SHALL have no effect.

#### Scenario: Configure the dispatch model and budget

- **WHEN** the shared configuration enables watching with the agent `claude:claude-sonnet-5-5:medium` and a concurrency cap of 3, and sets no session budget because none exists
- **THEN** dispatched sessions run with that profile, no more than 3 run at once, and no event is skipped because of how many sessions started that day

#### Scenario: A leftover budget setting

- **WHEN** watching is enabled and the `[watch]` section still sets `daily_sessions = 5`
- **THEN** configuration loads, and a sixth event in a local day starts a session like any other

#### Scenario: Reject an invalid profile

- **WHEN** watching is enabled with the agent `sonnet`
- **THEN** configuration loading fails and names the `[watch]` agent setting

#### Scenario: Leave watching unconfigured

- **WHEN** the shared configuration has no `[watch]` section
- **THEN** the factory detects no watch event, starts no session, and shows watching as disabled in status

#### Scenario: Reject a non-boolean auto-merge

- **WHEN** watching is enabled with `auto_merge = "yes"`
- **THEN** configuration loading fails and names the `[watch]` auto_merge setting

#### Scenario: Turn auto-merge off while a merge waits

- **WHEN** a `low`-rated pull request waits for a running check and `auto_merge` is changed to false before the next cycle
- **THEN** the next cycle does not merge it and ends its merge with the reason that auto-merge is off

### Requirement: Report watch dispatches in status

When watching is enabled, `agent-factory status` SHALL show a watch section with:

- whether watching is enabled, and whether auto-merge is on;
- the time of the last detection pass;
- each `launched` dispatch with its event, claim, pull request when there is one, model profile, and elapsed time;
- the number of `pending` dispatches, and why they wait: the concurrency cap, a failing watch doctor group, or a running check of the same pull request;
- each ended dispatch whose usage delivery to the development-audit destination did not succeed;
- the number of sessions started today and today's known estimated cost, with no budget;
- every dispatch recorded `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted` whose claim is not yet observed Done, cancelled, or superseded;
- each undelivered dispatch comment with its last failure reason;
- for each completed dispatch whose claim is not yet observed Done, cancelled, or superseded, the factory issues its session filed or updated, with the pull request, or the claim's issue for a triage;
- for each completed PR-READY check with a rating whose claim is not yet observed Done, cancelled, or superseded, the pull request, its rating, and its merge state: waiting with what it waits for, merged, or not merged with the reason.

When watching is disabled, status SHALL show one line saying so, and it SHALL still list `launched` and `pending` dispatches. Status SHALL NOT start or change any dispatch, and SHALL NOT merge.

#### Scenario: Inspect a running triage

- **WHEN** a triage session is running and one PR-READY check dispatch waits for the cap
- **THEN** status shows the triage session's event, claim, profile, and elapsed time, and one pending dispatch waiting for the concurrency cap

#### Scenario: Inspect the day's spend

- **WHEN** seven sessions have started today with known costs
- **THEN** status shows seven sessions started today and the sum of their estimated costs, and shows no session budget

#### Scenario: Inspect a dispatch an earlier release skipped for budget

- **WHEN** a `FAILURE` dispatch that an earlier release recorded `budget-exhausted` belongs to a claim not yet observed Done, cancelled, or superseded
- **THEN** status lists that dispatch as `budget-exhausted`

#### Scenario: Inspect a failed dispatch

- **WHEN** a PR-READY check dispatch timed out for a claim that is still in Review
- **THEN** status lists that dispatch as `timed-out` with its pull request and evidence path

#### Scenario: Inspect the issues a check filed

- **WHEN** a PR-READY check completed and filed one factory issue for a claim still in Review
- **THEN** status shows the pull request and the filed issue's URL on one line

#### Scenario: Inspect a merge that waits

- **WHEN** a pull request rated `low` waits for a running check
- **THEN** status shows the pull request, `low`, and that the merge waits for checks

#### Scenario: Inspect a pull request left for review

- **WHEN** a pull request was rated `medium` for a claim still in Review
- **THEN** status shows the pull request, `medium`, and not merged because of the rating

### Requirement: Redispatch a watch event

`agent-factory watch redispatch <dispatch>` SHALL queue a new `pending` attempt for the same event as the named dispatch, under a new attempt key. It SHALL accept only a `PR-READY` or `FAILURE` dispatch whose state is `completed`, `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted`. It SHALL refuse any other dispatch, naming its state, and change nothing. The new attempt SHALL be processed like any `pending` dispatch, under the concurrency cap, the watch readiness checks, and one session per pull request at a time. The command SHALL print the new dispatch's id. When watching is disabled, it SHALL say that the attempt waits until watching is enabled. The command SHALL NOT start the session itself.

#### Scenario: Redispatch an interrupted review

- **WHEN** the operator redispatches an `interrupted` `PR-READY` dispatch
- **THEN** a new pending attempt is queued, and the next cycle starts one PR-READY check session for that pull request

#### Scenario: Refuse a running dispatch

- **WHEN** the operator redispatches a `launched` dispatch
- **THEN** the command names the `launched` state, queues nothing, and exits with an error

#### Scenario: Redispatch an event skipped for budget

- **WHEN** the operator redispatches a `FAILURE` dispatch that an earlier release recorded `budget-exhausted`
- **THEN** a new pending attempt is queued, and a cycle starts one triage session for it once the concurrency cap and readiness checks allow

### Requirement: Document the service-driven watcher

The operations documentation SHALL describe service-driven watching as the normal mode, and SHALL state that the watcher's job is to make sure the factory itself works, and, when auto-merge is on, to merge the factory's low-risk pull requests:

- the two events and what each one does;
- the `[watch]` settings and their defaults, and how to escalate a failure to a stronger model;
- the concurrency behavior; that no event is skipped because of session volume, which the factory job cap bounds instead; and that `budget-exhausted` dispatches from earlier releases remain in history and can be redispatched;
- the actions a dispatched session may and may not take, including that it files issues for factory defects and never fixes or merges anything;
- auto-merge: the risk bars for fixes, tasks, and features, the gates the factory checks before it merges, the risk-verdict comment on the pull request, the 60-minute wait, and how to turn it off;
- that auto-merge requires a GitHub ruleset requiring status checks on each fix target's base branch, that a repository without one never auto-merges, and that branch protection requiring an approving review blocks auto-merge in that repository;
- that triage runs after a failed claim's automatic retry and does not hold it;
- the watch doctor group, including the `gh` login that files issues;
- the watch section of status;
- `watch redispatch`;
- how to find a dispatch's evidence and usage.

They SHALL state that no interactive watcher session is used. An on-demand `factory-status` skill SHALL report the factory's state once when asked, without watching or polling, including pull requests the factory merged automatically. A `factory-triage` skill SHALL hold the failure-handling procedure and both headless procedures that the watch workflow's sessions follow: the PR-READY check, including the risk rating, and the failure triage. Its standing rules SHALL allow the resident's auto-merge while still forbidding an agent session to merge. The factory PR review skill and its reviewer agent SHALL have no headless mode; they review a pull request only when the operator asks.

#### Scenario: Operate the service watcher

- **WHEN** an operator follows the documentation to enable watching
- **THEN** they can set the profile and concurrency cap, pass the watch doctor group, find running and failed dispatches and the issues they filed in status, and redispatch a failed one

#### Scenario: Turn off auto-merge

- **WHEN** an operator follows the documentation to stop auto-merging
- **THEN** they set `[watch] auto_merge = false` in committed configuration; sessions launched after the change do not rate risk, no pull request is merged after the change, and a rating from a session already running gets a risk-verdict comment saying it was not merged because auto-merge is off

#### Scenario: Enable auto-merge for a repository

- **WHEN** an operator follows the documentation to let a fix target auto-merge
- **THEN** they add a ruleset on its base branch that requires its CI status checks, and the factory then merges `low`-rated pull requests there once those checks pass

#### Scenario: Ask for a factory update

- **WHEN** the operator asks an agent for a factory update
- **THEN** the agent follows `factory-status`, reports once what waits on the operator, what is running, what failed, and what merged automatically, and starts no watcher

#### Scenario: Ask for a PR review

- **WHEN** the operator asks an agent to review a factory pull request
- **THEN** the agent follows `factory-pr-review` interactively; no watch session reviews it for the operator

### Requirement: Document the task kind

The operator documentation (`AGENTS.md` and `docs/operations.md`) SHALL describe the task kind:

- the native Task type, and that a Task reaches the factory only by moving it to Ready;
- the `[task]` shared and local settings and their defaults, and the task-host doctor group;
- what triage declines and the boundary between maintenance work and release configuration;
- the `chore:` commit and pull request convention;
- that review rounds on a task pull request stop on out-of-scope requests;
- that rolling back to a release without the task kind leaves open task claims unhandled, so they should be settled or cancelled first.

#### Scenario: Learn how to hand a chore to the factory

- **WHEN** an operator reads the documentation to queue maintenance work
- **THEN** it tells them to file a Task in a fix target, move it to Ready or use the assign skill, and what kinds of work triage will decline

### Requirement: Assign and report tasks through the operator skills

The repository's `factory-assign` skill SHALL accept `--apply task`. It SHALL then check, and where possible set, what task admission needs: an open issue in a fix target, native type Task, an author with write access, no `needs-input` label, `Owner=factory`, `Status=Ready`, and a default Priority. It SHALL then run one tick and confirm that the factory claimed the issue as a task. It SHALL refuse `--apply task` for an issue whose type is Bug or Feature, and SHALL refuse `--apply fix` and `--apply feature` for a Task. The `factory-status` skill SHALL report the task slot and task claims beside the other kinds, including blocked tasks and task pull requests waiting for review.

#### Scenario: Assign a Task

- **WHEN** an operator runs the assign skill with `--apply task` on a writer's open Task in a fix target
- **THEN** the card gets `Owner=factory`, `Status=Ready`, and a default Priority, one tick runs, and the skill reports the task claim

#### Scenario: Apply the wrong kind

- **WHEN** an operator runs the assign skill with `--apply fix` on a Task
- **THEN** the skill refuses and names the task kind

### Requirement: Govern post-run audits with one switch

One factory audit switch SHALL govern every post-run development audit the factory starts or settles. This covers the host attempt audit, the resident's settlement of an attempt's audit outcome (including delivery of an eval's collected reports), the post-run audit lines in `status`, and the post-run audit check in `doctor`. The switch SHALL be off. That is a temporary disable pending a decision on whether audits are worth their cost (Codagent-AI/agent-factory#60). Turning the switch back on SHALL restore the factory-owned audit behavior that applied before it was turned off, unchanged.

The switch governs only what the factory itself starts or settles. Audits that Agent Runner starts by its own automatic hook are outside it, including the audits inside an eval's sandbox or a Fly guest whose reports the resident delivers. Agent Runner turns that hook off separately (Codagent-AI/agent-runner#191). So:

- An attempt that runs a Runner revision which still has the hook, such as a claim admitted with an older pinned Runner, may still produce Runner-side audit reports while the switch is off. The resident SHALL NOT deliver or report them.
- Restoring eval audits requires both the switch and the Runner's automatic hook to be on.

While the switch is off:

- A host fix, feature, task, or review attempt SHALL run no post-run audit replay, and its outcome SHALL NOT depend on audits.
- When the resident consumes an attempt's result, it SHALL record no post-run audit outcome for that attempt, deliver no collected eval reports to the development-audit destination, and post no `post-run-audit` issue event.
- `status` SHALL NOT list an attempt as missing its post-run audit only because it has no recorded audit outcome. It SHALL still list a recorded undelivered outcome from within the last seven days.
- The `doctor` post-run audit check SHALL pass and state that post-run audits are disabled. It SHALL NOT probe the installed Agent Runner for audit support or require the development-audit reporting connection. So neither a missing connection nor a Runner built without development audits fails `doctor` or blocks admission.

Agent Runner's own execution log in an attempt's session evidence is not a post-run audit outcome, and this requirement SHALL NOT remove it.

#### Scenario: A host attempt finishes with audits off

- **WHEN** the switch is off and a host fix, feature, task, or review attempt's workflow ends, whether it succeeded or failed
- **THEN** no `agent-runner audit replay` runs for the attempt, the attempt's exit status is the workflow's own, and its evidence holds no post-run audit outcome

#### Scenario: The resident consumes an attempt with audits off

- **WHEN** the switch is off and the resident consumes the result of any attempt, including one whose Agent Runner metrics were recorded and an eval whose sandbox collected reports
- **THEN** no `post-run-audit` issue event is posted, no audit outcome is recorded, no reports are delivered to the development-audit destination, and the rest of result consumption and reporting proceeds as before

#### Scenario: Status after a run with audits off

- **WHEN** the switch is off and a host attempt finished within the last seven days with Agent Runner metrics but no recorded audit outcome
- **THEN** `status` shows no post-run audit line for that attempt

#### Scenario: Status keeps earlier recorded outcomes

- **WHEN** the switch is off and an attempt that finished within the last seven days recorded an undelivered audit outcome before the switch was turned off
- **THEN** `status` still lists that attempt's post-run audit outcome and reason

#### Scenario: Doctor without a reporting connection

- **WHEN** the switch is off and the Mac has no development-audit reporting connection, or the installed Agent Runner lacks development audits
- **THEN** `doctor` reports the post-run audit check as passing, with a detail saying post-run audits are disabled, and admission is not held because of audits

#### Scenario: Re-enabling audits

- **WHEN** the switch is turned on
- **THEN** host attempts replay their audit, the resident settles and reports undelivered audits as `post-run-audit` events, `status` lists missing and undelivered audits, and `doctor` checks Runner audit support and the reporting connection, exactly as before the switch was turned off
- **AND** the resident again delivers an eval's reports only when the attempt's Agent Runner collected them, which requires the Runner's automatic audit hook to be on

### Requirement: Locate the and-scene fixture checkout

The local configuration SHALL accept an optional `[repositories] and_scene` path to the operator's and-scene checkout. When the key is unset, the checkout SHALL be the `and-scene` directory next to the configured `agent_runner` checkout. The public example configuration SHALL show the key without a personal path.

Only eval requests that supply `fixture_ref` use the checkout. A deployment SHALL NOT need one to load configuration, pass `doctor`, or admit evals that do not select a fixture.

`doctor` SHALL report the checkout in the eval group as informational:

- whether it exists and is a Git repository;
- whether its origin is the and-scene suite's fixture repository.

A missing checkout or a mismatched origin SHALL NOT fail `doctor` or hold admission. The report SHALL say that requests supplying `fixture_ref` will wait for revision readiness until the problem is fixed. `doctor` SHALL NOT fetch the checkout or print credentials embedded in its origin.

#### Scenario: Use the sibling default

- **WHEN** the local configuration sets `agent_runner = "/Users/paul/codagent/agent-runner"` and leaves `and_scene` unset
- **THEN** the factory resolves requested fixture refs through `/Users/paul/codagent/and-scene`

#### Scenario: Use an explicit path

- **WHEN** the local configuration sets `and_scene` to a path
- **THEN** the factory uses that path and ignores the sibling default

#### Scenario: Run without an and-scene checkout

- **WHEN** `and_scene` is unset and no sibling checkout exists
- **THEN** configuration loads, `doctor` reports the missing checkout as informational without failing, and evals that do not supply `fixture_ref` are admitted as before this change

#### Scenario: Diagnose a checkout with the wrong origin

- **WHEN** the and-scene checkout's origin is not the suite's fixture repository
- **THEN** `doctor` reports it as informational, names the checkout and the normalized origin without credentials, and states that fixture-selecting requests will wait until the origin is corrected

### Requirement: Refuse a deploy that would drop frozen fixture revisions

`scripts/deploy.sh` SHALL NOT make live a release that cannot honor claims' frozen fixture revisions while any eval claim that recorded a fixture revision is unfinished. A release cannot honor them when it would launch the claim's next attempt without passing the frozen fixture. An unfinished claim is one that can still launch or recover an attempt. Making a release live means pointing the LaunchAgent or `shared_config` at it, or moving `releases/current` to it.

The deploy SHALL check before it pauses the factory. On refusal at that point it SHALL stop with a failing exit status and deploy nothing, leave the factory's pause state unchanged, and name each affected claim by claim identity and issue. Two preparatory steps that precede every deploy are the only exceptions to "deploy nothing": the Agent Runner checkout fast-forward, and building the target release's immutable worktree. Neither changes what the service runs.

The deploy SHALL check again after pausing and before it changes the LaunchAgent, `shared_config`, or `releases/current`, so that a claim admitted after the first check is also covered. On refusal at that point it SHALL stop, name each affected claim, leave the live release unchanged, and leave the factory paused, as other post-pause deploy failures do.

The refusal message SHALL give the rollback procedure:

1. pause the factory;
2. let each named claim settle, or cancel it;
3. deploy the older release.

The message SHALL also state that the older release cannot accept `fixture_ref`. A pinned evaluation that is still needed requires staying on a fixture-capable release. A new request without the key evaluates only the agent-evals default fixture.

The release's own executable SHALL answer whether the target release honors fixture revisions, through a read-only `agent-factory honored-revisions` command. It prints the frozen revision keys the release acts on and needs no configuration. A target release that lacks the command, or does not list `fixture`, does not honor them.

The live release's executable SHALL list the unfinished fixture-pinned claims, through a read-only `agent-factory --config <local> pinned-claims --revision fixture` command. It prints each such eval claim's identity and issue, and nothing when there are none. Handling depends on what the live release supports:

- If the live release does not honor fixture revisions itself, it cannot have admitted such a claim. The deploy SHALL warn and proceed.
- If the live release honors fixture revisions but cannot list the claims, the deploy SHALL stop as for a refusal at that stage.

The deploy SHALL offer no option that bypasses the check. A deploy of a release that honors fixture revisions, or a deploy while no unfinished claim recorded a fixture revision, SHALL be unaffected.

#### Scenario: Roll back while a fixture-pinned claim is unfinished

- **WHEN** the operator deploys a release that cannot honor fixture revisions while an eval claim with a fixture revision has unstarted or recoverable repetitions
- **THEN** the deploy stops before pausing the factory, names that claim and its issue, gives the rollback procedure, exits with failure, and leaves the live release, its plist, `shared_config`, and `releases/current` unchanged

#### Scenario: Claim admitted during the deploy

- **WHEN** a fixture-pinned claim is admitted after the deploy's first check and before the factory is paused, and the target release cannot honor fixture revisions
- **THEN** the second check, after the pause, refuses the deploy, names the claim, and leaves the live release unchanged with the factory paused

#### Scenario: Roll back after fixture-pinned claims finish

- **WHEN** the operator deploys a release that cannot honor fixture revisions and every claim that recorded a fixture revision has settled or been cancelled
- **THEN** the deploy proceeds as before this change

#### Scenario: Live release predates fixture revisions

- **WHEN** the operator deploys, with a deploy script that has this check, a target release that cannot honor fixture revisions while the live release also predates them
- **THEN** the deploy warns that the live release cannot have admitted fixture-pinned claims and proceeds as before this change

#### Scenario: Live release cannot list its fixture-pinned claims

- **WHEN** the live release honors fixture revisions, the target release does not, and listing the live release's fixture-pinned claims fails
- **THEN** the deploy stops at that stage without making the target release live, and states that the claims could not be listed

#### Scenario: List fixture-pinned claims

- **WHEN** the operator runs `agent-factory --config <local> pinned-claims --revision fixture` while one eval claim with a fixture revision is waiting and another has settled
- **THEN** the command prints only the waiting claim's identity and issue and changes nothing

#### Scenario: Deploy a release that honors fixture revisions

- **WHEN** the operator deploys a release that honors fixture revisions while fixture-pinned claims are unfinished
- **THEN** the deploy proceeds as before this change, and those claims keep their frozen fixture revisions

### Requirement: Document request-selected fixture revisions

The operations documentation and `AGENTS.md` SHALL explain:

- the `fixture_ref` eval-request key and that omitting it keeps the agent-evals pin;
- that the factory resolves it at admission through the and-scene checkout and its sibling default, and freezes the commit for the claim;
- that the commit must be published on the and-scene origin, and that deleting its branch before the claim finishes fails the remaining repetitions at fixture checkout;
- how reports and the `Refs` field show a pinned fixture;
- that results from a non-default fixture are not comparable with results from the agent-evals pin;
- the deploy's refusal to roll back past fixture support, and the rollback procedure: settle or cancel every unfinished fixture-pinned claim before rolling back. The older release cannot accept `fixture_ref`, so a pinned evaluation that is still needed means staying on a fixture-capable release, while a new request without the key evaluates only the default fixture;
- that a rollback done by hand, or with a deploy script without this check, bypasses the refusal.

#### Scenario: Request an eval against a fixture branch

- **WHEN** an operator reads the documentation to evaluate an unmerged and-scene fixture change
- **THEN** it tells them to push the fixture commit to a branch on the and-scene origin, add `fixture_ref` to the eval block, and how to confirm in the frozen inputs and `Refs` field which fixture was used

#### Scenario: Roll back with fixture-pinned claims

- **WHEN** an operator reads the documentation before rolling back to an older release
- **THEN** it tells them how to find unfinished fixture-pinned claims, to let them settle or cancel them before rolling back, and that the deploy refuses the rollback otherwise
- **AND** it states that the older release cannot accept `fixture_ref`, so a pinned evaluation still needed requires staying on a fixture-capable release

### Requirement: Configure session notifications

The shared configuration SHALL accept an optional `[notify]` section with these settings:

- `enabled` (default false);
- the delivery `agent` profile in `cli:model:effort` form, whose CLI SHALL be `claude`, required when notifications are enabled;
- `settle_seconds`, the settle period (default 360, zero or more);
- `watch_wait_minutes`, the watch wait (default 25, zero or more);
- `daily_sessions`, the per-day delivery cap (default 30, zero or more);
- `timeout_minutes`, the delivery timeout (default 5, at least 1).

When notifications are enabled, configuration loading SHALL fail on a missing profile, a profile that is not in `cli:model:effort` form, a CLI other than `claude`, or a value out of range, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior. The Codagent shared configuration SHALL enable notifications with the profile `claude:claude-haiku-4-5-20251001:low`. Each cycle SHALL read the notify settings from the configuration it loads, so a change applies to stops detected after it. A delivery session that is already running SHALL keep its profile and timeout.

#### Scenario: Reject a non-Claude profile

- **WHEN** notifications are enabled with the agent `codex:gpt-5:low`
- **THEN** configuration loading fails and names the `[notify]` agent setting

#### Scenario: Leave notifications unconfigured

- **WHEN** the shared configuration has no `[notify]` section
- **THEN** no stop is detected, no delivery session starts, and status shows notifications as disabled

### Requirement: Diagnose notification readiness

When notifications are enabled, `agent-factory doctor` SHALL run a `notify` group. The group SHALL verify:

- that the `claude` CLI and `ps` are executable on the service PATH;
- that the notify profile is a valid `claude` profile and the Claude CLI is authenticated;
- that the Claude CLI supports the options the notifier needs (`--tools`, `--allowedTools`, `--json-schema`, and `--strict-mcp-config`);
- that the local Claude session registry directory exists and is readable.

It SHALL report an empty registry as informational, not as a failure. A failing notify group SHALL affect only the start of delivery sessions: a stop detected while it fails SHALL start no session and SHALL be recorded `failed` with the doctor reason. Detection, the watcher, and every kind's admission SHALL continue. Doctor SHALL NOT read or print registry key files or tokens.

#### Scenario: Claude is not authenticated

- **WHEN** notifications are enabled and the Claude CLI on the service PATH is not logged in
- **THEN** doctor reports the notify group as failing and names the authentication problem, and the other groups report independently

### Requirement: Report session notifications in status

When notifications are enabled, `agent-factory status` SHALL show a notify section with:

- whether notifications are enabled;
- each running delivery session with its issue, stop kind, and elapsed time;
- the number of Claude notifier sessions launched today against the cap, and today's known estimated cost;
- the recent notification records (from the last 24 hours), each with its issue, stop kind, outcome, the target session's recorded name, and any `watch event missed` or `watch dispatch waiting` note.

When notifications are disabled, status SHALL show one line saying so and SHALL still list running delivery sessions. Status SHALL NOT start or change any delivery.

#### Scenario: Inspect today's notifications

- **WHEN** three stops were notified today, two `sent` and one `no-session`
- **THEN** status lists the three with their issues, stop kinds, and outcomes, and shows 2 sessions against the daily cap, because the `no-session` stop launched none

### Requirement: Document session notifications

`AGENTS.md` and `docs/operations.md` SHALL describe session notifications:

- that `codagent-github-project` records the creating session and `factory-assign` replaces it, and that one session is tracked per issue;
- the stop kinds, the settle period, and the wait for the service watcher;
- that a message only notifies and carries the issue, kind, what happened, and links;
- that delivery is best effort: an ended, renamed-and-reused, or unresolvable session gets nothing; a session in another permission mode may hold the message; the name race is a known limitation;
- the `[notify]` settings and defaults, the notify doctor group, and the notify section of status.

The `codagent-github-project` and `factory-assign` skills SHALL state that they record the session. The `factory-watch` skill SHALL state that marked issues notify their recording session on their own, so `factory-watch` is needed only for issues without a marker or to follow issues from another session.

#### Scenario: Decide whether to start factory-watch

- **WHEN** an agent that just assigned an issue with `factory-assign` reads the `factory-watch` skill
- **THEN** it learns that its session will be notified when the factory stops progressing on that issue, and that it need not start a watch for it

### Requirement: Cap attempts started across the factory

The shared configuration SHALL accept an optional `[job_cap]` section with these settings:

- `attempts`: the most attempts that may start in the window, across all work kinds. It defaults
  to 100 and SHALL be an integer of at least 1.
- `window_hours`: the length of the rolling window. It defaults to 24 and SHALL be an integer of
  at least 1.

The cap SHALL always apply. When the section is missing, the defaults apply. Configuration loading
SHALL fail on a value that is not an integer or is out of range, and the failure SHALL name the
setting. Each cycle SHALL read the cap from the configuration it loads. The Codagent example
configuration SHALL set the section explicitly.

The factory SHALL count every attempt it reserves whose reservation time is within the last
`window_hours` hours and not earlier than the latest job-cap reset:

- an initial attempt;
- a retry;
- a recovery;
- an unblock;
- a review round;
- an eval repetition.

Watch sessions, merge syncs, and post-run audits SHALL NOT count. The cap is reached while the
count is at or above `attempts`.

While the cap is reached, the factory SHALL NOT reserve any new attempt of any kind:

- it SHALL NOT claim a new Ready card;
- it SHALL NOT start a retry, recovery, unblock, review round, or eval repetition.

The check and the reservation SHALL be one atomic step for every reservation path. So concurrent
cycles, a `tick` overlapping the resident, or several paths in one cycle cannot together start
more attempts than the cap allows.

An attempt held by the cap SHALL NOT consume an execution or recovery retry. Its claim SHALL keep
its lifecycle, frozen inputs, completed work, and card status. Attempts already running SHALL
continue under their own limits. The hold SHALL clear without operator action once enough counted
attempts leave the window for the count to fall below `attempts`. Held work then starts in a later
cycle under the normal admission controls.

The earliest clear time is when the (N − `attempts` + 1)th oldest counted attempt leaves the
window, where N is the count. This assumes no reset and no configuration change. `pause` and
`resume` SHALL NOT change the count or clear the hold. The resident SHALL log one line when it
finds the cap reached, and one when it finds the cap clear again.

#### Scenario: Reach the cap

- **WHEN** the cap is 100 attempts per 24 hours, 100 attempts have started in the last 24 hours, and a fix claim's automatic retry is due
- **THEN** no attempt is reserved, the claim stays active with its retry unconsumed, and a running eval repetition continues

#### Scenario: Every reservation path is held

- **WHEN** the cap is reached while a blocked claim has a writer's answer, a claim in Review has eligible review comments, and an eval claim has a repetition left
- **THEN** no unblock, review round, or repetition starts, and each claim keeps its lifecycle and card status

#### Scenario: The cap clears with the window

- **WHEN** the cap is reached and the oldest counted attempt leaves the 24-hour window
- **THEN** the count falls below the cap, and a later cycle starts held work under the normal admission controls

#### Scenario: Concurrent reservations at the edge

- **WHEN** 99 of 100 attempts have started in the window, and the resident's cycle and an operator's `tick` each try to reserve an attempt at the same time
- **THEN** exactly one attempt is reserved

#### Scenario: Work that does not count

- **WHEN** a watch session, a merge sync, and a post-run audit run in the window
- **THEN** the job cap count does not change

#### Scenario: A lowered cap

- **WHEN** 120 attempts count, and a configuration change lowers `attempts` from 150 to 100
- **THEN** the cap is reached, and its earliest clear time is when the 21st-oldest counted attempt leaves the window

#### Scenario: A raised cap

- **WHEN** the cap is reached at 100, and a configuration change raises `attempts` to 150
- **THEN** the next cycle finds the cap clear and can start held work

#### Scenario: Resume does not clear the cap

- **WHEN** the factory is paused while the cap is reached and the operator resumes it
- **THEN** the pause clears, and no attempt starts until the cap clears

#### Scenario: Reject an invalid cap

- **WHEN** the shared configuration sets `[job_cap] attempts = 0`
- **THEN** configuration loading fails and names the `job_cap.attempts` setting

#### Scenario: Leave the cap unconfigured

- **WHEN** the shared configuration has no `[job_cap]` section
- **THEN** the factory caps attempts at 100 per 24 hours

### Requirement: Notify when the job cap holds work

A cap episode SHALL begin when the factory finds the job cap reached while no episode is open. It
SHALL end when the factory finds the count below the cap. The open episode SHALL survive restarts.
In each episode, the factory SHALL post at most one factory-bot comment:

- on the issue of each claim that has an attempt held by the cap. This includes a Ready card
  whose existing claim admission would reuse, for example a fix awaiting its retry or an eval
  awaiting its next repetition. Such a card is a held claim, not an unclaimed card;
- on the issue of each unclaimed Ready card that the factory would admit now except for the cap.
  This applies only when all of these hold:
  - the factory is not paused;
  - the card's kind's window is open;
  - its slot is free;
  - the kind's readiness checks pass;
  - the request's revisions resolve;
  - the request is valid for admission;
  - no provider quota hold applies to the roles it would freeze.

  A card that fails one of these checks gets the notice it gets today, such as revision
  readiness or invalid-request feedback, or no notice. It does not get the job-cap comment.
  Checking a card SHALL NOT create, change, or supersede a claim.

Each comment SHALL state that the factory job cap is reached. It SHALL give:

- the cap and the window;
- the number of attempts counted when the comment was posted;
- the earliest clear time at that moment, noting that `status` shows the current value;
- the reset command.

Comments SHALL be delivered at most once per issue in each episode, across restarts and retries,
and SHALL be retried until delivered. An unclaimed card SHALL stay Ready and unclaimed, and the
factory SHALL record which cards it notified so that status can list them. A new episode SHALL
post new comments.

#### Scenario: A held retry is announced once

- **WHEN** the cap holds a fix claim's retry over several cycles and a resident restart
- **THEN** exactly one job-cap comment is posted on that claim's issue for the episode

#### Scenario: A new request arrives while the cap is reached

- **WHEN** the cap is reached, the factory is not paused, and a Feature card moves to Ready while the feature window is open, its slot is free, and its readiness checks pass
- **THEN** the card is not claimed, stays Ready, receives one job-cap comment, and status lists it as waiting for the job cap

#### Scenario: A card waiting for a busy slot

- **WHEN** the cap is reached and a Ready fix card waits while the fix slot is occupied
- **THEN** no job-cap comment is posted on it

#### Scenario: A previously claimed card awaiting a retry

- **WHEN** the cap is reached and a Ready fix card's existing claim has an automatic retry due, with no attempt running
- **THEN** the retry does not start, the claim's issue receives one job-cap comment, and status shows the claim held by the job cap and does not list the card among unclaimed waiting cards

#### Scenario: A new request whose revisions do not resolve

- **WHEN** the cap is reached and a new Ready card names a revision that cannot be resolved
- **THEN** no claim is created, the issue receives the existing revision-readiness comment, and no job-cap comment is posted on it

#### Scenario: A new request held by a provider quota

- **WHEN** the cap is reached and a new Ready card's roles would use a provider under an active quota hold
- **THEN** no claim is created and no job-cap comment is posted on it

#### Scenario: A later episode

- **WHEN** an episode ends because the count falls below the cap, and the cap is reached again the next day while the same claim's review round is held
- **THEN** that claim's issue receives one new job-cap comment for the new episode

### Requirement: Reset the job cap

`agent-factory job-cap reset` SHALL record the current time as the job-cap reset time in the
store. Attempts reserved before that time SHALL no longer count toward the cap. The reset SHALL
survive restarts. The command SHALL print the reset time and the count after the reset. When the
cap was reached, it SHALL also say that held work can start in the next cycle. The command SHALL
NOT start work itself, SHALL NOT clear a pause or other holds, and SHALL NOT change running
attempts. It SHALL be accepted whether or not the cap is reached.

#### Scenario: Reset a reached cap

- **WHEN** the cap is reached with a held unblock and the operator runs `agent-factory job-cap reset`
- **THEN** the command reports a count of zero, and the next cycle can start the unblock under the normal admission controls

#### Scenario: Reset while paused

- **WHEN** the factory is paused and the cap is reached, and the operator resets the cap
- **THEN** the count is reset, the factory stays paused, and no attempt starts until resume

### Requirement: Document the factory job cap

The operations documentation SHALL describe:

- the `[job_cap]` settings and their defaults;
- which attempts count and which work does not;
- what a reached cap holds and what continues;
- how the cap clears;
- the claim and card comments;
- the job cap in status;
- `agent-factory job-cap reset`;
- that raising `attempts` needs a committed configuration change.

The `AGENTS.md` "Service-driven watcher" section SHALL state that every PR-READY and FAILURE
event gets a session, limited only by the concurrency settings. It SHALL state that the watch
status shows no session budget, and it SHALL point to the factory job cap as the volume bound.
The on-demand `factory-status` skill SHALL report a job cap that is reached or near its limit,
instead of a watch session budget.

#### Scenario: Relieve a reached cap

- **WHEN** an operator sees a job-cap comment on an issue and follows the documentation
- **THEN** they can find the count and earliest clear time in status, and either wait, reset the cap, or raise it through a configuration change

#### Scenario: Ask for a factory update while the cap is reached

- **WHEN** the operator asks an agent for a factory update while the cap is reached
- **THEN** the agent following `factory-status` reports that the job cap holds work, when it clears, and the reset command

### Requirement: Restore the per-kind guard before rolling back past lanes

`scripts/deploy.sh` SHALL NOT make a release live that does not support Priority lanes while any work kind has more than one unfinished attempt. Making a release live means pointing the LaunchAgent or `shared_config` at it, or moving `releases/current` to it. Before such a release is made live, the deploy SHALL restore the database's guard of one unfinished attempt per kind, on which a release without lanes relies for atomic reservation.

The target release's own executable SHALL answer whether it supports lanes, through a read-only `agent-factory lanes supported` command that prints `priority-lanes` and needs no configuration. A target release that lacks the command, or does not print `priority-lanes`, does not support them. The live release's executable SHALL provide `agent-factory --config <local> lanes downgrade`, which in one atomic step verifies that no kind has more than one unfinished attempt and restores the per-kind guard. With `--check` it SHALL only verify, and change nothing. When the verification fails, the command SHALL change nothing, exit with failure, and name each kind with more than one unfinished attempt, with its claims and issues. It SHALL also provide `agent-factory --config <local> lanes enable`, which re-establishes lane enforcement.

The deploy SHALL check before it pauses the factory. On refusal at that point it SHALL stop with a failing exit status, deploy nothing, and leave the factory's pause state unchanged. It SHALL name each affected kind with its claims and issues. The Agent Runner checkout fast-forward and building the target release's immutable worktree are the only steps that may still have run. The deploy SHALL restore the guard after pausing and before it changes the LaunchAgent, `shared_config`, or `releases/current`. If restoring fails at that point, it SHALL stop, name the affected claims, leave the live release unchanged, and leave the factory paused, as other post-pause deploy failures do.

The refusal message SHALL give the rollback procedure:

1. pause the factory;
2. let attempts settle, or cancel claims, until each kind has at most one unfinished attempt;
3. deploy the older release.

A release that supports lanes SHALL establish lane enforcement when its resident starts, and when `lanes enable` runs. No other command, and no supervisor, SHALL switch between lane enforcement and the per-kind guard. If the deploy stops after restoring the guard and before it has confirmed that the live lanes release's resident is gone, it SHALL point the LaunchAgent and `shared_config` back at the live release and run `lanes enable` with it before it exits. That covers a failed `doctor` and a resident that did not unload. Once the resident's removal is confirmed, a later failure SHALL keep the per-kind guard, for the older release. While the per-kind guard is in place, a release that supports lanes SHALL start at most one attempt per kind, and `status` SHALL show that lanes are off and that the resident re-enables them when it starts. If the live release does not support lanes, the deploy SHALL proceed as before this change. A deploy of a release that supports lanes SHALL be unaffected. The deploy SHALL offer no option that bypasses the check.

#### Scenario: Roll back while two lanes of a kind are busy

- **WHEN** the operator deploys a release without lanes while a Low and a High fix attempt are both unfinished
- **THEN** the deploy stops before pausing the factory, names the fix kind with both claims and their issues, gives the rollback procedure, exits with failure, and leaves the live release, its plist, `shared_config`, and `releases/current` unchanged

#### Scenario: Roll back with at most one attempt per kind

- **WHEN** the operator deploys a release without lanes while one fix attempt and one eval attempt are unfinished and no kind has more than one
- **THEN** the deploy pauses, restores the per-kind guard, makes the older release live, and the older release reserves attempts atomically one per kind

#### Scenario: A second lane starts during the deploy

- **WHEN** a second fix attempt starts after the deploy's first check and before the factory is paused, and the target release has no lanes
- **THEN** restoring the guard after the pause fails, the deploy names the claims, and it leaves the live release unchanged with the factory paused

#### Scenario: Deploy to a release with lanes

- **WHEN** the operator deploys a release that supports lanes while several lanes of a kind are busy
- **THEN** the deploy proceeds as before this change, and the running attempts keep their release and lanes

#### Scenario: A failed rollback keeps the lanes release

- **WHEN** a deploy restored the per-kind guard, then failed `doctor` and pointed the service back at the release with lanes
- **THEN** the deploy runs `lanes enable` with that release before exiting, and lane admission resumes once the factory is resumed

#### Scenario: Lanes stay off after an interrupted rollback

- **WHEN** a deploy restored the per-kind guard and was killed before it could re-enable lanes, leaving the lanes release live
- **THEN** that release starts at most one attempt per kind, status shows that lanes are off, and lanes return when its resident next starts or `lanes enable` runs

#### Scenario: The live resident does not unload during a rollback

- **WHEN** a deploy restored the per-kind guard for a rollback and the live lanes resident is still running after the unload wait
- **THEN** the deploy points the LaunchAgent and `shared_config` back at the live release, runs `lanes enable` with it, and stops with the factory paused

#### Scenario: The older resident fails to start after unload

- **WHEN** a rollback deploy confirmed the lanes resident was gone and the older release's resident then failed to start
- **THEN** the deploy keeps the per-kind guard and stops with the factory paused, and does not run `lanes enable`

### Requirement: Document Priority lanes

The operations documentation, `AGENTS.md`, and the repository's `factory-status`, `factory-assign`, and `factory-watch` skills SHALL describe Priority lanes. They SHALL explain:

- that each work kind runs at most one attempt per Priority level, Urgent, High, Medium, and Low, and that unprioritized work uses the Low lane;
- that a higher-priority start never waits for lower-priority work, and that new lower-priority work of the same kind does not start while higher-priority work runs;
- that running work is never preempted or moved by a Priority change;
- that a claim already under way continues its repetitions and retries in its lane, while admissions, resumes, and review rounds are new starts;
- how status shows each kind's lanes and why work waits;
- that attempts reserved before the upgrade hold their whole kind until they finish;
- the disk and memory the machine needs when several lanes of the host kinds run together, and that the existing disk, memory, quota, readiness, and job-cap holds still bound admission;
- the deploy's refusal to roll back past lanes and its rollback procedure, and that a rollback done by hand, or with a deploy script without this check, bypasses the refusal and the guard restoration.

#### Scenario: Learn why a card waits

- **WHEN** an operator reads the documentation because a Ready Medium bug has not started while a High fix runs
- **THEN** it tells them that the High fix holds lower fix lanes until it finishes, and how status names the holder

#### Scenario: Roll back past lanes

- **WHEN** an operator reads the documentation before rolling back to a release without lanes
- **THEN** it tells them to pause and let attempts settle, or cancel claims, until each kind has at most one unfinished attempt, and that the deploy refuses the rollback otherwise

