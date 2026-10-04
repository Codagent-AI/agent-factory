# Task boundary

Every Task step (triage, guard, review triage, and review implementation) applies these
rules. A request that breaks them is out of scope for a Task.

## Out of scope

- Runtime behavior, public APIs or CLI, persisted data, or OpenSpec specifications.
- Credentials, release or deploy configuration (publishing, versioning, signing,
  tagging, deployment), and branch protection.
- Runtime dependencies, or a shipped runtime requirement (such as `engines`),
  unless the issue explicitly requests it.
- Work outside this repository, work too large for one reviewable pull request, or
  a product, design, compatibility, or other decision the issue leaves open.

Name each crossing and where it belongs: "belongs in a Bug" for a defect, "belongs in a
Feature" for a behavior or specification change.

## In scope

Tooling and development dependencies, CI, docs, behavior-preserving refactors and
cleanups. Decline any decision the issue leaves open with `needs-input`, naming that decision.
