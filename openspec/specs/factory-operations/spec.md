# factory-operations Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. The eval kind SHALL accept only sandbox execution. The execution mode, fix disk floor, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

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

#### Scenario: Configure host execution for evals

- **WHEN** the local configuration requests host execution for the eval kind
- **THEN** the factory reports the unsupported setting at startup and in doctor and admits no eval

### Requirement: Apply shared deployment changes through explicit updates

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, fix defaults, and the branch names for the `agent-evals` harness, Agent Runner, Agent Skills, and fix target repositories (each defaulting to `main`). Configuration SHALL NOT pin any of these repositories to a commit; commits are resolved per claim at admission and recorded on the claim. Machine-specific paths, schedule, execution limits, and credential-file locations SHALL be configured separately in local TOML. Secret values SHALL remain outside the versioned deployment configuration.

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

### Requirement: Run as a recoverable per-user Mac service

The change SHALL provide a launchd LaunchAgent configuration and setup instructions that start the factory for the configured Mac user on login and restart the controller if it crashes. Execution SHALL use explicit executable, configuration, and credential paths suitable for the service environment, and the LaunchAgent SHALL supply an explicit PATH that includes every executable host-mode fixes need; the resident controller and the supervisors it launches SHALL resolve executables against that environment. Doctor SHALL report the PATH it resolved executables against and, when the LaunchAgent definition is installed at its documented location, SHALL check that every executable host mode needs also resolves on the PATH that definition carries, naming the definition and the missing executable on failure. Controller restarts SHALL preserve running evaluations as required by `factory-claim-lifecycle`; restarting the controller SHALL NOT itself restart or terminate those evaluations.

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

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval-sandbox, fix-sandbox, or fix-host and label each so the operator can see which kind a failure holds. Docker availability, memory allowance against one reservation, and sandbox launcher checks SHALL be reported under the kinds that use the sandbox; when the fix kind runs on the host they SHALL hold only evals. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, and the packaged fix and review workflows each declare a compatible contract version. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt or attempt to repair credentials or configuration.

Shared diagnostics SHALL remain distinct from checks supplied by each work kind and suite.

#### Scenario: Diagnose an unavailable prerequisite

- **WHEN** the operator runs doctor with Docker stopped, invalid required authentication, or an invalid Project mapping
- **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an attempt

#### Scenario: Diagnose suite readiness

- **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
- **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites

#### Scenario: Diagnose fix readiness

- **WHEN** the fix credential is missing, contains additional variables, or the packaged fix or review workflow lacks a compatible contract
- **THEN** doctor reports the fix-specific problem and shows eval readiness independently

#### Scenario: Diagnose Docker with fixes on the host

- **WHEN** Docker is stopped and the fix kind is configured for host execution
- **THEN** doctor reports Docker as an eval-sandbox problem and reports the fix kind ready when its host checks pass

#### Scenario: Diagnose a missing host executable

- **WHEN** the fix kind is configured for host execution and `jq` is not on the service PATH
- **THEN** doctor reports the missing executable under the fix-host group with the action to take

#### Scenario: Diagnose Runner settings

- **WHEN** the operator's Runner user settings do not select the headless backend and yolo permission mode
- **THEN** doctor reports the fix-host problem and names the required values without changing the settings

#### Scenario: Report reclaimable Docker space

- **WHEN** Docker is running and holds reclaimable images or build cache
- **THEN** doctor prints the reclaimable amount and the trim command and does not run it

#### Scenario: Pass a check

- **WHEN** a check passes
- **THEN** its line shows the result and no repair action

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix claims, settled fix claims with eligible review comments waiting for the slot, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a merge sync, or holding a recorded cleanup failure; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a waiting review round, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

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

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees, or for a fix its per-attempt clones and, for sandbox attempts, run-specific images. It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention requirement. Worktrees and clones SHALL remain available while work is running, waiting, blocked, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

A fix claim cancelled by issue closure SHALL have its clones, run-specific images, and credential copies released on the first poll after its execution has stopped, whatever its card's status, since a cancelled card may never pass through Review; its evidence SHALL remain subject to the retention requirement.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees, clones, and images and SHALL NOT remove shared source checkouts, the operator's working clones, or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and any run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, blocked, or in Review, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution, recovery, and human judging

#### Scenario: Release a cancelled fix claim

- **WHEN** a fix claim's issue is closed while an attempt is running and the attempt has since stopped
- **THEN** the next poll removes its clones, run-specific images, and credential copies without waiting for Review or Done
- **AND** its evidence remains until the retention rule removes it
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's worktrees fails or the controller restarts partway through cleanup
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

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, mirrors and working clones, Docker startup and memory allowance, the fix execution mode and what host mode keeps and gives up, model authentication, GitHub routing and board permissions, suite prerequisites, the packaged fix workflow and its contract version, explicit service paths including the LaunchAgent PATH, login behavior, preventing idle sleep, and the evidence retention period. Documentation SHALL state plainly that in host mode, on a machine where the operator's own GitHub login is available, the separate fix credential and the PR-only rulesets are conventions the launched process follows rather than boundaries an autonomous agent cannot cross, and that trusted-writer admission and human merge are the enforceable controls. Documentation SHALL explain doctor, status and `--all`, tick, pause, resume, service installation and restart, evidence locations including a host attempt's Runner session directory, the human-review handoff, the blocked-bug loop, and the merge sync. The GitHub setup documentation SHALL describe the harness setting as a branch resolved at admission, not a commit pin.

Credentials for the suite's candidate branch, the fix PR credential, and board/routing credentials SHALL remain separately configured. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

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

### Requirement: Retain evidence for a bounded period

The factory SHALL prune attempt evidence of settled claims after a configurable retention period, default 14 days. A claim's evidence SHALL be eligible only when all of the following hold: its card has been observed Done, and the retention period has elapsed since the factory first durably recorded that observation; no run of the claim is non-terminal or of unverified ownership; all of the claim's reporting has been delivered; any post-merge sync the claim requires has completed; and its worktree, clone, image, and credential cleanup has settled. Pruning SHALL remove logs, Runner session state, agent session state, and agent output under the attempt's artifact directory and, for a host attempt, its recorded Runner session directory. Pruning SHALL keep the fix outcome, eval result and provenance records, and the attempt's issue input, and SHALL NOT touch candidate branches, PRs, mirrors, SQLite history, or the operator's working clones. The factory SHALL record what it removed and any failures, retry failed pruning on later polls, and run pruning both at Done cleanup and as a sweep on each tick so evidence that predates this rule is covered. Pruning SHALL NOT touch a claim whose card is not currently Done: the current-Done condition SHALL be established from the board observation of the same poll that prunes, a card observed in any other state SHALL reset the recorded Done observation, and a claim whose card is no longer on the board SHALL NOT be pruned. A superseded or cancelled claim SHALL be pruned on the same conditions judged on its own runs and reporting; because the factory does not clean up a superseded claim's clones or images, and releases a cancelled claim's at cancellation rather than at Done, the cleanup condition is not applied to them. Pruning SHALL remove only enumerated evidence paths and SHALL keep any file or directory it does not recognise.

#### Scenario: Prune after the retention period

- **WHEN** a claim's card was observed Done more than 14 days ago under the default retention and nothing still needs its evidence
- **THEN** the next tick removes its logs, session state, and agent output
- **AND** its outcome or result records, issue input, candidate branches, and PRs remain

#### Scenario: Skip a Done claim with a pending sync

- **WHEN** a fix claim's card is Done but its post-merge sync has not completed
- **THEN** its evidence is not pruned however old the claim is

#### Scenario: Prune a cancelled claim that recorded a PR

- **WHEN** a cancelled fix claim recorded a PR and its card has been observed Done for the retention period
- **THEN** its evidence is pruned without waiting on a post-merge sync, which only settled claims receive

#### Scenario: Reach Done after a long time

- **WHEN** a claim that has existed for months is moved to Done today
- **THEN** its evidence is retained for the full retention period from today's Done observation

#### Scenario: Sweep pre-existing history

- **WHEN** the factory first runs with this rule against a database whose old Done claims have no recorded Done observation
- **THEN** it records the observation on that poll and prunes those claims only after the retention period from that observation

#### Scenario: Fail to prune

- **WHEN** removal of an eligible claim's evidence fails partway
- **THEN** the factory records the failure and remaining work and retries on later polls without blocking other jobs

