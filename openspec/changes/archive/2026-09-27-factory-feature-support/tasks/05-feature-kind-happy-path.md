# Task: Configure, admit, run, and report feature work on the host

## Goal

Register the `feature` work kind so that a maintainer can move a writer-authored
Feature-typed issue in a configured fix target to Ready and receive a finalized pull
request: feature configuration (host only, sharing the fix targets, branches, and
credential), the feature `PullRequestKind` definition and handler registration, the Ready
handoff and Priority selection into the feature slot, the host launch of the packaged
`factory-feature` workflow, outcome mapping and issue comments for admission, pull request,
failure, and exhausted recovery, doctor's `feature-host` group, status, cleanup, merge sync,
and retention for feature claims, the Codagent example configuration, and operator
documentation. Stops, resumes, continuations, and recovery resume points are not part of
this task; a feature attempt launched here always starts fresh (`resume_from=""`,
`prior_branch=""`).

## Background

Read `openspec/changes/factory-feature-support/design.md`, sections B2 (the `FEATURE`
column of the definition), B4, B5, B7, D (the feature workflow's params), "Migration Plan",
and the risks "The audit runs inside the attempt" and "Crosscheck model".

Where things are (paths relative to `src/agent_factory/`):

- `work_kinds/pull_request/kinds.py` defines the frozen `PullRequestKind`, the
  `ReconcilePolicy` enum (`SETTLE_ON_OPEN_PR`, `RESUME_FROM_OWN_BRANCH`), the `FIX`
  instance, and `FEATURE_STAGED_FILES` (the packaged `factory-feature-v1.0.yaml`,
  `factory-define-v1.0.yaml`, `factory-define-rules.md`, and their scripts in
  `work_kinds/pull_request/workflow/`). `PullRequestHandler(definition, shared, local)` in
  `work_kinds/pull_request/handler.py` implements the handler capabilities the runtime calls
  for every registered kind (`ready_handoff`, `unblock`, `review_round`, `merge_sync`,
  `pending_sync`, `retention_targets`, `blocked_reason`, `execution_mode`, and so on).
  `work_kinds/__init__.py` `handlers()` registers the kinds. Per-kind slots are keyed by
  kind in SQLite, so the feature slot needs no schema change.
- Every plan names its backend (`backends/resolve.py`); host plans come from
  `work_kinds/pull_request/launch.py` `build_host_plan`, which runs `host-run.sh`
  (`host_script`): the Runner with `--session-dir <evidence>/agent-runner-session`, then the
  post-run audit with the credential removed, exiting with the workflow's status.
- `config.py`: `SharedConfig`/`LocalConfig` loaders; `_fix_local_config` and
  `_fix_shared_config` show the fix pattern; the eval execution-mode rejection is the
  precedent for rejecting unsupported modes with `ConfigurationError`.
- `operations.py` holds doctor (`DiagnosticGroup`, `_GROUP_ORDER`, host executable checks
  now provided by the host backend's `readiness`) and status (slot lines and provider maps
  iterate registered kinds).
- `routing.py` applies the untyped-issue rule on type changes (an untouched,
  routing-initialized card returns to Backlog; human edits are preserved). Verify that a
  retype to the feature type follows it, and that routing never initializes
  `Owner=factory`/`Status=Ready` for the feature type.
- `retention.py` calls the handler's `retention_targets`.
- `github.py` already ranks queues by Priority, then newest created.
- Existing e2e harness: `tests/e2e/test_fix_cycle.py` (stub `gh`, stub board, stub host
  `agent-runner`).
- Docs: `docs/installation.md`, `docs/operations.md`, `docs/github-setup.md`.

Decisions to implement:

- **Configuration (B5).** Shared: `routing.feature_type` (default `Feature`); `[feature]`
  with `contract` (default `factory-feature/1`) and `[feature.defaults]` roles `lead`,
  `implementor`, `tester`, `crosscheck`. Local: `[feature]` with `limits` (defaults 1800 /
  21600 / 28800 seconds for no progress / execution / total), `schedule` (default always
  open), `execution` (only `"host"` accepted; anything else raises `ConfigurationError`
  naming the unsupported mode, in a new `_feature_local_config`), and `minimum_free_gib`.
  Targets, branches, and `credentials.fix_environment` are shared with fix.
- **Registration.** The feature handler is always registered, so claims already recorded
  keep being supervised, reported, synced, cleaned up, and pruned; the shared `[feature]`
  section only enables handoff and admission of new feature work (`ready_handoff` and
  admission return nothing without it). A claim's roles are frozen at admission; without
  `[feature]`, limits and window fall back to their defaults.
- **Definition.** `FEATURE = PullRequestKind(kind="feature", unit_key="feature",
  noun="Feature", item_noun="feature", issue_type=routing.feature_type,
  workflow_name="factory-feature", workflow_file="factory-feature-v1.0.yaml",
  staged_files=FEATURE_STAGED_FILES, contract=feature.contract,
  outcome_file="feature-outcome.json", branch_prefix="factory/feature",
  sync_marker="feature-sync", allowed_modes=("host",), roles=(lead, implementor, tester,
  crosscheck), doctor_groups={"host": "feature-host"}, reconcile=RESUME_FROM_OWN_BRANCH, ...)`.
  Staging then copies the feature files into every clone.
- **Launch.** The host plan passes `issue_file`, `branch_name` (deterministic from the issue
  and claim under `factory/feature`), `change_name` (derived from the issue), `contract_version`,
  `artifact_dir`, `resume_from`, and `prior_branch` (both empty in this task), and stages the
  factory profile set including `crosscheck`. The plan carries `backend=host` beside its
  legacy hints. The launch refuses (claim held, reported in status and doctor) when the
  packaged workflow's contract is incompatible or the installed Runner lacks
  `core/verify-change`.
- **Doctor.** When `[feature]` is configured: run the fix-host host checks against the
  feature roles (including the `crosscheck` role's CLI authentication), check the packaged
  feature and define workflows' contract, check that the installed Runner provides
  `core/verify-change` (fail naming it), and list fix targets without `openspec/` as
  informational.
- **Reporting.** Admission comment includes refs, attempt number, and "starts fresh"; the
  inputs-accepted comment reads `Feature inputs accepted and frozen.`; the pull-request
  comment states red, orange, and yellow counts from the outcome's
  `review_attention_counts`; every outcome comment states the attempt ran on the host and
  that the recorded Runner and Skills commits were not the versions that executed. Board:
  `pull-request` → Review with `pending-human-review`; `failed` → Review with `failed` and
  the pushed branch or open pull request linked; exhausted recovery → Review with
  `infra-error`.
- **Codagent configuration.** Do not add a `[feature]` section to `config/codagent.toml`
  in this task. The design's migration plan (steps 4 and 5) lands the feature code with the
  section absent and enables features only once stops, resumes, and recovery are in place.
  Tests that need features enabled use their own configuration files.
- **Docs.** Document feature setup (configuration, `crosscheck` role, Runner prerequisite),
  the Ready handoff gesture and its authorization rule, doctor's `feature-host` group, and
  status for features in `docs/installation.md`, `docs/operations.md`, and
  `docs/github-setup.md`.

## Spec

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

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

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

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

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

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

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

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

_From `specs/factory-operations/spec.md` (MODIFIED Requirements)_

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

_From `specs/factory-feature-intake/spec.md` (ADDED Requirements)_

### Requirement: Hand off features to the factory through Ready

On each Project poll, the factory SHALL treat placement of an open issue from a configured fix target with the configured native feature type in `Status=Ready`, when the feature kind is configured, as an explicit handoff and set `Owner=factory` before admission when the issue author has effective write, maintain, or admin access to the repository, regardless of the prior or missing Owner value. This handoff SHALL be the only way a feature becomes factory work. The factory SHALL NOT identify the person who moved the card; that gesture is trusted through Project write access. Failure to establish the author's permission SHALL NOT be treated as authorization. A GitHub issue assignee SHALL NOT be required.

#### Scenario: Ready placement assigns factory ownership

- **WHEN** a human moves an open Feature from a configured fix target to Ready with Owner unset or set to human, and its author has write access
- **THEN** the next factory poll verifies the author's permission and sets `Owner=factory`
- **AND** admission re-verifies permission before work starts

#### Scenario: Move an outside contributor's feature to Ready

- **WHEN** a human moves a Feature to Ready whose author lacks write access to the repository
- **THEN** the factory does not set `Owner=factory` and admits no work
- **AND** it explains on the issue once why the feature is not eligible

#### Scenario: Move a feature from an unconfigured repository

- **WHEN** a Feature from a repository that is not a configured fix target is moved to Ready
- **THEN** the factory leaves the card unchanged and admits no work

_From `specs/factory-feature-intake/spec.md` (ADDED Requirements)_

### Requirement: Select eligible features by Priority

The factory SHALL select open issues from configured fix targets with the configured native feature type, `Owner=factory`, and `Status=Ready`, whose authors have effective write, maintain, or admin access verified again at admission, that carry no `needs-input` label, and that have no applicable admission hold. Selection SHALL rank eligible features by the Project Priority field, highest first with unset values last, then by newest creation time. An ineligible feature SHALL NOT prevent selection of a later eligible feature. Feature selection SHALL be independent of eval and bug selection: each kind fills only its own execution slot. Features SHALL carry no per-issue execution overrides; role profiles, branches, limits, and window come from factory configuration. Reordering or reprioritizing SHALL NOT interrupt an active attempt.

#### Scenario: Pick the highest-priority feature

- **WHEN** the feature slot is free and two eligible features with different Priority values sit in Ready
- **THEN** the factory admits the higher-priority feature regardless of issue age or repository

#### Scenario: Break a Priority tie by creation time

- **WHEN** two eligible features share a Priority value
- **THEN** the factory admits the more recently created feature first

#### Scenario: Skip a blocked or held feature

- **WHEN** the top-ranked feature carries the `needs-input` label or a feature-specific hold applies
- **THEN** the factory selects the next eligible feature instead

#### Scenario: Admit a feature while a fix runs

- **WHEN** a fix attempt occupies the fix slot and an eligible feature waits in Ready
- **THEN** the factory admits the feature into the free feature slot

#### Scenario: Recheck permission at admission

- **WHEN** a Ready Feature card has `Owner=factory` but its author no longer has write access
- **THEN** the factory does not admit it based only on its board fields
- **AND** it explains on the issue once why the feature is not eligible

_From `specs/factory-feature-intake/spec.md` (ADDED Requirements)_

### Requirement: Resolve feature branches once per claim

A new feature claim SHALL resolve the configured branches of the target repository, Agent Runner, and Agent Skills to commits at admission and record those commits on the claim. Configuration SHALL name branches, not commits. The claim's attempts, including technical recovery retries and attempts resumed after `needs-input`, SHALL use the recorded commits. The `Refs` field SHALL render as `target@<7> runner@<7> skills@<7>`. Only a new claim SHALL re-resolve branch heads.

#### Scenario: Admit a feature

- **WHEN** the factory admits a feature
- **THEN** it records the resolved commits for the target repository, Runner, and Skills on the claim
- **AND** the card's Refs shows those three abbreviated commits

#### Scenario: Resume after the target branch advanced

- **WHEN** the target branch receives new commits while a feature claim is blocked, and the claim is then re-admitted
- **THEN** the resumed attempt uses the commits recorded at admission

_From `specs/factory-routing/spec.md` (ADDED Requirements)_

### Requirement: Never route features to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured feature type, whatever the author's repository role. Such an issue SHALL be routed as an untyped issue is. When the feature type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule: a card whose Owner and Status still hold the values routing last initialized SHALL return to Backlog, and a card a human has edited since SHALL keep its values. An issue auto-routed to the factory as a bug and then retyped as a feature SHALL therefore not remain in Ready as factory feature work. The only path by which a feature becomes factory work SHALL be the Ready handoff defined by `factory-feature-intake`.

#### Scenario: File a feature as a maintainer

- **WHEN** a user with the maintain or admin role creates an issue with native Type Feature in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Retype an auto-routed bug as a feature

- **WHEN** routing placed a maintainer's bug in Ready with `Owner=factory`, no human has edited the card, and its native Type is changed to Feature
- **THEN** routing returns the card to Backlog
- **AND** the factory does not admit it as feature work

#### Scenario: Retype an edited card as a feature

- **WHEN** a human changed a routed card's Status or Owner and its native Type is then changed to Feature
- **THEN** routing leaves the card's Owner and Status unchanged

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Invoke the versioned feature workflow

The factory SHALL run the packaged `factory-feature` workflow, with its `factory-define` sub-workflow, through the operator's installed Agent Runner, passing the configured feature role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, the attempt's artifact directory, and the attempt's resume point when one exists. The resume point SHALL be the claim's own pushed branch, or, for a new claim continuing a settled prior claim, the prior claim's branch supplied at admission. The factory SHALL ship the feature workflows beside the fix, review, and shared implementation workflows and stage all of them into the Runner catalog the attempt uses. The workflow contract SHALL be versioned as `factory-feature/1`. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the installed Runner does not provide what the workflow requires, including the `core/verify-change` builtin workflow, and SHALL hold the claim and report the problem in status and doctor. The workflow SHALL write exactly one structured outcome to `feature-outcome.json` in the attempt's artifact directory, declaring its contract version: `pull-request` with the pull request reference; `needs-input` with reasons; or `failed` with reasons. Absence of a structured outcome SHALL be treated as a technical failure. A feature attempt SHALL work on a deterministic branch named from the issue and claim.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged feature workflow declares a compatible contract version and the installed Runner provides `core/verify-change`
- **THEN** the attempt starts under its own supervisor on the host with the configured feature roles
- **AND** the staged workflow directory holds the feature, define, fix, review, and implementation workflow files

#### Scenario: Launch against a Runner without verify-change

- **WHEN** the installed Runner lacks the `core/verify-change` builtin workflow
- **THEN** no attempt is recorded, the feature is held, and status and doctor name the missing workflow

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing `feature-outcome.json`
- **THEN** the factory records a technical failure and applies the recovery policy

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Apply feature-specific limits and window

Each feature attempt SHALL have configurable limits with defaults of 30 minutes without progress, six hours of execution, and eight hours of total elapsed time. Feature admission SHALL use its own configurable window, defaulting to always open, and SHALL honor pause, disk and memory admission checks, and provider quota holds for providers used by the feature roles. Feature admission SHALL NOT be bound to the eval or fix window. Exceeding a limit SHALL stop owned execution and be treated as a technical failure.

#### Scenario: Exceed the execution limit

- **WHEN** a feature attempt runs for six hours
- **THEN** the factory stops its owned execution and applies the recovery policy

#### Scenario: Admit a feature outside the eval window

- **WHEN** the eval window is closed and the feature window is open
- **THEN** an eligible feature is admitted

_From `specs/factory-feature-execution/spec.md` (ADDED Requirements)_

### Requirement: Execute features on the host only

Feature attempts SHALL run only in `host` execution mode. Configuration that selects Docker or Fly execution for the feature kind SHALL be rejected when configuration loads. A feature attempt SHALL follow the host execution rules of `factory-fix-execution` using the fix credential: clones from local mirrors at the recorded commits, the operator's HOME and installed Runner, process-local git settings, workflows supplied through the clone, a Runner session directory under the attempt's artifact directory, the credential kept out of persisted state, supervision by process, host provenance, and preserved evidence including the session directory.

#### Scenario: Configure Docker execution for features

- **WHEN** the configuration selects Docker or Fly execution for the feature kind
- **THEN** configuration loading fails and names the unsupported mode

#### Scenario: Inspect a feature attempt's run record

- **WHEN** a feature attempt has launched
- **THEN** its run record stores the host execution mode, the Runner path and version, and the session directory, and contains no credential

_From `specs/factory-claim-lifecycle/spec.md` (MODIFIED Requirements)_

### Requirement: Prevent overlapping execution

The factory SHALL execute at most one attempt per work kind at a time across the service and manual execution commands: one eval repetition, one fix attempt, and one feature attempt. Per-kind slots SHALL be enforced atomically in SQLite so two admission paths cannot both reserve the same kind's slot. The factory SHALL reconcile saved execution records with surviving processes, containers, and evidence before dispatching new work of any kind. A blocked fix or feature claim SHALL NOT occupy a slot. Status and pause controls SHALL remain usable while execution is active.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution while the service already has an attempt of the same kind running
- **THEN** no second attempt of that kind starts
- **AND** status and pause controls remain available

#### Scenario: Run an eval and a fix together

- **WHEN** an eval repetition is running and an eligible bug is admitted
- **THEN** the fix attempt starts in its own slot and the eval continues unaffected

#### Scenario: Migrate the single-slot database

- **WHEN** the factory starts against a database at the current shipped schema version with claims, runs, reporting progress, a pause, and an active quota hold
- **THEN** it migrates the schema to per-kind slots in one transaction without losing claim, run, or settings history
- **AND** the existing quota hold continues to apply to the provider it was recorded for

#### Scenario: Run a feature beside a fix and an eval

- **WHEN** an eval repetition and a fix attempt are running and an eligible feature is admitted
- **THEN** the feature attempt starts in its own slot and the other attempts continue unaffected

_From `specs/factory-claim-lifecycle/spec.md` (MODIFIED Requirements)_

### Requirement: Bound technical recovery per repetition

The factory SHALL allow one automatic recovery retry per repetition after a technical execution failure, using supported suite resume behavior and preserving completed repetitions. It SHALL record execution attempts and consumed retries durably. A completed evaluation with a poor product result SHALL NOT trigger a technical retry. A suite-confirmed non-resumable implementation-workflow failure SHALL settle that repetition as failed without a retry and SHALL allow remaining repetitions to proceed. Resumable workflow failures and recoverable harness failures SHALL follow the bounded technical recovery policy. The factory SHALL preserve the suite's failure owner, code, and resumable value and SHALL NOT infer non-resumability from the workflow-failed status alone. Recognized quota waiting, unavailable prerequisites detected before execution, and waiting for an admission window SHALL NOT consume this retry budget. Under Fly execution, a lost Machine SHALL settle that repetition as failed for a factory-owned infrastructure reason without consuming a retry and SHALL allow remaining repetitions to proceed, as defined in `factory-fly-execution`.

A pre-suite failure SHALL NOT consume the recovery retry. A pre-suite failure is one identified by an explicit launch-stage signal, never by elapsed time: a readiness or planning error raised after the attempt was reserved but before its process started; a failure of the attempt's process to start; under Fly execution, a failure of the per-claim image build, a launcher transport failure before the job was delivered to the Machine, or a job failure before the factory's job script wrote the setup-complete marker defined in `factory-fly-execution`. A failure after the suite or workflow process has started, including a model login failure, SHALL NOT be a pre-suite failure. On a pre-suite failure the factory SHALL record the failed attempt with its diagnostic, SHALL under Fly execution destroy any Machine that attempt created before anything relaunches (a recovery attempt's retained Machine SHALL remain retained), and SHALL hold the claim waiting with `infra-error` and an explanation. On a subsequent poll where readiness passes, the factory SHALL relaunch the same unit of work with the same recovery status it had, so a first attempt relaunches as a first attempt and a recovery attempt relaunches as that recovery attempt. A second consecutive pre-suite failure of the same unit SHALL stop the claim exactly as an exhausted recovery does. This SHALL apply to eval, fix, and feature claims alike.

If the recovery retry fails, the factory SHALL stop the claim, leave remaining repetitions unstarted, retain completed results and failure evidence, and move the issue to Review with `infra-error` and an explanation. Other eligible requests MAY proceed. Another evaluation SHALL require the explicit fresh-request behavior defined by intake and SHALL start the requested repetitions anew while preserving prior claim history.

#### Scenario: Recover the second repetition

- **WHEN** repetition 1 completed and repetition 2 encounters its first technical failure
- **THEN** the factory can resume repetition 2 once when execution is permitted
- **AND** repetition 1 remains completed

#### Scenario: Exhaust recovery

- **WHEN** repetition 2's recovery retry fails in a request for three repetitions
- **THEN** the claim stops, repetition 3 remains unstarted, and the issue moves to Review with `infra-error` and an explanation
- **AND** completed results and both failed attempts' evidence remain available
- **AND** the factory may process other eligible requests

#### Scenario: Preserve the retry limit across interruption

- **WHEN** the controller restarts or the card moves after the repetition has consumed its recovery retry
- **THEN** those events do not grant another automatic recovery retry

#### Scenario: Complete with a product failure

- **WHEN** an evaluation completes and establishes that the tested product failed
- **THEN** the factory reports the result without retrying it as a technical failure

#### Scenario: Settle a non-resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=false`, such as a typed prohibited workflow action
- **THEN** the factory records a failed repetition without attempting technical recovery and permits remaining repetitions
- **AND** it preserves the suite's product verdict and failure details instead of inventing a product score or verdict

#### Scenario: Recover a resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=true` and no recognized quota hold
- **THEN** the repetition follows the existing bounded technical recovery policy rather than being treated as a settled product failure

#### Scenario: Lose a Fly Machine

- **WHEN** repetition 2's Machine is found destroyed or without its checkpoint in a request for three repetitions
- **THEN** repetition 2 is recorded as failed for an infrastructure reason with its streamed evidence, no retry is consumed, and repetition 3 starts in a new Machine

#### Scenario: Relaunch after a pre-suite failure

- **WHEN** repetition 1's first attempt fails because the launcher could not deliver the job to its Machine
- **THEN** the attempt is recorded as failed with its diagnostic, its Machine is destroyed, the claim waits with `infra-error`, and no recovery retry is consumed
- **AND** on the next poll where readiness passes, repetition 1 launches again as a first attempt in a new Machine

#### Scenario: Stop after two pre-suite failures

- **WHEN** the relaunched attempt of the same repetition also fails before the suite starts
- **THEN** the claim stops as for an exhausted recovery and the issue moves to Review with `infra-error` and both diagnostics

#### Scenario: Fail after the suite started

- **WHEN** an attempt fails after the job script wrote the setup-complete marker
- **THEN** the failure follows the ordinary bounded technical recovery policy and is not treated as pre-suite

#### Scenario: Fail a fix attempt before its process starts

- **WHEN** a fix attempt fails with a readiness error raised after the attempt was reserved
- **THEN** its recovery attempt is not consumed and the fix relaunches as the same attempt once readiness passes

_From `specs/factory-claim-lifecycle/spec.md` (MODIFIED Requirements)_

### Requirement: Exempt settled work from closure cancellation

Issue closure SHALL cancel only claims with unfinished execution. A settled fix or feature claim with a recorded pull request SHALL remain eligible for its post-merge sync whether the issue was closed by a human or by the factory after a successful sync, and closure SHALL NOT be reported as a cancellation for such a claim.

#### Scenario: Close a fixed issue by hand before the sync

- **WHEN** a human closes an issue whose fix or feature claim is settled with a merged PR before the factory has synced
- **THEN** the claim is not cancelled and the sync still runs once

#### Scenario: Observe the factory's own closure

- **WHEN** the factory closed the issue after a successful sync and observes the closed issue on a later poll
- **THEN** it records nothing new and posts no cancellation comment

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Comment on feature activity

The factory SHALL comment on the issue when it admits a feature attempt, when it admits a review round (naming the pull request and the comments it will address), when an attempt stops with `needs-input`, fails, is retried, is cancelled, or produces a pull request, and when a review round completes (linking the pull request and summarizing what was changed and answered). The admission comment SHALL include the resolved refs and attempt number and SHALL state whether the attempt starts fresh, resumes at a named step, or continues a prior claim's branch, and SHALL state when a resume point was unavailable and the attempt started fresh. The comment reporting a `needs-input` stop SHALL list the specific questions, summarize the direction drafted so far, and link the pushed branch; for a `preflight` stop it SHALL state that no branch was created and that the next attempt starts fresh. The comment linking a produced pull request SHALL state the number of red, orange, and yellow items of the final classification defined by `factory-feature-execution`, matching the pull request description. Every comment reporting an attempt outcome SHALL state that the attempt ran on the host and that the recorded Runner and Skills commits were not the versions that executed. The acceptance comment posted when a feature claim's inputs are accepted SHALL read `Feature inputs accepted and frozen.` Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

#### Scenario: Admit a resumed attempt

- **WHEN** the factory admits a resumed attempt for a claim that stopped during design
- **THEN** the issue receives one comment naming the refs, the attempt number, and that the attempt resumes at design

#### Scenario: Report a definition stop

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the issue receives one comment listing the questions, summarizing the drafted direction, and linking the pushed branch

#### Scenario: Report flag counts with the pull request

- **WHEN** a feature attempt returns `pull-request` with two red items, three orange items, and twelve yellow items
- **THEN** the comment linking the pull request states two red flags, three orange flags, and twelve yellow items

#### Scenario: Report a fresh start after an unavailable resume point

- **WHEN** a continuing claim's prior branch no longer exists
- **THEN** the outcome comment states that the prior branch was unavailable and the attempt started fresh

#### Scenario: Accept a feature claim's inputs

- **WHEN** the factory accepts and freezes a feature claim's inputs
- **THEN** the acceptance comment reads `Feature inputs accepted and frozen.`

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Map feature outcomes to the board

On `pull-request`, the factory SHALL link the pull request in a comment, move the card to Review, and set `Verdict=pending-human-review`. On `needs-input` from a feature attempt's definition, it SHALL post the reasons, apply the `needs-input` label, leave the card in Running, and release the feature slot. On `needs-input` from a review attempt, it SHALL post the reasons on the issue, apply the `needs-input` label, return the card to Review, restore the verdict it held when the round was admitted, and release the feature slot. On `failed`, it SHALL post the reasons, link the pushed branch or open pull request, move the card to Review, and set `Verdict=failed`. On exhausted recovery, it SHALL move the card to Review with `Verdict=infra-error` and an explanation. While a review attempt runs, the card SHALL be in Running with its verdict cleared. The factory SHALL never assign `passed`, merge, or close the issue as part of an attempt outcome.

#### Scenario: Hand off a feature pull request

- **WHEN** a feature attempt returns `pull-request`
- **THEN** the card moves to Review with `pending-human-review` and the pull request link is on the issue

#### Scenario: Park a feature stopped during definition

- **WHEN** a feature attempt returns `needs-input` during definition
- **THEN** the card stays in Running with the `needs-input` label, and the reasons and pushed branch link are on the issue
- **AND** another eligible feature can be admitted to the feature slot

#### Scenario: Report a failed feature without a pull request

- **WHEN** a feature attempt returns `failed` after the validator stayed red
- **THEN** the card moves to Review with `failed`, and the reasons and the pushed branch link are on the issue

_From `specs/factory-feature-reporting/spec.md` (ADDED Requirements)_

### Requirement: Deliver feature reports durably without duplicates

Feature reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as eval and fix reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a stop comment response

- **WHEN** GitHub accepts the `needs-input` comment but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment

_From `specs/factory-pull-request-lifecycle/spec.md` (ADDED Requirements)_

### Requirement: Sync the working clone and close the issue after merge

For a settled pull-request claim, fix or feature, with a recorded factory PR that has been merged and whose sync has not completed, regardless of the card's current column, the factory SHALL update the operator's configured working clone of that repository on the next successful poll: verify the working tree and index have no changes to tracked files, fetch `main` from the remote into the local `main` branch (refusing when local `main` has diverged or is checked out in any worktree of that clone), verify by dry run that merging `main` into the currently checked-out branch produces no conflicts, and then perform that merge. On success it SHALL close the issue when it is still open so closure automation moves the card to Done. On tracked changes, a diverged local `main`, `main` checked out elsewhere, a predicted conflict, a detached HEAD, or an unreachable clone, it SHALL leave the card where it is, apply the `needs-input` label when the card is not yet Done, comment with the reason, and retry on later polls, clearing the label when the sync succeeds. Apart from fetched refs and objects, it SHALL NOT change the clone's working tree, index, or checked-out branch except by the merge itself. A merged PR whose issue a human already closed SHALL still receive its one sync attempt sequence.

#### Scenario: Merge a fix or feature while the clone is on dev

- **WHEN** a factory PR for a fix or feature claim merges while the working clone has branch `dev` checked out and a clean tree
- **THEN** the factory updates local `main`, merges it into `dev`, closes the issue, and the card moves to Done

#### Scenario: Merge a fix while the clone is dirty

- **WHEN** the working clone has uncommitted changes
- **THEN** the card stays in Review with the `needs-input` label and a comment naming the uncommitted changes
- **AND** the clone's working tree, index, and branches are unchanged

#### Scenario: Predict a conflict

- **WHEN** the dry-run merge reports conflicts
- **THEN** the factory performs no merge, leaves the working tree unchanged, and blocks the card with the conflict reason

#### Scenario: Find local main diverged or checked out elsewhere

- **WHEN** local `main` has commits not on the remote, or `main` is checked out in another worktree of the clone
- **THEN** the factory refuses to update `main`, blocks the card with that reason, and changes no branch

#### Scenario: Merge after a human closed the issue

- **WHEN** a human closed the issue before the factory observed the merged PR and the card is already Done
- **THEN** the factory still performs the sync once and comments on the outcome without relabeling the Done card

#### Scenario: Resolve and retry

- **WHEN** the operator commits or stashes the changes and the next poll's checks pass
- **THEN** the factory completes the merge, removes the `needs-input` label, closes the issue, and the card moves to Done

_From `specs/factory-execution-backends/spec.md` (ADDED Requirements)_

### Requirement: Own every execution through a named backend

Every execution plan SHALL name the execution backend that owns the attempt: `docker` for a Docker sandbox, `host` for a host process, or `fly-machine` for a Fly Machine. The run record SHALL store that name. For every work kind, readiness reported by doctor, ownership verification, probing, termination, result disposal, restart reconciliation, and provenance SHALL be provided by the attempt's backend, and the ownership rules of `factory-claim-lifecycle`, `factory-fix-execution`, and `factory-fly-execution` SHALL continue to apply to their respective mechanisms. Where a backend's surviving execution needs a new local launcher after a controller or launcher restart, that backend SHALL provide the reattachment; a backend whose execution is a local process SHALL resume watching the verified process instead. Selecting a backend SHALL NOT depend on the work kind.

#### Scenario: Record the backend at launch

- **WHEN** a host feature attempt, a Docker fix attempt, and a Fly eval attempt launch
- **THEN** each run record names its backend, `host`, `docker`, and `fly-machine` respectively

#### Scenario: Terminate through the recorded backend

- **WHEN** the factory cancels a running attempt
- **THEN** it verifies ownership and terminates execution through the backend the attempt's plan names, applying that mechanism's ownership rules

#### Scenario: Diagnose each backend in use

- **WHEN** the operator runs doctor with host features, Docker fixes, and Fly evals configured
- **THEN** doctor reports readiness for the host, Docker, and Fly Machine backends


Portion for this task:

- "Configure deployment without Codagent-specific controller code": everything except the
  scenario "Supply the Codagent deployment" (the Codagent example configuration keeps the
  feature kind disabled in this task) and the review-round part of "Remove the feature
  section with feature claims in flight".
- "Expose current operational status": the feature slot line and feature claims; the
  "Inspect a blocked feature" scenario needs a feature stop, which this task does not
  produce, so it is covered only by the kind-generic blocked listing.
- "Comment on feature activity" and "Map feature outcomes to the board": admission (fresh),
  inputs accepted, pull request with flag counts, failed, retried, cancelled, and exhausted
  recovery. Comments and board mapping for `needs-input` stops, resumed or continued
  admissions, fallback-to-fresh reports, and review rounds are outside this task.
- "Bound technical recovery per repetition": the feature kind's pre-suite and exhausted
  recovery behavior through the shared controller; the recovery attempt here relaunches
  fresh.
- "Own every execution through a named backend": a feature run record names `host`.

## Test Plan

- `INT-004` — feature configuration reaches runtime and doctor. Boundary: TOML loading →
  `LocalConfig`/`SharedConfig` → handler registration → `doctor` output. Setup:
  configuration files with and without `[feature]`, with `execution = "docker"` and
  `"fly"`; a stub `agent-runner` on PATH with and without `core/verify-change`; fix targets
  with and without `openspec/`. Action: load configuration; run `doctor`; run one `tick`
  against a stub board; then remove `[feature]` while one feature attempt is running and
  another feature claim is settled with an open pull request, and run further ticks.
  Assertions: Docker or Fly for features fails loading with the unsupported mode named;
  without `[feature]` no feature is handed off or admitted and bug and eval behavior is
  unchanged; doctor shows a `feature-host` group that fails naming `core/verify-change` when
  the stub Runner lacks it and passes otherwise; targets without `openspec/` are
  informational; other kinds' readiness is reported independently; after `[feature]` is
  removed, the running attempt is still supervised and its result reported, the settled
  claim still gets review-round admission, merge sync, and cleanup, and no new Feature card
  is handed off or admitted. File: `tests/integration/test_feature_config.py`.
  Portion for this task: every assertion except "the settled claim still gets review-round
  admission" after `[feature]` is removed. Review-round admission for feature claims
  (feature slot, feature limits, feature comments) is not implemented here; leave that one
  assertion out of the test (do not add a skipped placeholder), and keep the merge sync and
  cleanup assertions for the settled claim.
- `INT-005` — feature handoff, eligibility, and routing retype. Boundary: runtime Ready
  handoff, router, stub GitHub client. Setup: a stub board with Feature cards by writer and
  non-writer authors, in configured and unconfigured repositories, with Priority values; a
  maintainer's bug auto-routed to Ready, untouched, then retyped to Feature; a human-edited
  card retyped to Feature. Action: route events and run handoff and admission for one
  cycle. Assertions: only writer-authored Features in fix targets get `Owner=factory`;
  non-writers get one explanation and no ownership; admission ranks by Priority then newest
  created; the retyped untouched card returns to Backlog and is not admitted; the
  human-edited card keeps its values; a Feature card never receives `Owner=factory` from
  routing. File: `tests/integration/test_feature_intake.py`.
- `INT-010` — retention for feature claims. Boundary: retention sweep, store, evidence
  directories. Setup: a Done feature claim past the retention period with attempt evidence,
  a post-run audit directory, and an outcome file; a feature claim with a pending merge
  sync. Action: run the retention sweep. Assertions: the feature claim is pruned with the
  pull-request rules (logs, session state, audit directory, agent output removed;
  `feature-outcome.json` and `input/` kept); the claim with a pending sync is not pruned.
  File: extend `tests/integration/test_retention.py`.
- `E2E-001` — a feature goes from Ready to a merged, cleaned-up pull request. Surface: the
  `agent-factory` CLI (`tick`, `status`) with the resident's normal cycle. Setup: the
  `tests/e2e/test_fix_cycle.py` harness (stub `gh`, stub board, stub host `agent-runner`
  that records its argv and writes `feature-outcome.json` = `pull-request` with tier counts
  and a pull request reference); configuration with `[feature]`; one writer-authored
  Feature card in Ready in a fix target. Journey: tick → handoff and admission → host launch
  → attempt completes → tick → merge the stub pull request → tick → move card to Done →
  tick. Assertions: `Owner=factory` set; the attempt runs in the feature slot with every
  packaged workflow staged in the clone and the feature params passed; the card moves to
  Review with `pending-human-review` and one comment states the flag counts; status shows
  the feature slot; after merge the working clone is synced and the issue closed; after
  Done the clones and credential copy are removed and evidence remains. File:
  `tests/e2e/test_feature_cycle.py`.

## Done When

- `FEATURE` is defined and registered; feature configuration loads with the defaults above
  and rejects non-host execution; `config/codagent.toml` still has no `[feature]` section.
- A feature run record names the `host` backend, the Runner path and version, and the
  session directory, and contains no credential.
- INT-004 (the portion above), INT-005, INT-010, and E2E-001 pass, and the existing fix and eval suites still
  pass.
- The three docs describe feature setup, the handoff gesture, doctor's `feature-host`
  group, and feature status.
- `uv run pytest`, `uv run ruff format --check . && uv run ruff check .`, and `uv run pyright`
  pass.
