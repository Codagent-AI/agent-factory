# factory-routing Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
### Requirement: Route requests through shared configuration

Routing rules and their implementation SHALL be maintained in `agent-factory` and invoked through a reusable GitHub Actions workflow. Source repositories SHALL use small caller workflows that subscribe to issue creation, reopening, editing, closure, label changes, and native issue type changes. Rules SHALL configure source repositories and the destination Project. Routing SHALL add an issue or pull request to the destination Project if absent and apply the default Priority to an issue whose Priority is empty. Routing SHALL act only on delivered issue events; it SHALL NOT retroactively route issues that predate the caller workflow. Closing a tracked issue or pull request SHALL move its card to Done.

#### Scenario: Route a new issue
- **WHEN** an issue of any native type is created in a configured source repository
- **THEN** routing adds it to the Project with no Status and no factory ownership

#### Scenario: Close a tracked issue
- **WHEN** a tracked issue or pull request is closed
- **THEN** routing sets its card's Status to Done

### Requirement: Never route work to the factory

Routing SHALL NOT set `Owner`, `Status=Ready`, or any other Status for any issue or pull request, whatever its native Type (Bug, Feature, Task, Eval, or none), its labels, or its author's repository role. Routing SHALL NOT set Backlog, so it cannot overwrite a Status that a handoff set while a routing run was in flight. Routing SHALL recognize no bypass marker. Creating an issue SHALL NOT start factory work. The only path by which an issue becomes factory work SHALL be the handoff: a writer moves its card to Ready, or uses the `factory-assign` skill, after which the factory poll verifies the author's permission and sets `Owner=factory`. Each work kind's intake SHALL verify the author's write, maintain, or admin permission at admission.

#### Scenario: Create a writer's issue
- **WHEN** a writer creates a Bug, Feature, Task, or Eval issue
- **THEN** routing adds it to the Project with no Status and no factory ownership
- **AND** no work starts

#### Scenario: Assign right after creation
- **WHEN** a handoff sets `Owner=factory` and `Status=Ready` while routing is still running for the new issue
- **THEN** the card keeps `Owner=factory` and `Status=Ready`

#### Scenario: Hand off by moving the card
- **WHEN** a writer moves a card to Ready
- **THEN** the factory poll verifies the author's permission and sets `Owner=factory`
