# factory-routing Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
### Requirement: Route requests through shared configuration

Routing rules and their implementation SHALL be maintained in `agent-factory` and invoked through a reusable GitHub Actions workflow. Source repositories SHALL use small caller workflows that subscribe to issue creation, reopening, editing, closure, label changes, and native issue type changes, so a type set after an issue is created is routed when it is set. Rules SHALL configure source repositories, request markers and native issue types per work kind, destination Projects, and initial Project fields. The eval rule SHALL route `agent-evals` evaluation requests to the shared Codagent Project; every other issue from a configured source repository, Bug-typed issues included, SHALL be routed as general work. Adding a source repository or routing another work kind SHALL reuse this routing behavior through configuration.

Routing SHALL add an issue to its destination Project if absent and initialize fields once. For an authorized explicitly marked eval request, routing SHALL set the native issue Type to the configured eval type before initializing its factory fields. Repeated delivery SHALL NOT reset work in progress or overwrite subsequent human field changes. Routing SHALL recognize explicit request markers and native issue types without requiring a valid eval block or inferring assignment from arbitrary issue prose. Routing SHALL act only on delivered issue events; it SHALL NOT retroactively route issues that existed before a rule was deployed.

#### Scenario: Route while local execution is unavailable

- **WHEN** an eval request from an author with the required repository access, or any other issue, is created while the Mac mini is offline, factory execution is paused, or an admission window is closed
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

- **WHEN** a routing rule is deployed while matching issues already exist in a configured source repository
- **THEN** those issues are not routed or re-assigned until a routing event for them is delivered
- **AND** a human can still hand one to the factory by moving it to Ready, after which the factory poll sets `Owner=factory`

### Requirement: Restrict factory assignment to repository writers

Routing SHALL verify that the issue author has effective write, maintain, or admin permission on its source repository before assigning factory ownership to an eval request. Organization membership, the presence of a request label, or a native issue type alone SHALL NOT satisfy this check. A request from an author without sufficient access SHALL enter Backlog without factory assignment. Failure to establish the author's permission SHALL NOT be treated as authorization. Each work kind's intake SHALL re-verify this permission at execution admission.

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an eval request
- **THEN** the request enters the Project in Backlog without factory ownership
- **AND** no execution is admitted even though the marker is present

#### Scenario: Fail to establish permission

- **WHEN** the permission lookup for an author fails or returns an unknown value
- **THEN** routing treats the author as unauthorized and places the issue in Backlog without factory ownership

### Requirement: Never route bugs to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured bug type, whatever the author's repository role or labels. Such an issue SHALL be routed as an untyped issue is: it enters Backlog. When the bug type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule. Routing SHALL recognize no bypass marker for bugs; creating a Bug SHALL NOT start a fix. The only path by which a bug becomes factory work SHALL be the Ready handoff: a writer moves its card to Ready, after which the factory poll verifies the author's permission and sets `Owner=factory`. When an issue matches both the eval marker and the bug type, the eval rule SHALL take precedence.

#### Scenario: File a bug as a repository writer

- **WHEN** a user with the write, maintain, or admin role creates an issue with native Type Bug in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Set the bug type after creation

- **WHEN** an issue routed to Backlog without a native type later gets the Bug type
- **THEN** the type-change event leaves it in Backlog without factory ownership

#### Scenario: Hand a bug to the factory

- **WHEN** a writer moves a Backlog bug's card to Ready
- **THEN** repeated routing preserves that change
- **AND** the factory poll sets `Owner=factory` and admits it under the fix intake rules

#### Scenario: Receive an issue matching both rules

- **WHEN** an issue in `agent-evals` carries the eval request marker and has native Type Bug
- **THEN** routing applies the eval rule, including setting the native eval type

### Requirement: Never route features to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured feature type, whatever the author's repository role. Such an issue SHALL be routed as an untyped issue is. When the feature type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule: a card whose Owner and Status still hold the values routing last initialized SHALL return to Backlog, and a card a human has edited since SHALL keep its values. The only path by which a feature becomes factory work SHALL be the Ready handoff defined by `factory-feature-intake`.

#### Scenario: File a feature as a maintainer

- **WHEN** a user with the maintain or admin role creates an issue with native Type Feature in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Retype an edited card as a feature

- **WHEN** a human changed a routed card's Status or Owner and its native Type is then changed to Feature
- **THEN** routing leaves the card's Owner and Status unchanged

### Requirement: Never route tasks to the factory

Routing SHALL NOT initialize `Owner=factory` or `Status=Ready` for an issue whose native Type is the configured task type (`[routing] task_type`, default `Task`), whatever the author's repository role. Such an issue SHALL be routed as an untyped issue is. When the task type is set after the issue was first routed, the type-change event SHALL apply the untyped-issue rule: a card whose Owner and Status still hold the values routing last initialized SHALL return to Backlog, and a card a human has edited since SHALL keep its values. The only path by which a task becomes factory work SHALL be the Ready handoff defined by `factory-task-intake`. Eval, bug, and feature routing SHALL be unchanged.

#### Scenario: File a task as a maintainer

- **WHEN** a user with the maintain or admin role creates an issue with native Type Task in a configured source repository
- **THEN** routing adds it to the Project in Backlog without factory ownership

#### Scenario: Retype an edited card as a task

- **WHEN** a human changed a routed card's Status or Owner and its native Type is then changed to Task
- **THEN** routing leaves the card's Owner and Status unchanged

