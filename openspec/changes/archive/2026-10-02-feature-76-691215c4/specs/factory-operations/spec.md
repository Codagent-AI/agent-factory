## ADDED Requirements

### Requirement: Document the task kind

The operator documentation (`AGENTS.md` and `docs/operations.md`) SHALL describe the task kind:

- the native Task type, and that a Task reaches the factory only by moving it to Ready;
- the `[task]` shared and local settings and their defaults, and the task-host doctor group;
- what triage declines, the bounded-choice rule, and the boundary between toolchain work and release configuration;
- the `chore:` commit and pull request convention and the gate exercises;
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

## MODIFIED Requirements

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, evidence retention, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, the fix credential file location, the fix execution mode (`docker` by default, or `host`), and an optional fix-specific minimum free disk space that defaults to the shared minimum. Feature configuration SHALL include the feature role profiles, feature limits, the feature admission window, and the feature workflow contract; the feature kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new feature work SHALL be enabled only when the feature configuration is present; when it is removed, existing feature claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The feature kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. Task configuration SHALL include the native task type in routing configuration (default `Task`), the task role profiles (lead, implementor, tester), the task workflow contract (default `factory-task/1`), task limits, the task admission window, and an optional task-specific minimum free disk space; the task kind SHALL use the fix targets, branch names, and fix credential. Handoff and admission of new task work SHALL be enabled only when the shared task configuration is present; when it is removed, existing task claims SHALL continue to be supervised, reported, synced after merge, cleaned up, and pruned. The task kind SHALL accept only host execution; configuration selecting Docker or Fly execution for it SHALL be rejected when configuration loads. The eval kind SHALL accept `docker` (the default) or `fly` execution and SHALL NOT accept host execution. Fly configuration SHALL be local and SHALL include the Fly app, region (default `ewr`), Machine CPU kind, CPU count, and memory (default shared, 4 CPUs, 8 GiB), the sandbox image reference, the deploy-token file location alongside the other controller credentials, and the collection grace period. The execution mode, fix disk floor, Fly settings, and retention period SHALL be local configuration. It SHALL supply Codagent as an example deployment configuration whose eval role defaults are `lead = claude:opus:medium`, `implementor = codex:gpt-5.6-luna:medium`, and `tester = codex:gpt-5.6-luna:medium`, and which enables the feature kind with role defaults matching its fix role defaults and the task kind with a Sonnet-class lead. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review. An admission window whose start hour equals its stop hour SHALL be always open.

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

- the slot holder and progress;
- waiting work and why it waits;
- blocked fix, feature, and task claims;
- settled fix, feature, and task claims with eligible review comments waiting for their kind's slot;
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

#### Scenario: Inspect a declined task

- **WHEN** a task claim was declined by triage
- **THEN** status shows the task slot as free and the blocked task with its decline reason

#### Scenario: Inspect the task slot

- **WHEN** a task attempt is running
- **THEN** status shows the task slot's holder and progress beside the eval, fix, and feature slots
