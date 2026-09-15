# factory-fix-execution Specification

## Purpose
TBD - created by archiving change pickup-and-fix-bugs. Update Purpose after archive.
## Requirements
### Requirement: Clone from local mirrors at recorded commits

The factory SHALL maintain a bare mirror for each configured target repository under the local storage root, fetched at admission using the controller's read credential. Each fix attempt SHALL run in fresh clones checked out at the claim's recorded commits: the target repository from its local mirror, and Agent Runner and Agent Skills from their configured local checkouts. The push credential SHALL NOT be used for fetching or by the controller. Clones SHALL be factory-owned and recorded for cleanup.

#### Scenario: Launch an attempt

- **WHEN** an attempt is launched
- **THEN** its clones are checked out at the claim's recorded commits from the target mirror and the configured Runner and Skills checkouts
- **AND** no clone from a previous attempt is reused

#### Scenario: Fail to fetch a mirror

- **WHEN** the mirror fetch for the target repository fails at admission
- **THEN** the factory holds the bug without recording an attempt or consuming a retry and reports the problem

### Requirement: Invoke the versioned fix workflow

The factory SHALL ship the fix workflow with its own package, stage it into the attempt's artifact directory where the sandboxed Runner resolves it by name, and run it in the existing sandbox through the Runner sandbox script, passing a per-run image tag, the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, and the location of the fix credential. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the packaged workflow does not declare the configured contract version or when the recorded Runner commit cannot run it (its `finalize-pr` workflow does not accept the `ci_fix_cycles` parameter), and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged fix workflow declares the configured contract version and the recorded Runner commit can run it
- **THEN** the attempt starts under its own supervisor with the configured roles and a run-specific image tag

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged workflow declares an unsupported contract version or the recorded Runner commit cannot run it
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy

### Requirement: Fix or decline autonomously

The fix workflow SHALL read the issue and its supplied comments and decide whether the bug is safely fixable without human input. It SHALL return `needs-input` with specific reasons instead of guessing when the problem stems from a CLI, platform, or dependency limitation the agent cannot change, when several viable solutions require a human choice, when the report lacks enough detail to reproduce, when the fix would require changes outside the target repository, or when the fix would require a non-trivial specification change, which belongs in a Feature rather than a bug fix. Trivial inline specification updates are permitted within a fix. The workflow SHALL use one shared lead session for triage and review, one shared implementor session for implementation and validation or review repairs before PR finalization, and one shared tester session for flow testing. Otherwise it SHALL implement the fix with tests following the repository's conventions, run the existing validator workflow immediately after the initial implementation, exercise the changed flow, have the lead review the diff and test evidence, always return the findings to the implementor, and run the same validator workflow again after the implementor addresses them. It SHALL then reuse the Runner's existing generic finalization workflow to push the branch, open or update the pull request, wait for CI, and address failures within its bounded loop. A validator that remains red after its bounded repair and recheck SHALL return `failed` with reasons before any push or PR; CI that remains red after the loop SHALL return `failed` with reasons while leaving that PR open. The PR SHALL be on a deterministic branch named from the issue and claim, SHALL reference the issue without a closing keyword, and SHALL identify the factory claim in a stable marker.

#### Scenario: Fix a reproducible bug

- **WHEN** the issue describes a reproducible defect within the target repository
- **THEN** the implementor produces the code and tests, validation runs after initial implementation and after findings are addressed, and the workflow returns `pull-request` after CI is addressed

#### Scenario: Decline a bug needing a decision

- **WHEN** the issue admits several viable solutions that change behavior differently
- **THEN** the workflow returns `needs-input` naming the options and what it needs decided
- **AND** it pushes no branch and opens no PR

#### Scenario: Fail CI after the bounded loop

- **WHEN** CI remains red after the workflow's fix cycles
- **THEN** the workflow returns `failed` with the failing checks and leaves the PR open for a human

### Requirement: Apply fix-specific limits and window

Each fix attempt SHALL have configurable limits with defaults of 15 minutes without progress, two hours of execution, and three hours of total elapsed time. Fix admission SHALL use its own configurable window, defaulting to always open, and SHALL honor pause, disk and memory admission checks, and provider quota holds for providers used by the fix roles. Fix admission SHALL NOT be bound to the eval window.

#### Scenario: Admit a fix outside the eval window

- **WHEN** a bug is eligible at 18:00 under default configuration and the fix slot is free
- **THEN** the factory admits it although the eval window is closed

#### Scenario: Exceed a fix limit

- **WHEN** a fix attempt exceeds its inactivity, execution, or total limit
- **THEN** the factory stops verified owned execution, records which limit was exceeded, preserves evidence, and applies the recovery policy

### Requirement: Recover a fix attempt from a fresh clone

A fix attempt that fails technically SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation. There SHALL be no resume of a partial attempt. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Retry once

- **WHEN** the first attempt fails technically and reconciliation finds no branch or PR
- **THEN** the factory launches one retry from fresh clones at the same commits

#### Scenario: Exhaust recovery

- **WHEN** the retry also fails technically
- **THEN** the claim settles with `infra-error` and its evidence is retained

### Requirement: Isolate concurrent sandbox builds

Each attempt SHALL build and run its sandbox under a unique image tag derived from its run identity, so an eval attempt and a fix attempt building from different Runner checkouts never overwrite each other's image. The run record SHALL store the image tag used. Run-specific images SHALL be removed together with the claim's clones when its card reaches Done, and SHALL be retained while the claim is running, waiting, blocked, or in Review.

#### Scenario: Build while an eval is running

- **WHEN** a fix attempt starts while an eval attempt's container is running
- **THEN** the fix builds and runs under its own tag and the eval's image and provenance are unaffected

### Requirement: Preserve fix evidence

The factory SHALL retain each attempt's workflow output, structured outcome, validator and CI results as available, and the PR reference under the attempt's artifact directory, recorded in SQLite. Evidence SHALL survive clone cleanup.

#### Scenario: Inspect a declined attempt

- **WHEN** an operator inspects a `needs-input` attempt after cleanup
- **THEN** the attempt's reasons and workflow output remain available under its artifact directory
