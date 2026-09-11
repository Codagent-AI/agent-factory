# Task: Deliver configured GitHub intake and routing

## Goal

Make marked eval issues and ordinary work enter the configured Project through one authenticated, repeatable routing implementation that works independently of the Mac worker.

## Background

All repository-relative code paths below are in `agent-factory` unless a sibling repository is explicitly named. Planning sources are `openspec/changes/iteration-1/proposal.md`, `design.md`, the cited files under `specs/`, and `test-plan.md` in that same change directory. Read the relevant design sections and the approved test plan; the excerpts below preserve the requirements and assigned automated obligations verbatim.

The repository begins as a Python 3.12/uv scaffold: `src/agent_factory/__init__.py`, `pyproject.toml`, `README.md`, and an empty `tests/` tree. The package areas named below are intended implementation locations, not claims that an API already exists. Use small protocols and dataclasses with static registration for only `eval` and `and-scene`. Preserve GitHub intent, SQLite execution history, and suite-owned evidence as separate authorities. Keep organization names, logical field mappings, local paths, and defaults configurable. Do not add more work kinds, suites, workers, queue/storage providers, a dynamic plugin loader, a generic outbox, or another evaluator.

The App, native Eval type, and board were already provisioned and confirmed; reuse the records in `openspec/changes/iteration-1/setup/`. That prior confirmation satisfies the pre-controller board-layout gate and is not evidence of implementation acceptance. Public configuration and documentation must use portable paths and contain no private credentials.

Read the design sections “Configuration and credentials”, “GitHub routing and board representation”, and “Migration and Deployment”. Deliver configuration loading and validation in `src/agent_factory/config.py`, App-backed GitHub access in `src/agent_factory/github.py`, and routing in `src/agent_factory/routing.py`; equivalent cohesive package directories are acceptable. Add a versioned Codagent deployment example at `config/codagent.toml` and a portable local example at `config/local.example.toml`. Shared configuration includes sources, markers, Project/field/option mappings, suite/input defaults, and an explicit full deployed harness SHA; local configuration holds paths, schedule, limits, and credential-file locations. Validate that configured IDs identify the expected fields/options. Do not silently choose a local HEAD or remote branch for the harness.

If concrete suite integration is not yet complete, record the initial full harness SHA as a provisional execution pin and identify its known compatibility gaps in `docs/github-setup.md`; routing completion does not establish suite readiness. Final suite integration must update `config/codagent.toml` to the integrated immutable harness revision containing the required companion behavior, as specified by the approved design’s “Configuration and credentials” and “Worktrees and suite invocation” sections. Keep the value a real full commit SHA, never a placeholder or mutable ref.

Use `gh api` for REST/GraphQL calls and pagination, with structured JSON through stdin. Sign an RS256 App JWT using OpenSSL and the private key, exchange it for an installation token, and refresh before expiry. Keep token values in memory and in the `gh` child environment only, never argv, JSON request bodies, logs, or suite environments. Expose native issue Type from issue data in the GitHub client; do not depend on a Project field/group connection exposing it. The omission recorded in `openspec/changes/iteration-1/setup/project-board.md` is legitimate, as explained in `openspec/changes/iteration-1/design.md` under “GitHub routing and board representation”. `openspec/changes/iteration-1/setup/github_api.py` demonstrates provisioning access but is not production architecture. Read the non-secret deployment records in `setup/github-app.md` and `setup/project-identifiers.toml` to reuse the App and mappings; do not commit the referenced key or copy private credential values.

Deliver `.github/workflows/route-work.yml` as the reusable workflow and thin `.github/workflows/factory-routing.yml` callers in the sibling `agent-evals`, `agent-runner`, `agent-skills`, `agent-validator`, and `agent-plugin` repositories under `/Users/paul/codagent/`. The initial eval source is only `Codagent-AI/agent-evals`; the five listed repositories are the configurable general-work sources. Add the regular Markdown template at `agent-evals/.github/ISSUE_TEMPLATE/eval-request.md`, setting native Type Eval and the request label and supplying a fenced TOML `eval` block. Supply idempotent label setup for `eval-request` and red `needs-input` and document it in `docs/github-setup.md`. Keep routing implementation/configuration in agent-factory rather than duplicating it in callers. Include creation and closure handling for general issues and PRs; an eval match takes precedence over Backlog intake.

Read current source-item state and verify the issue author's effective collaborator permission; only write, maintain, or admin authorizes eval ownership and Ready. Invalid settings still route for correction. An outsider goes to Backlog without ownership; unavailable permission never authorizes execution. Routing recognizes markers and never interprets arbitrary prose as assignment. Implement idempotent Project insertion and a durable audit-trail receipt with stable marker/destination, initial field values, and initialization progress. Serialize routing per source item, set ownership before Ready, and preserve later human changes after partial or repeated delivery. Closure moves associated cards to Done without reinitializing them.

Execute trusted workflow code/configuration from an explicitly pinned factory revision. Do not run contributor PR code with App credentials; use the base-repository event context when PR routing needs write credentials. Callers explicitly pass the App secret. Publish the reusable workflow at its referenced revision before deploying callers, and use the normal repository deployment process/default-branch issue events; this is deployment sequencing, not permission to merge arbitrary code. Deliver reviewable companion changes and precise rollout instructions. Do not mutate live resources merely to run automated tests.

Scope of shared requirements: own the deployment schema, client API/credential boundary, template, routing, labels, and GitHub rollout assets. The execution-admission half of the writer-permission requirement is included below to keep the authorization contract explicit; expose the same effective-permission query for the controller without adding local execution to routing. Local service installation and evaluation execution are not part of this routing outcome.

## Spec

The following requirement blocks are copied verbatim from the approved specifications. For shared requirements, the Background states this delivery unit’s portion; retain the complete scenario semantics.

Source: `openspec/changes/iteration-1/specs/factory-eval-intake/spec.md`.

### Requirement: Create explicitly assigned evaluation requests

The change SHALL provide a regular Markdown issue template in `Codagent-AI/agent-evals`. The template SHALL set the native organizational issue type to `Eval`, apply the `eval-request` label, and pre-fill a description and fenced `eval` configuration block. Creating the request by an author with write, maintain, or admin access to the source repository SHALL be sufficient to assign it to the factory and queue it; no manual assignment or move to Ready SHALL be required. The Project SHALL display the native issue Type rather than a duplicate custom Type field.

#### Scenario: Create an evaluation request

- **WHEN** a user with write, maintain, or admin access to the source repository creates an issue using the evaluation request template
- **THEN** the issue has native Type `Eval` and the `eval-request` label
- **AND** routing adds it to the configured Project with `Owner=factory` and `Status=Ready` without another human action

### Requirement: Route requests through shared configuration

Routing rules and their implementation SHALL be maintained in `agent-factory` and invoked through a reusable GitHub Actions workflow. Source repositories SHALL use small caller workflows. Rules SHALL configure source repositories, request markers and work kinds, destination Projects, and initial Project fields. The initial eval rule SHALL route `agent-evals` evaluation requests to the shared Codagent Project. Adding a source repository or routing another work kind SHALL reuse this routing behavior through configuration; iteration 1 SHALL execute only eval work.

Routing SHALL add an issue to its destination Project if absent and initialize fields once. Repeated delivery SHALL NOT reset work in progress or overwrite subsequent human field changes. Routing SHALL recognize explicit request markers without requiring a valid eval block or inferring assignment from arbitrary issue prose.

#### Scenario: Route while local execution is unavailable

- **WHEN** an eval request from an author with the required repository access is created while the Mac mini is offline, factory execution is paused, or the admission window is closed
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

### Requirement: Restrict automatic execution to repository writers

Routing and execution admission SHALL verify that the issue author has effective write, maintain, or admin permission on its source repository. Organization membership or the presence of the request label alone SHALL NOT satisfy this check. An eval request from an author without sufficient access SHALL enter Backlog without factory assignment and SHALL NOT execute. Failure to establish the author's permission SHALL NOT be treated as authorization.

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an issue from the eval template
- **THEN** the request enters the Project in Backlog without factory ownership
- **AND** no evaluation is admitted even though the template applied the request label

#### Scenario: Recheck permission at execution admission

- **WHEN** a Ready card has the eval marker but its author lacks the required repository access
- **THEN** the factory does not accept it for execution based only on its label or board fields

Source: `openspec/changes/iteration-1/specs/factory-operations/spec.md`.

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

## Test Plan

No complete cross-boundary `INT-*` or `E2E-*` obligation is assigned to this routing-only outcome. Implement focused routing/client contract tests now for duplicate and interrupted initialization, lost receipt responses, later field edits, current closure state, eval precedence, general issue/PR handling, native type/labels, denied or unknown author permission, alternate organization mappings, pagination, and token refresh/redaction. Use actual REST/GraphQL payload shapes and assert structured request semantics. Verify caller/reusable workflow event and secret wiring statically without executing a live Actions run. The complete routing-plus-controller reporting boundary remains a required integration obligation in the index.

Use implementation-time TDD for the copied specification scenarios within the scope stated above. Keep unit cases close to the behavior rather than reproducing every case at every test layer. Use isolated roots, temporary SQLite/files, and controlled external responses; automated verification must not read the operator's database, live queue, or private credentials. Run the relevant collected pytest tests and the repository's Ruff format/lint and strict Pyright checks. Register any markers in `pyproject.toml`; pytest exit code 5 or an unexecuted required platform test does not count as passing.

Implementors do not execute `AT-001`, `AT-002`, or `HT-001`. Preserve their requirements in `openspec/changes/iteration-1/test-plan.md` for the separate acceptance stage: real issue-event routing, one real suite repetition and launchd restart, and the operator's board-drag-to-API observation. Controlled automated evidence cannot satisfy those live boundaries. Do not change the approved definition artifacts or weaken their testing obligations.


## Done When

- The shared implementation and thin callers route evals and general work, initialize fields once, handle closure, and preserve human edits under controlled API tests.
- The regular Markdown eval template, both label definitions/setup, provisioned-ID configuration, and GitHub deployment documentation are delivered in their proper repositories without new controller-specific organization constants.
- Both local and Actions authentication paths keep control credentials outside logs, argv, and evaluated workloads; unverified authors never gain eval authorization.
- All copied routing/configuration scenarios within this scope have meaningful passing automated coverage. Companion changes and revision-pinning/default-branch rollout requirements are recorded for review; live acceptance is not claimed.
