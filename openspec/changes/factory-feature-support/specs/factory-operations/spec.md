## MODIFIED Requirements

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

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval, eval-sandbox, eval-fly, fix-sandbox, fix-host, or feature-host and label each so the operator can see which kind a failure holds; the eval group holds the mode-neutral eval checks that apply under every eval execution mode. It SHALL run only the groups that apply to a kind under its configured execution mode. Docker availability, memory allowance against one reservation, sandbox launcher checks, and reclaimable Docker space SHALL be checked and reported only under kinds configured for Docker execution; when no kind is configured for Docker, doctor SHALL neither probe Docker nor print any Docker line. The eval-fly group SHALL verify that the Fly API is reachable with the configured deploy token, the configured app exists, the configured image's repository (the configured `image` with any tag removed) is the configured app's `registry.fly.io` repository that the per-claim build pushes to, a Claude login is deliverable as defined in `factory-fly-execution` whenever an eval role uses Claude, using the same bounded Keychain read the launcher uses, the deploy-token file is owner-readable and contains only that token, the factory's own Fly launcher is resolvable, and `flyctl` is executable on the service PATH for transport. The factory SHALL resolve its launcher from the service PATH when present and otherwise from the directory holding the running factory, so that a service started without a bespoke PATH entry still finds the launcher shipped with it. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, and the packaged fix and review workflows each declare a compatible contract version. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When the feature kind is configured, it SHALL run the host checks of the fix-host group against the feature roles, verify that the packaged feature and define workflows declare a compatible contract version and that the installed Agent Runner provides the `core/verify-change` builtin workflow, and report each fix target without an `openspec/` directory as informational. When a kind is configured for Docker and Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. On macOS, when a login-Keychain item with service `Claude Code-credentials` and account `unknown` exists, doctor SHALL report it as informational only, explaining that it is a stale login created by a process without `USER`; it SHALL NOT fail on it or delete it. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt, create a Machine, build an image, print any credential, or attempt to repair credentials, Keychain items, or configuration.

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

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix and feature claims, settled fix and feature claims with eligible review comments waiting for their kind's slot, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's state including whether it is stopped for a quota hold, and the attempt's recorded deadline; it SHALL also list Machines that reconciliation reported as unknown to the store or as failed cleanup. It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a merge sync, or holding a recorded cleanup failure; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a waiting review round, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

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

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees, or for a fix or feature its per-attempt clones and, for sandbox attempts, run-specific images. It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors, subject to the evidence retention requirement. Worktrees and clones SHALL remain available while work is running, waiting, blocked, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

A fix or feature claim cancelled by issue closure SHALL have its clones, run-specific images, and credential copies released on the first poll after its execution has stopped, whatever its card's status, since a cancelled card may never pass through Review; its evidence SHALL remain subject to the retention requirement.

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

### Requirement: Retain evidence for a bounded period

The factory SHALL prune attempt evidence of settled claims after a configurable retention period, default 14 days. A claim's evidence SHALL be eligible only when all of the following hold: its card has been observed Done, and the retention period has elapsed since the factory first durably recorded that observation; no run of the claim is non-terminal or of unverified ownership; all of the claim's reporting has been delivered; any post-merge sync the claim requires has completed; and its worktree, clone, image, and credential cleanup has settled. Pruning SHALL remove logs, Runner session state, agent session state, and agent output under the attempt's artifact directory and, for a host attempt, its recorded Runner session directory. Pruning SHALL keep the fix or feature outcome, eval result and provenance records, and the attempt's issue input, and SHALL NOT touch candidate branches, PRs, mirrors, SQLite history, or the operator's working clones. The factory SHALL record what it removed and any failures, retry failed pruning on later polls, and run pruning both at Done cleanup and as a sweep on each tick so evidence that predates this rule is covered. Pruning SHALL NOT touch a claim whose card is not currently Done: the current-Done condition SHALL be established from the board observation of the same poll that prunes, a card observed in any other state SHALL reset the recorded Done observation, and a claim whose card is no longer on the board SHALL NOT be pruned. A superseded or cancelled claim SHALL be pruned on the same conditions judged on its own runs and reporting; because the factory does not clean up a superseded claim's clones or images, and releases a cancelled claim's at cancellation rather than at Done, the cleanup condition is not applied to them. Pruning SHALL remove only enumerated evidence paths and SHALL keep any file or directory it does not recognise.

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
