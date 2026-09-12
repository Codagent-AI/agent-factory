## MODIFIED Requirements

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, and the fix credential file location. It SHALL supply Codagent as an example deployment configuration. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review.

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

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, Docker availability and memory allowance against one reservation, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against the configured minimum. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, and the companion fix workflow at the configured Runner branch declares a compatible contract version. It SHALL distinguish available prerequisites from problems needing operator action and explain each failed check. Diagnosis SHALL NOT launch an attempt or attempt to repair credentials or configuration.

Shared diagnostics SHALL remain distinct from checks supplied by each work kind and suite.

#### Scenario: Diagnose an unavailable prerequisite

- **WHEN** the operator runs doctor with Docker stopped, invalid required authentication, or an invalid Project mapping
- **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an attempt

#### Scenario: Diagnose suite readiness

- **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
- **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites

#### Scenario: Diagnose fix readiness

- **WHEN** the fix credential is missing, contains additional variables, or the Runner branch lacks a compatible fix workflow
- **THEN** doctor reports the fix-specific problem and shows eval readiness independently

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix claims, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

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

Configuration SHALL support the approved default eval limits of 30 minutes without progress, six hours of execution excluding recognized quota waits, 12 hours total per attempt, the default fix limits of 15 minutes, two hours, and three hours, a five-hour fallback for Codex reset holds, and a default memory reservation of 3 GiB per attempt. These values SHALL be configurable. The configured free-space minimum and memory reservation SHALL be checked before admission; insufficient space or memory SHALL hold new affected work without consuming an execution retry. The local configuration SHALL note that the fix window matters only when a fix role selects Codex.

#### Scenario: Resume while another hold remains

- **WHEN** the operator resumes a paused factory while a usage hold remains active
- **THEN** the pause clears but execution waits until the usage hold and other admission conditions permit it

#### Scenario: Run below the free-space minimum

- **WHEN** free disk space is below the configured minimum before admission
- **THEN** the factory starts no affected attempt, reports the storage problem, and rechecks readiness without consuming a recovery retry

#### Scenario: Pause during a fix

- **WHEN** the operator pauses while a fix and an eval are both running
- **THEN** both finish under their limits and no new attempt of either kind starts until resume

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees and clones, target repository mirrors, and attempt artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations. The operator's working clones used by the merge sync SHALL be outside the root and are never created by the factory.

Evidence and candidate outputs SHALL be retained until manual cleanup. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, state the disk and memory the machine needs to run one eval and one fix concurrently, and perform operator-managed cleanup while preserving work still needed for execution, recovery, or human review. Automatic evidence and candidate-output pruning is outside this change; factory-owned worktrees and clones follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees and clones, mirrors, and artifacts
- **AND** commands and result reports identify the actual paths in use

#### Scenario: Retain evidence after handoff

- **WHEN** an evaluation or fix is handed off for human review
- **THEN** its artifacts and required suite files remain available until the reviewed item moves to Done; evidence remains retained after worktree or clone cleanup

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees, or for a fix its per-attempt clones and run-specific images. It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors. Worktrees and clones SHALL remain available while work is running, waiting, blocked, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees, clones, and images and SHALL NOT remove shared source checkouts, the operator's working clones, or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, blocked, or in Review, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution, recovery, and human judging
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

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, mirrors and working clones, Docker startup and memory allowance, model authentication, GitHub routing and board permissions, suite prerequisites, the companion fix workflow and its contract version, explicit service paths, login behavior, and preventing idle sleep. Documentation SHALL explain doctor, status, tick, pause, resume, service installation and restart, evidence locations, the human-review handoff, the blocked-bug loop, and the merge sync.

Credentials for the suite's candidate branch, the fix PR credential, and board/routing credentials SHALL remain separately configured. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

#### Scenario: Set up the supported deployment

- **WHEN** an operator follows the installation instructions with the required credentials, suite behavior, and companion workflow available
- **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution of both kinds, inspect progress, pause and resume work, run a posted human-review command, and review a factory fix PR

#### Scenario: Reuse the public example

- **WHEN** another organization follows the public setup documentation
- **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
- **AND** the documentation clearly identifies any additional handler, suite, or workflow implementation needed for different work behavior

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
