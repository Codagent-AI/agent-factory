# factory-operations Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. Feature configuration SHALL include the feature role profiles, feature limits, the feature admission window, and the feature workflow contract; the feature kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new feature work SHALL be enabled only when the feature configuration is present; when it is removed, existing feature claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The feature kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. The eval kind SHALL accept `docker` (the default) or `fly` execution and SHALL NOT accept host execution. Fly configuration SHALL be local and SHALL include the Fly app, region (default `ewr`), Machine CPU kind, CPU count, and memory (default shared, 4 CPUs, 8 GiB), the sandbox image reference, the deploy-token file location alongside the other controller credentials, and the collection grace period. The execution mode, fix disk floor, Fly settings, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration whose eval role defaults are `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`, and `tester = codex:gpt-5.6-luna:medium`, and which enables the feature kind with role defaults matching its fix role defaults. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific and workflow-specific repository and executable locations SHALL be supplied to the relevant handler rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the Eval, Bug, and Feature behavior and the field names, options, and defaults described by the active specifications

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

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval, eval-sandbox, eval-fly, fix-sandbox, fix-host, or feature-host and label each so the operator can see which kind a failure holds; the eval group holds the mode-neutral eval checks that apply under every eval execution mode. It SHALL run only the groups that apply to a kind under its configured execution mode. Docker availability, memory allowance against one reservation, sandbox launcher checks, and reclaimable Docker space SHALL be checked and reported only under kinds configured for Docker execution; when no kind is configured for Docker, doctor SHALL neither probe Docker nor print any Docker line. The eval-fly group SHALL verify that the Fly API is reachable with the configured deploy token, the configured app exists, the configured image's repository (the configured `image` with any tag removed) is the configured app's `registry.fly.io` repository that the per-claim build pushes to, a Claude login is deliverable as defined in `factory-fly-execution` whenever an eval role uses Claude, using the same bounded Keychain read the launcher uses, the deploy-token file is owner-readable and contains only that token, the factory's own Fly launcher is resolvable, and `flyctl` is executable on the service PATH for transport. The factory SHALL resolve its launcher from the service PATH when present and otherwise from the directory holding the running factory, so that a service started without a bespoke PATH entry still finds the launcher shipped with it. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, the packaged fix and review workflows each declare a compatible contract version, and every fix role has a `cli:model:effort` profile. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When the feature kind is configured, it SHALL run the host checks of the fix-host group against the feature roles, verify that every feature role has a `cli:model:effort` profile, that the packaged feature and define workflows declare a compatible contract version, and that the installed Agent Runner provides the `core/verify-change` builtin workflow, and report each fix target without an `openspec/` directory or without an Agent Validator configuration as informational. When a kind is configured for Docker and Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. On macOS, when a login-Keychain item with service `Claude Code-credentials` and account `unknown` exists, doctor SHALL report it as informational only, explaining that it is a stale login created by a process without `USER`; it SHALL NOT fail on it or delete it. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt, create a Machine, build an image, print any credential, or attempt to repair credentials, Keychain items, or configuration.

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

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind:

- the slot holder and progress;
- waiting work and why it waits;
- blocked fix and feature claims;
- settled fix and feature claims with eligible review comments waiting for their kind's slot;
- pending merge syncs and their last failure reason;
- pause state and blocking conditions;
- the next permitted start time, when it can be determined.

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
wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a
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
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but its kind's slot is busy
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

#### Scenario: Inspect a blocked feature

- **WHEN** a feature claim stopped during definition
- **THEN** status shows the feature slot as free and the blocked feature with its stop reason and pushed branch

#### Scenario: Inspect a failed registry image deletion

- **WHEN** a superseded eval claim's registry image deletion failed on the last poll
- **THEN** plain `status` lists that claim with the image tag and the registry's reason until a later poll deletes the image

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation, merge syncs, and pending reporting, and MAY start eligible work in any free slot. It SHALL apply the same admission windows, pause state, prerequisite, memory, and quota holds, and per-kind slot guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work, a free slot for its kind, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside a kind's window, while paused, or while that kind's slot is occupied
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
terminal release below applies. When verified running work is dragged to Done,
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

The change SHALL include the GitHub setup for the Codagent example deployment: an organization-level Codagent Project, native Eval issue type, use of the native Bug issue type, `eval-request`, red `needs-input`, and `factory-hold` labels in each configured source repository, regular Markdown eval issue template, a "Bug (tracking only)" issue template in each configured source repository that sets the Bug type and pre-applies `factory-hold`, shared routing rules and reusable workflow, source-repository caller workflows, required permissions, general issue/PR auto-add, and closure automation. For the fix kind it SHALL additionally provision a fine-grained repository credential with Contents, Pull requests, and Issues access to the target repositories and no workflow, administration, or Project access, preferably on a machine user, and a ruleset on each target repository requiring a pull request for changes to `main` with no bypass for the credential's owner.

The board SHALL have Backlog, Ready, Running, Review, and Done Status columns, horizontal groups by native issue Type, no field-based sorting, and views for the eval queue and active factory work. It SHALL expose configured Owner, Refs, and Verdict fields and visible attention labels. Verdict options SHALL support `pending-human-review`, `failed`, `quota-deferred`, and `infra-error`; the factory SHALL never assign `passed`.

The initial eval request source SHALL be `Codagent-AI/agent-evals`. General work from the configured Codagent repositories SHALL enter Backlog; eval and bug routing SHALL initialize factory ownership and Ready for authors with the required repository access as defined in `factory-routing`, without a general auto-add rule undoing that routing. Other authors' requests SHALL enter Backlog without factory assignment. The initial general-work and bug repository set SHALL cover agent-runner, agent-skills, agent-validator, agent-plugin, and agent-evals, and SHALL be configurable. Issue/PR closure automation SHALL move associated cards to Done.

Routing and the local controller SHALL authenticate with an organization-owned GitHub App with organization Projects read/write, repository Issues read/write, and Contents, Pull requests, and Metadata read access at minimum. Suite and fix PR operations SHALL use their separate credentials. Installation SHALL cover the configured repositories. App private keys and the fix credential SHALL remain outside version control, with local owner-only file access and Actions secret storage for caller workflows.

#### Scenario: Create an eval request in the configured source repository

- **WHEN** a user with write, maintain, or admin access to the source repository creates an issue from the eval template
- **THEN** the configured automation places it in the Project's Ready column with native Eval type and factory ownership without a separate manual handoff
- **AND** general auto-add behavior does not reset the routed request to Backlog

#### Scenario: Add general work

- **WHEN** an ordinary issue or PR is created in a configured source repository without the eval request marker or Bug type
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

### Requirement: Retain evidence for a bounded period

The factory SHALL prune the attempt evidence of terminal claims after configurable retention
periods. The retention period is `[limits] evidence_retention_days`, default 14 days. The
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
directory. It SHALL remove only enumerated evidence paths and SHALL keep any file or
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

- **WHEN** a claim's card was observed Done more than 14 days ago under the default retention and nothing still needs its evidence
- **THEN** the next tick removes its logs, session state, and agent output
- **AND** its outcome or result records, issue input, candidate branches, and PRs remain

#### Scenario: Prune a Done claim released without Review

- **WHEN** a settled claim reached Done without a recorded Review observation, its terminal release completed, and 14 days have passed since its first durable Done observation
- **THEN** the next tick prunes its attempt evidence under the Done path

#### Scenario: Skip a Done claim with a pending sync

- **WHEN** a fix claim's card is Done and its PR merged, but its post-merge sync has not completed
- **THEN** its evidence is not pruned however old the claim is

#### Scenario: Prune a cancelled claim that recorded a PR

- **WHEN** a cancelled fix claim recorded a PR and more than 14 days have passed since it was cancelled
- **THEN** its evidence is pruned whatever its card's status, without waiting on a post-merge sync, which only settled claims receive

#### Scenario: Prune a superseded claim outside Done

- **WHEN** a claim was superseded 15 days ago, its card is in Review for the fresh claim, and its terminal release has completed
- **THEN** the superseded claim's evidence is pruned and the fresh claim's evidence is untouched

#### Scenario: Keep a recently superseded claim whose runs finished long ago

- **WHEN** a claim's last run finished 40 days ago and the claim was superseded 3 days ago
- **THEN** its evidence is kept until 14 days after it was superseded

#### Scenario: Prune a settled claim left in Review

- **WHEN** a settled claim's card has stayed in Review for more than 30 days since it settled, its reporting is delivered, and its terminal release has completed
- **THEN** its evidence is pruned
- **AND** its outcome or result records and issue input remain

#### Scenario: Move a Review card to Done late

- **WHEN** a settled claim's card moves from Review to Done 25 days after it settled
- **THEN** the unreviewed path no longer applies and its evidence is kept until 14 days after that Done observation

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
