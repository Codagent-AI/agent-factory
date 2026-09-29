## ADDED Requirements

### Requirement: Configure service-driven watching

The shared configuration SHALL accept an optional `[watch]` section with these settings:

- `enabled` (default false);
- the factory `repository` (`owner/name`) that dispatched sessions check out, required when watching is enabled;
- the default dispatch `agent` profile in `cli:model:effort` form, required when watching is enabled;
- optional per-event profiles for `PR-READY` and `FAILURE`;
- the concurrency cap (default 2, at least 1);
- the per-day session budget (default 20, zero or more);
- the failure grace period in minutes (default 7, zero or more);
- the session timeout in minutes (default 90, at least 1);
- an optional operator GitHub login that decisions comments mention.

When watching is enabled, configuration loading SHALL fail on a missing repository or default profile, a profile that is not in `cli:model:effort` form, an unknown event name, or a value out of range, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior. The Codagent example configuration SHALL enable watching with the default profile `claude:claude-sonnet-5-5:medium`. Each cycle SHALL read the watch settings from the configuration it loads, so a changed profile, cap, budget, grace period, or timeout applies to dispatches that start after the change. A session that is already running SHALL keep its profile and timeout.

#### Scenario: Configure the dispatch model and budget

- **WHEN** the shared configuration enables watching with the agent `claude:claude-sonnet-5-5:medium` and a budget of 12
- **THEN** dispatched sessions run with that profile, and no more than 12 sessions start in a local day

#### Scenario: Reject an invalid profile

- **WHEN** watching is enabled with the agent `sonnet`
- **THEN** configuration loading fails and names the `[watch]` agent setting

#### Scenario: Leave watching unconfigured

- **WHEN** the shared configuration has no `[watch]` section
- **THEN** the factory detects no watch event, starts no session, and shows watching as disabled in status

### Requirement: Report watch dispatches in status

When watching is enabled, `agent-factory status` SHALL show a watch section with:

- whether watching is enabled;
- the "handled up to" time;
- each `launched` dispatch with its event, claim, pull request when there is one, model profile, and elapsed time;
- the number of `pending` dispatches, and why they wait: the concurrency cap, a failing watch doctor group, or a running review of the same pull request;
- each ended dispatch whose usage delivery to the development-audit destination did not succeed;
- the number of sessions started today against the budget, and today's known estimated cost;
- every dispatch recorded `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted` whose claim is not yet observed Done, cancelled, or superseded;
- each undelivered dispatch comment with its last failure reason;
- each decisions comment posted for a claim that is not yet observed Done, cancelled, or superseded, with its pull request.

When watching is disabled, status SHALL show one line saying so, and it SHALL still list `launched` and `pending` dispatches. Status SHALL NOT start or change any dispatch.

#### Scenario: Inspect a running triage

- **WHEN** a triage session is running and one review dispatch waits for the cap
- **THEN** status shows the triage session's event, claim, profile, and elapsed time, and one pending dispatch waiting for the concurrency cap

#### Scenario: Inspect the day's spend

- **WHEN** seven sessions have started today with known costs, and the budget is 20
- **THEN** status shows 7 of 20 sessions and the sum of their estimated costs

#### Scenario: Inspect a failed dispatch

- **WHEN** a review dispatch timed out for a claim that is still in Review
- **THEN** status lists that dispatch as `timed-out` with its pull request and evidence path

### Requirement: Redispatch a watch event

`agent-factory watch redispatch <dispatch>` SHALL queue a new `pending` attempt for the same event as the named dispatch, under a new attempt key. It SHALL accept only a `PR-READY` or `FAILURE` dispatch whose state is `completed`, `interrupted`, `timed-out`, `launch-failed`, or `budget-exhausted`. It SHALL refuse any other dispatch, naming its state, and change nothing. The new attempt SHALL be processed like any `pending` dispatch, under the cap, the budget, and one session per pull request at a time. The command SHALL print the new dispatch's id. When watching is disabled, it SHALL say that the attempt waits until watching is enabled. The command SHALL NOT start the session itself.

#### Scenario: Redispatch an interrupted review

- **WHEN** the operator redispatches an `interrupted` `PR-READY` dispatch
- **THEN** a new pending attempt is queued, and the next cycle starts one review session for that pull request

#### Scenario: Refuse a running dispatch

- **WHEN** the operator redispatches a `launched` dispatch
- **THEN** the command names the `launched` state, queues nothing, and exits with an error

### Requirement: Document the service-driven watcher

The operations documentation and the factory-watch skill SHALL describe service-driven watching as the normal mode:

- the four events and what each one does;
- the `[watch]` settings and their defaults, and how to escalate a failure to a stronger model;
- the budget and concurrency behavior, and the budget-exhausted comment;
- the actions a dispatched session may and may not take;
- that triage runs after a failed claim's automatic retry and does not hold it;
- the watch doctor group, including the writer `gh` login that reviews need;
- the watch section of status;
- `watch redispatch`;
- how to find a dispatch's evidence and usage.

They SHALL state that an interactive watcher session is no longer needed, and that running one alongside service watching duplicates reviews and triage. They SHALL keep manual `watch.sh` use documented for debugging. The factory PR review procedure SHALL document its headless mode: decisions are returned in the result instead of asked.

#### Scenario: Operate the service watcher

- **WHEN** an operator follows the documentation to enable watching
- **THEN** they can set the profile and budget, pass the watch doctor group, find running and failed dispatches in status, and redispatch a failed one

## MODIFIED Requirements

### Requirement: Diagnose readiness with doctor

`agent-factory doctor` SHALL check GitHub authentication and required access, configured Project fields and options, required model authentication, repository/worktree availability, selected-suite readiness, required token environment files, and free disk space against each kind's configured minimum. It SHALL group checks as shared, eval, eval-sandbox, eval-fly, fix-sandbox, fix-host, feature-host, or watch and label each so the operator can see which kind a failure holds; the eval group holds the mode-neutral eval checks that apply under every eval execution mode. It SHALL run only the groups that apply to a kind under its configured execution mode, and the watch group only when watching is enabled. The watch group SHALL verify that the installed Agent Runner, `git`, and `gh` are executable on the service PATH; that the default dispatch profile and every per-event profile are in `cli:model:effort` form, and each CLI they select is authenticated and carries the codagent plugin; that the packaged watch session workflow declares a compatible contract version; that the factory repository can be fetched for session checkouts; and that `gh` on the service PATH is authenticated as a login that has write access to the factory repository and is not the factory bot. A failing watch group SHALL hold only the launch of dispatched sessions. Detection, queueing, logging of claim and eval-completion events, and the other kinds' admission SHALL continue. Docker availability, memory allowance against one reservation, sandbox launcher checks, and reclaimable Docker space SHALL be checked and reported only under kinds configured for Docker execution; when no kind is configured for Docker, doctor SHALL neither probe Docker nor print any Docker line. The eval-fly group SHALL verify that the Fly API is reachable with the configured deploy token, the configured app exists, the configured image's repository (the configured `image` with any tag removed) is the configured app's `registry.fly.io` repository that the per-claim build pushes to, a Claude login is deliverable as defined in `factory-fly-execution` whenever an eval role uses Claude, using the same bounded Keychain read the launcher uses, the deploy-token file is owner-readable and contains only that token, the factory's own Fly launcher is resolvable, and `flyctl` is executable on the service PATH for transport. The factory SHALL resolve its launcher from the service PATH when present and otherwise from the directory holding the running factory, so that a service started without a bespoke PATH entry still finds the launcher shipped with it. For the fix kind it SHALL additionally verify that each target mirror can be fetched, each configured working clone exists and is a Git repository, the fix credential file is owner-readable, contains exactly one repository token variable and no other variable, authenticates, reaches each target repository, and is not the controller's own identity nor an organization administrator, the packaged fix and review workflows each declare a compatible contract version, and every fix role has a `cli:model:effort` profile. In host mode it SHALL verify, against the service environment, that the installed Agent Runner, `git`, `gh`, `jq`, `python3`, and the validator are executable, that each CLI selected by the fix roles is authenticated and carries the codagent plugin, and that the operator's Runner user settings select the headless backend and yolo permission mode. When the feature kind is configured, it SHALL run the host checks of the fix-host group against the feature roles, verify that every feature role has a `cli:model:effort` profile, that the packaged feature and define workflows declare a compatible contract version, and that the installed Agent Runner provides the `core/verify-change` builtin workflow, and report each fix target without an `openspec/` directory or without an Agent Validator configuration as informational. When a kind is configured for Docker and Docker is running it SHALL report the space Docker could reclaim and the command that reclaims it, without running that command. On macOS, when a login-Keychain item with service `Claude Code-credentials` and account `unknown` exists, doctor SHALL report it as informational only, explaining that it is a stale login created by a process without `USER`; it SHALL NOT fail on it or delete it. It SHALL distinguish available prerequisites from problems needing operator action, explain each failed check, and print no action on a passing check. Diagnosis SHALL NOT launch an attempt, create a Machine, build an image, print any credential, or attempt to repair credentials, Keychain items, or configuration.

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

- **WHEN** watching is enabled and `gh` on the service PATH is not authenticated, or is authenticated as the factory bot
- **THEN** doctor fails the watch group naming the login problem and the action to take, reports the other groups independently, and pending review and triage dispatches wait without starting a session

#### Scenario: Run doctor with watching disabled

- **WHEN** watching is disabled or unconfigured
- **THEN** doctor runs no watch check and prints no watch group
