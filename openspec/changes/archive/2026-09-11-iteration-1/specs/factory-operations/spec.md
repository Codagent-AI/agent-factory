## ADDED Requirements

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules, request labels, field and option mappings, repository locations, evaluation defaults, schedule, supervision limits, minimum free disk space, and local storage paths. It SHALL supply Codagent as an example deployment configuration. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific repository and executable locations SHALL be supplied to the suite integration rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers or additional work-kind implementations.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the initial Eval request behavior and the field names, options, and defaults described by this change

### Requirement: Apply shared deployment changes through explicit updates

Shared deployment configuration SHALL be versioned with the factory and contain source repositories, routing rules, Project/field mappings, eval defaults, and the deployed harness full commit SHA. The harness pin SHALL NOT default silently to a source checkout's current HEAD or remote default branch. Machine-specific paths, schedule, execution limits, and credential-file locations SHALL be configured separately in local TOML. Secret values SHALL remain outside the versioned deployment configuration.

The installed factory SHALL use the shared configuration from its explicitly installed version and SHALL NOT automatically fetch configuration changes from main. Reusable routing workflows SHALL use shared configuration from their explicitly pinned factory revision. Deployment instructions SHALL cover updating the local factory and the caller workflows' routing revision together. Updating configuration SHALL NOT mutate frozen inputs or the execution configuration of an already-running attempt.

#### Scenario: Edit shared configuration in GitHub

- **WHEN** shared deployment configuration changes on main but the local factory has not been explicitly updated
- **THEN** the installed factory continues using its installed configuration
- **AND** ordinary queue polling and operational controls continue without waiting for a software update

#### Scenario: Deploy a shared configuration change

- **WHEN** the operator explicitly updates the local installation and routing workflow pins to the intended factory revision
- **THEN** subsequent routing and new claims use that revision's shared deployment configuration
- **AND** existing claims retain frozen inputs and already-running attempts retain their execution configuration

### Requirement: Run as a recoverable per-user Mac service

The change SHALL provide a launchd LaunchAgent configuration and setup instructions that start the factory for the configured Mac user on login and restart the controller if it crashes. Execution SHALL use explicit executable, configuration, and credential paths suitable for the service environment. Controller restarts SHALL preserve running evaluations as required by `factory-claim-lifecycle`; restarting the controller SHALL NOT itself restart or terminate those evaluations.

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

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, Docker availability, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against the configured minimum. It SHALL distinguish available prerequisites from problems needing operator action and explain each failed check. Diagnosis SHALL NOT launch an evaluation or attempt to repair credentials or configuration.

Shared diagnostics SHALL remain distinct from checks supplied by the selected work kind and suite. For the initial suite, setup and integration validation SHALL confirm the separately delivered automated-score failure contract described in `factory-eval-reporting`; implementing that suite behavior remains outside this change.

#### Scenario: Diagnose an unavailable prerequisite

- **WHEN** the operator runs doctor with Docker stopped, invalid required authentication, or an invalid Project mapping
- **THEN** doctor identifies the affected prerequisite and explains what needs attention without starting an evaluation

#### Scenario: Diagnose suite readiness

- **WHEN** generic factory prerequisites are available but the selected suite's required entry-point or fixture files are unavailable
- **THEN** doctor identifies the suite-specific readiness problem separately from the available factory prerequisites

### Requirement: Expose current operational status

`agent-factory status` SHALL show current work, repetition progress for eval claims, pause state, blocking conditions, and the next permitted start time when it can be determined. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, an unavailable prerequisite, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

### Requirement: Run an immediate normal cycle with tick

`agent-factory tick` SHALL perform one normal polling cycle immediately, including reconciliation and pending reporting, and MAY start eligible work. It SHALL apply the same admission window, pause state, prerequisite and quota holds, and single-execution guard as the resident service. It SHALL NOT act as a preview or force work past those controls. Execution started through tick SHALL receive the same supervision, persistence, and recovery guarantees as service-started execution.

#### Scenario: Tick with eligible queued work

- **WHEN** the operator runs tick with eligible work, no active execution, and all admission conditions satisfied
- **THEN** the factory performs the normal cycle and can start the selected request under normal supervision

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside the admission window, while paused, or while another execution is active
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work and reporting

### Requirement: Persist pause and enforce configured admission controls

`agent-factory pause` SHALL save a factory-wide pause while allowing the current repetition to finish. `agent-factory resume` SHALL clear that pause without clearing unrelated usage or prerequisite holds. The saved pause SHALL survive controller restarts. Under the default schedule, each repetition or recovery attempt SHALL start only from 00:00 up to but not including 15:00 local time. Already-running attempts SHALL follow the lifecycle limits rather than stop solely because the window closes.

Configuration SHALL support the approved default limits of 30 minutes without progress, six hours of execution excluding recognized quota waits, 12 hours total per attempt, and a five-hour fallback for Codex reset holds. These values SHALL be configurable. The configured free-space minimum SHALL be checked before admission; insufficient space SHALL hold new affected work without consuming an execution retry.

#### Scenario: Resume while another hold remains

- **WHEN** the operator resumes a paused factory while a usage hold remains active
- **THEN** the pause clears but execution waits until the usage hold and other admission conditions permit it

#### Scenario: Run below the free-space minimum

- **WHEN** free disk space is below the configured minimum before admission
- **THEN** the factory starts no affected evaluation, reports the storage problem, and rechecks readiness without consuming a recovery retry

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees, and evaluation artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations.

Iteration 1 SHALL retain evidence and candidate outputs until manual cleanup. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, and perform operator-managed cleanup while preserving work still needed for execution, recovery, or human review. Automatic evidence and candidate-output pruning is outside this change; factory-owned worktrees follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees, and artifacts
- **AND** commands and result reports identify the actual paths in use

#### Scenario: Retain evidence after handoff

- **WHEN** an evaluation is handed off for human review
- **THEN** its artifacts and required suite files remain available for the posted review command until the reviewed item moves to Done; evidence remains retained after worktree cleanup

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees. It SHALL preserve results, logs, SQLite history, candidate branches, and PRs. Worktrees SHALL remain available while work is running, waiting, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees and SHALL NOT remove shared source checkouts or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned Runner, Skills, and evals worktrees are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, or in Review, including an active item incorrectly dragged to Done
- **THEN** its worktrees remain available for execution, recovery, and human judging
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's worktrees fails or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

### Requirement: Provision the initial GitHub deployment

The change SHALL include the GitHub setup for the Codagent example deployment: an organization-level Codagent Project, native Eval issue type, `eval-request` and red `needs-input` labels, regular Markdown eval issue template, shared routing rules and reusable workflow, source-repository caller workflows, required permissions, general issue/PR auto-add, and closure automation.

The board SHALL have Backlog, Ready, Running, Review, and Done Status columns, horizontal groups by native issue Type, no field-based sorting, and views for the eval queue and active factory work. It SHALL expose configured Owner, Refs, and Verdict fields and visible attention labels. Verdict options SHALL support `pending-human-review`, `failed`, `quota-deferred`, and `infra-error`; the factory SHALL never assign `passed` in iteration 1.

The initial eval request source SHALL be `Codagent-AI/agent-evals`. General work from the configured Codagent repositories SHALL enter Backlog; eval routing SHALL initialize factory ownership and Ready for authors with the required repository access as defined in intake, without a general auto-add rule undoing that routing. Other authors' requests SHALL enter Backlog without factory assignment. The initial general-work repository set SHALL cover agent-runner, agent-skills, agent-validator, agent-plugin, and agent-evals, and SHALL be configurable. Issue/PR closure automation SHALL move associated cards to Done. The board setup SHALL be confirmed with Paul before controller implementation, as required by the proposal.

Routing and the local controller SHALL authenticate with an organization-owned GitHub App with organization Projects read/write, repository Issues read/write, and Contents, Pull requests, and Metadata read access at minimum. The Codagent deployment MAY grant Pull requests write access for future use; iteration 1 suite PR operations SHALL continue to use their separate credentials. Installation SHALL cover the configured repositories; an operator MAY choose an organization-wide installation. Routing SHALL still apply only to configured source repositories. App private keys SHALL remain outside version control, with local owner-only file access and Actions secret storage for caller workflows.

#### Scenario: Create an eval request in the configured source repository

- **WHEN** a user with write, maintain, or admin access to the source repository creates an issue from the eval template
- **THEN** the configured automation places it in the Project's Ready column with native Eval type and factory ownership without a separate manual handoff
- **AND** general auto-add behavior does not reset the routed request to Backlog

#### Scenario: Add general work

- **WHEN** an ordinary issue or PR is created in a configured source repository without the eval request marker
- **THEN** it is added to the shared Project in Backlog without becoming an eval execution request

#### Scenario: Arrange the board manually

- **WHEN** the operator views the configured board
- **THEN** Status determines columns, native Type determines horizontal groups, and cards can be manually reordered without a field sort overriding their positions

#### Scenario: Close tracked work

- **WHEN** a tracked issue or PR is closed
- **THEN** its card moves to Done through closure automation

### Requirement: Document installation and service operation

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, Docker startup, model authentication, GitHub routing and board permissions, suite prerequisites, explicit service paths, login behavior, and preventing idle sleep. Documentation SHALL explain doctor, status, tick, pause, resume, service installation and restart, evidence locations, and the human-review handoff.

Credentials for the suite's candidate branch and draft-PR operations SHALL remain separately configured from board/routing credentials. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

#### Scenario: Set up the supported deployment

- **WHEN** an operator follows the installation instructions with the required credentials and separately delivered suite behavior available
- **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution, inspect progress, pause and resume work, and run a posted human-review command

#### Scenario: Reuse the public example

- **WHEN** another organization follows the public setup documentation
- **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
- **AND** the documentation clearly identifies any additional handler or suite implementation needed for different work behavior
