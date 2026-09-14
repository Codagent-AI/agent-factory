# factory-routing Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
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

