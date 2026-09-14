# Task: Bug routing, doctor and status for both kinds, documentation, and GitHub provisioning

## Goal

Route Bug-typed issues from configured source repositories to the factory (with writer verification, the `factory-hold` bypass, and eval precedence), extend `doctor` and `status` to diagnose and describe both work kinds, document installation, operation, capacity, the blocked-bug loop, the merge sync, and rollback, and provision the GitHub side: labels, the "Bug (tracking only)" template, the fix credential guidance, rulesets, and the caller-workflow revision bump.

## Background

All paths are in `agent-factory` unless a sibling repository is named. Planning sources are `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "Routing", "Doctor and status", "Harness branch" for the doctor line, "Decisions: Tracking-only template" and "Docker memory, not host memory", "Risks: Ruleset exemption", and "Migration Plan"), the specs under `specs/`, and `test-plan.md`.

Preconditions in the repository: `Router` in `src/agent_factory/routing.py` routes the eval rule with receipts over `GitHubRoutingClient`; `SharedConfig.routing` in `src/agent_factory/config.py` has `eval_source`, `general_sources`, `eval_label`, `eval_type`; `.github/workflows/route-work.yml` is the reusable workflow the five caller repositories pin by `factory-revision`; `docs/github-setup.md` documents labels and callers. The fix work kind exists: `FixHandler` under `src/agent_factory/work_kinds/fix/` exposes `readiness(local, shared)` diagnostics (credential file shape, contract line at the Runner branch head, mirrors), `SharedConfig.fix` and `LocalConfig` carry the fix targets, branches, defaults, contract, limits, window, `memory_reservation_gib`, `credentials.fix_environment`, and `repositories.working_clones`; `EvalConfig.harness_ref` replaced `harness_sha` and loading fails naming `harness_ref` when the old key remains; the store keeps per-kind runs, `blocked` claims, `readiness:<kind>` and `quota:<provider>` settings, a memory hold reason, and `reporting.sync` on fix claims; `operations.doctor` and `operations.status` exist with eval-only output.

### Routing

`SharedConfig.routing` gains `bug_type` (default `Bug`) and `hold_label` (default `factory-hold`); add them to `config/codagent.toml`. `Router.route` gains a second rule after the eval rule:

```
is_bug = repo in general_sources and issue_type == routing.bug_type
         and item is an issue (payload has no pull_request)
if is_bug and routing.hold_label in labels: initialize owner=human, status=backlog
elif is_bug and permission in writers:      initialize owner=factory, status=ready
```

The eval rule takes precedence when both match. A non-writer or a failed or unknown permission lookup leaves the bug in Backlog without ownership. Receipts and re-delivery behavior are unchanged; repeated delivery preserves later human field changes. `SourceItem` needs the issue's labels and a pull-request flag if it does not already carry them; extend `GitHubClient.get_source_item` accordingly. Routing acts only on delivered events, so existing bugs are untouched until an event fires.

### Doctor

Add a `fix` heading, distinct from shared and eval diagnostics, containing: each mirror fetchable; each configured working clone exists and is a Git repository; `credentials.fix_environment` is owner-readable, contains exactly `GH_TOKEN` and nothing else, authenticates (`gh api user` with that token), is not the App identity, is not an organization admin (warn), and can read each target repository; the Runner branch head contains `core/factory-fix-v1.0.yaml` with the expected contract line; Docker memory allowance (`docker info --format '{{.MemTotal}}'`) is at least one reservation. Reuse `FixHandler.readiness` for the checks it already implements and add the network-backed identity checks here. Doctor must not launch anything or repair configuration. Shared config with a leftover `harness_sha` fails configuration loading with a message naming `harness_ref`, and doctor surfaces that message.

### Status

Print one block per kind: slot holder (claim, unit, attempt, progress) or `free`; why the kind is waiting (window, pause, per-kind readiness, provider hold with the providers it affects, memory, disk); blocked fix claims with their decline reason; pending merge syncs with their last failure reason; plus the global lines printed today (pause, next permitted start, reporting and cleanup progress). A Codex quota hold must show as not blocking fix admission when the fix roles use another provider.

### Documentation

- `docs/installation.md`: mirrors and working clones, Docker startup and memory allowance, the fix credential file and its containment (exactly one `GH_TOKEN`, owner-only), the companion workflow and its contract version, capacity guidance stating the free disk and Docker memory the mini needs to run one eval and one fix concurrently, updating local TOML for fix settings, and rollback (pause, reinstall the previous tag, restore `harness_sha`, copy `state.sqlite3.v3.bak` back over `state.sqlite3`, noting that claims created after the upgrade are lost by that copy).
- `docs/operations.md`: the fix work kind end to end for an operator: admission comment and Refs, the blocked-bug loop (label, writer comments, drag to Ready), reviewing a factory fix PR, the merge sync and each block reason with how to resolve it, evidence locations for fix attempts, storage growth from clones and per-run images, and per-kind `status` output.
- `docs/github-setup.md`: the `factory-hold` label (create idempotently in each of the five repositories beside `eval-request` and `needs-input`), the "Bug (tracking only)" issue template that sets the Bug type and pre-applies `factory-hold`, the fine-grained credential (Contents, Pull requests, Issues on the five repositories; no workflow, administration, or Project access; preferably a non-admin machine user), the ruleset on each target repository requiring a pull request for `main` with no bypass for the credential's owner, and bumping `FACTORY_REVISION` in the five caller workflows after publishing.
- `README.md`: mention the fix work kind at the level it mentions evals.
- Public examples use portable paths and no credentials.

### Provisioning artifacts

Add `.github/ISSUE_TEMPLATE/bug-tracking-only.md` (or YAML form) to this repository as the template to copy to each source repository, with the Bug type and the `factory-hold` label pre-set, and a script or documented `gh` commands in `docs/github-setup.md` for labels, templates, and rulesets. Do not perform live provisioning or edit other repositories in this task; the acceptance run does that with the operator's credentials.

Constraints: no changes to admission, handler, or sync logic beyond reading their recorded state; no new work kinds. Keep `pyright` strict and `ruff` clean.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-routing/spec.md`.

### Requirement: Route requests through shared configuration

Routing rules and their implementation SHALL be maintained in `agent-factory` and invoked through a reusable GitHub Actions workflow. Source repositories SHALL use small caller workflows. Rules SHALL configure source repositories, request markers and native issue types per work kind, bypass markers, destination Projects, and initial Project fields. The eval rule SHALL route `agent-evals` evaluation requests to the shared Codagent Project; the bug rule SHALL route Bug-typed issues from every configured source repository. Adding a source repository or routing another work kind SHALL reuse this routing behavior through configuration.

Routing SHALL add an issue to its destination Project if absent and initialize fields once. For an authorized explicitly marked eval request, routing SHALL set the native issue Type to the configured eval type before initializing its factory fields. Repeated delivery SHALL NOT reset work in progress or overwrite subsequent human field changes. Routing SHALL recognize explicit request markers and native issue types without requiring a valid eval block or inferring assignment from arbitrary issue prose. Routing SHALL act only on delivered issue events; it SHALL NOT retroactively route issues that existed before a rule was deployed.

#### Scenario: Route while local execution is unavailable

- **WHEN** an eval request or bug report from an author with the required repository access is created while the Mac mini is offline, factory execution is paused, or an admission window is closed
- **THEN** GitHub Actions routes it to the configured Project and initializes its fields
- **AND** routing does not require the local service or its database

#### Scenario: Deliver the same routing event again

- **WHEN** routing is repeated for a request whose initial routing completed
- **THEN** the issue is not added as a duplicate Project item
- **AND** its current Owner and Status are preserved

#### Scenario: Route a request with invalid settings

- **WHEN** an explicitly marked eval request from an author with the required repository access contains invalid execution settings
- **THEN** routing still places it in Ready with Owner factory
- **AND** the factory validates the settings before admitting execution

#### Scenario: Route an eval request without a native type

- **WHEN** an authorized explicitly marked eval request has no native issue Type
- **THEN** routing assigns the configured native eval type and initializes `Owner=factory` and `Status=Ready`
- **AND** an unauthorized request does not cause the type mutation

#### Scenario: Deploy a new rule with existing issues

- **WHEN** the bug rule is deployed while Bug-typed issues already exist in a configured source repository
- **THEN** those issues are not routed or re-assigned until a routing event for them is delivered
- **AND** a human can still hand one to the factory by setting `Owner=factory` and moving it to Ready

### Requirement: Restrict factory assignment to repository writers

Routing SHALL verify that the issue author has effective write, maintain, or admin permission on its source repository before assigning factory ownership. Organization membership, the presence of a request label, or a native issue type alone SHALL NOT satisfy this check. A request from an author without sufficient access SHALL enter Backlog without factory assignment. Failure to establish the author's permission SHALL NOT be treated as authorization. Each work kind's intake SHALL re-verify this permission at execution admission.

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an eval request or a Bug-typed issue
- **THEN** the request enters the Project in Backlog without factory ownership
- **AND** no execution is admitted even though the marker or type is present

#### Scenario: Fail to establish permission

- **WHEN** the permission lookup for an author fails or returns an unknown value
- **THEN** routing treats the author as unauthorized and places the issue in Backlog without factory ownership

### Requirement: Route bug reports to the factory

For an open issue whose native Type is the configured bug type in a configured source repository, routing SHALL initialize `Owner=factory` and `Status=Ready` when the author has the required repository access and the issue carries no bypass marker. When the configured bypass marker (`factory-hold` in the Codagent deployment) is present at routing time, routing SHALL initialize `Owner=human` and `Status=Backlog` so the bug is tracked without factory work. Pull requests SHALL NOT be routed as bugs. When an issue matches both the eval marker and the bug type, the eval rule SHALL take precedence. Bug routing SHALL NOT require a template or fenced configuration block. Because the bypass marker must be present when the creation event is delivered, each configured source repository SHALL provide a "Bug (tracking only)" issue template that sets the bug type and pre-applies the bypass label.

#### Scenario: File a bug as a repository writer

- **WHEN** a user with write, maintain, or admin access creates an issue with native Type Bug in a configured source repository
- **THEN** routing adds it to the Project with `Owner=factory` and `Status=Ready` without another human action

#### Scenario: File a bug for tracking only

- **WHEN** a writer creates a Bug-typed issue from the "Bug (tracking only)" template, or otherwise with the configured bypass label already applied
- **THEN** routing adds it to the Project with `Owner=human` and `Status=Backlog`
- **AND** the factory does not pick it up unless a human later sets `Owner=factory` and moves it to Ready

#### Scenario: Hold a routed bug by hand

- **WHEN** a human changes a routed bug's Owner to human or moves it to Backlog before the factory admits it
- **THEN** repeated routing preserves that change and the factory does not admit the bug

#### Scenario: Receive an issue matching both rules

- **WHEN** an issue in `agent-evals` carries the eval request marker and has native Type Bug
- **THEN** routing applies the eval rule, including setting the native eval type
- **AND** the bug rule does not apply

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-operations/spec.md`.

### Requirement: Configure deployment without Codagent-specific controller code

The factory SHALL use TOML configuration for GitHub organization and repository identities, Project destinations, routing rules including native issue types and bypass markers per work kind, request labels, field and option mappings, repository locations, evaluation defaults, fix defaults, schedule, supervision limits, minimum free disk space, memory reservation, and local storage paths. Fix configuration SHALL include, per target repository, the mirror location and the operator's working clone path; and globally the branch names for the target, Runner, and Skills (default `main`), fix role profiles, fix limits, the fix admission window, and the fix credential file location. It SHALL supply Codagent as an example deployment configuration. The controller SHALL use configured mappings for logical queue states rather than require literal column names such as Ready or Review.

Another organization SHALL be able to deploy the supported factory behavior using its own configuration and credentials without modifying core controller code. Suite-specific and workflow-specific repository and executable locations SHALL be supplied to the relevant handler rather than embedded as Codagent or personal-machine assumptions in the controller. GitHub Projects, SQLite, one worker, and the Mac launchd service SHALL remain the supported initial deployment choices; this requirement does not introduce interchangeable providers.

#### Scenario: Configure another organization's Project

- **WHEN** an operator supplies a different organization, repositories, Project destination, request label, and mappings for queued and review states
- **THEN** routing and execution use those configured identities and mappings without requiring Codagent names or controller changes

#### Scenario: Supply the Codagent deployment

- **WHEN** an operator uses the supplied Codagent example configuration
- **THEN** it establishes the Eval and Bug behavior and the field names, options, and defaults described by the active specifications

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

#### Scenario: Tick while execution is disallowed

- **WHEN** the operator runs tick outside a kind's window, while paused, or while that kind's slot is occupied
- **THEN** tick does not bypass the blocking condition or start overlapping execution
- **AND** its cycle can still reconcile existing work, syncs, and reporting

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees and clones, target repository mirrors, and attempt artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations. The operator's working clones used by the merge sync SHALL be outside the root and are never created by the factory.

Evidence and candidate outputs SHALL be retained until manual cleanup. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, state the disk and memory the machine needs to run one eval and one fix concurrently, and perform operator-managed cleanup while preserving work still needed for execution, recovery, or human review. Automatic evidence and candidate-output pruning is outside this change; factory-owned worktrees and clones follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees and clones, mirrors, and artifacts
- **AND** commands and result reports identify the actual paths in use

### Requirement: Provision the initial GitHub deployment

The change SHALL include the GitHub setup for the Codagent example deployment: an organization-level Codagent Project, native Eval issue type, use of the native Bug issue type, `eval-request`, red `needs-input`, and `factory-hold` labels in each configured source repository, regular Markdown eval issue template, a "Bug (tracking only)" issue template in each configured source repository that sets the Bug type and pre-applies `factory-hold`, shared routing rules and reusable workflow, source-repository caller workflows, required permissions, general issue/PR auto-add, and closure automation. For the fix kind it SHALL additionally provision a fine-grained repository credential with Contents, Pull requests, and Issues access to the target repositories and no workflow, administration, or Project access, preferably on a machine user, and a ruleset on each target repository requiring a pull request for changes to `main` with no bypass for the credential's owner.

The board SHALL have Backlog, Ready, Running, Review, and Done Status columns, horizontal groups by native issue Type, no field-based sorting, and views for the eval queue and active factory work. It SHALL expose configured Owner, Refs, and Verdict fields and visible attention labels. Verdict options SHALL support `pending-human-review`, `failed`, `quota-deferred`, and `infra-error`; the factory SHALL never assign `passed`.

The initial eval request source SHALL be `Codagent-AI/agent-evals`. General work from the configured Codagent repositories SHALL enter Backlog; eval and bug routing SHALL initialize factory ownership and Ready for authors with the required repository access as defined in `factory-routing`, without a general auto-add rule undoing that routing. Other authors' requests SHALL enter Backlog without factory assignment. The initial general-work and bug repository set SHALL cover agent-runner, agent-skills, agent-validator, agent-plugin, and agent-evals, and SHALL be configurable. Issue/PR closure automation SHALL move associated cards to Done.

Routing and the local controller SHALL authenticate with an organization-owned GitHub App with organization Projects read/write, repository Issues read/write, and Contents, Pull requests, and Metadata read access at minimum. Suite and fix PR operations SHALL use their separate credentials. Installation SHALL cover the configured repositories. App private keys and the fix credential SHALL remain outside version control, with local owner-only file access and Actions secret storage for caller workflows.

#### Scenario: Add general work

- **WHEN** an ordinary issue or PR is created in a configured source repository without the eval request marker or Bug type
- **THEN** it is added to the shared Project in Backlog without becoming a factory request

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

#### Scenario: Deploy a shared configuration change

- **WHEN** the operator explicitly updates the local installation and routing workflow pins to the intended factory revision
- **THEN** subsequent routing and new claims use that revision's shared deployment configuration
- **AND** existing claims retain frozen inputs and already-running attempts retain their execution configuration

#### Scenario: Migrate a pinned harness configuration

- **WHEN** the installed configuration still contains a harness commit pin
- **THEN** the factory reports the obsolete setting at startup and in doctor instead of silently ignoring it

## Test Plan

- `INT-002` (Bug routing rule against the controlled GitHub API): `Router` with the existing in-memory `GitHubRoutingClient` stub and the shared configuration parser; configuration with `bug_type = "Bug"` and `hold_label = "factory-hold"`; source items for a writer's Bug, a non-writer's Bug, a Bug carrying the hold label, a pull request typed Bug, an `agent-evals` issue with both the eval label and Bug type, and a permission lookup that fails. Route each event, then re-route the writer's Bug after a human changed Owner to human. Assert writer Bug → `Owner=factory`, `Status=Ready`, receipt written; non-writer or failed lookup → Backlog without ownership; hold label → `Owner=human`, Backlog; PR → not routed as a bug; eval-labelled Bug → eval rule applied including the native type mutation and no bug initialization; re-delivery preserves the human's Owner change; no existing issue is touched without an event. Execution: extend `tests/integration/test_configured_routing.py`; `uv run pytest`.
- `INT-007` (Doctor and status for both kinds): `operations.doctor` and `operations.status` over a temporary configuration, a real Git checkout standing in for Runner, and the store. Setup: a Runner checkout whose branch head contains `workflows/core/factory-fix-v1.0.yaml` with the contract line and a second without it; fix env files that are valid, missing `GH_TOKEN`, carrying an extra variable, and carrying the App installation token's value; a Runner checkout containing a `.sandbox-secrets.env`; a shared TOML retaining `harness_sha`; a store with an eval run active, a fix claim blocked, a pending sync failure, and a Codex quota hold with fix roles on Cursor. Assert doctor reports the contract check as available or names the missing line; rejects the env files with an extra variable or the App token as readiness failures; the launcher's plan passes `--no-default-secrets` and an env file containing only `GH_TOKEN` even when the checkout has a `.sandbox-secrets.env`; fails configuration loading naming `harness_ref` when `harness_sha` remains; eval and fix diagnostics are listed separately. Status prints the eval slot holder, the fix slot as free, the blocked claim with its reason, the pending sync with its last failure, and shows the Codex hold as not blocking fix admission. Network-backed identity checks (`gh api user`, org admin, target reachability) are exercised through a controlled `gh` stub. Execution: extend `tests/integration/test_cli_operations.py`; `uv run pytest`.

## Done When

- `Router.route` implements the bug rule with the hold-label bypass, writer verification, PR exclusion, and eval precedence; receipts and re-delivery are unchanged; `config/codagent.toml` carries `bug_type` and `hold_label`.
- `doctor` prints a separate `fix` section with mirrors, working clones, credential shape and identity checks, contract line, and Docker memory allowance; `harness_sha` leftover fails loading naming `harness_ref`; nothing is launched or repaired.
- `status` prints one block per kind with slot holder or `free`, waiting reason, blocked claims with decline reason, pending syncs with last failure, and the existing global lines; provider holds show which kinds they affect.
- `docs/installation.md`, `docs/operations.md`, `docs/github-setup.md`, and `README.md` cover the fix kind, capacity, credential containment, ruleset, template, label, caller revision bump, blocked loop, merge sync, and rollback; the "Bug (tracking only)" template and idempotent label and ruleset commands are in the repository; examples are portable.
- INT-002 and INT-007 pass; `uv run pytest`, `uv run ruff check .`, and `uv run pyright` pass; existing tests still pass.
