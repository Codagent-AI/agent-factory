# Task boundary

Every Task step (triage, guard, review triage, and review implementation) applies these
rules. A request that breaks them is out of scope for a Task.

## Out of scope

- Runtime behavior, public APIs or CLI, persisted data, or OpenSpec specifications.
- Credentials, release or deploy configuration (publishing, versioning, signing,
  tagging, deployment), and branch protection.
- Runtime dependencies, or a shipped runtime requirement (such as `engines`), unless the
  issue explicitly requests a value the repository already uses. For example, a Node
  version that CI, `.nvmrc`, or Docker already pins.
- Work outside this repository, work too large for one reviewable pull request, or an
  open product, design, compatibility, or threshold choice.

Name each crossing and where it belongs: "belongs in a Bug" for a defect, "belongs in a
Feature" for a behavior or specification change.

## In scope

Development tools, dev dependencies, linters and type checkers, check-only CI jobs
(lint, type, test, duplication, dead code), docs, and behavior-preserving refactors.

## Delegated choices

Accept a choice the issue hands to the work when the issue or the repository bounds it:

- use a stated target when the measurements meet it;
- otherwise record the measured baseline, and never loosen it;
- baseline any finding that cannot be fixed without changing behavior.
