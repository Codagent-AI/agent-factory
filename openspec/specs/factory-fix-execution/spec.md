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

The factory SHALL run the packaged fix workflow in the execution mode configured for the fix kind, `docker` or `host`, passing the configured fix role profiles, the target repository and issue number, the recorded branch names and commits, the eligible issue comments, the attempt number, the location of the fix credential, and the attempt's artifact directory. In `docker` mode the workflow runs in the existing sandbox through the Runner sandbox script with a per-run image tag; in `host` mode it runs as described by the host execution requirement. The workflow SHALL write its outcome and any intermediate records under the supplied artifact directory and SHALL NOT assume a fixed container path. The workflow contract SHALL be versioned; the factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract version or the Runner in use does not support what the workflow requires, and SHALL report this as a readiness problem. The workflow SHALL return exactly one structured outcome: `pull-request` with the PR reference; `needs-input` with reasons; `failed` with reasons; or a technical failure. The outcome SHALL be written to `fix-outcome.json` in the attempt's artifact directory and SHALL declare its contract version; absence of a structured outcome SHALL be treated as a technical failure. A running attempt SHALL keep the mode it launched with; a recovery retry SHALL launch in the currently configured mode.

#### Scenario: Launch with a compatible workflow

- **WHEN** the packaged fix workflow declares a compatible contract version and the Runner in use supports what it requires
- **THEN** the attempt starts under its own supervisor in the configured execution mode with the configured roles

#### Scenario: Launch in Docker mode

- **WHEN** the fix kind is configured for `docker` and the packaged workflow declares the expected contract
- **THEN** the attempt starts under its own supervisor in the sandbox with the configured roles and a run-specific image tag
- **AND** the workflow receives the sandbox's artifact mount as its artifact directory

#### Scenario: Launch in host mode

- **WHEN** the fix kind is configured for `host` and host readiness passes
- **THEN** the attempt starts under its own supervisor on the host with the configured roles and no image tag
- **AND** the workflow receives the attempt's artifact directory under the storage root and writes `fix-outcome.json` there

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged workflow declares an unsupported contract version or the Runner in use lacks a feature the workflow requires
- **THEN** no attempt is recorded, the bug is held, and status and doctor name the incompatibility

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a structured outcome
- **THEN** the factory records a technical failure and applies the recovery policy

#### Scenario: Change the mode while an attempt runs

- **WHEN** the operator changes the fix execution mode while a fix attempt is running
- **THEN** the running attempt continues in the mode it launched with
- **AND** a later recovery retry or new attempt launches in the newly configured mode

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

Each Docker-mode attempt SHALL build and run its sandbox under a unique image tag derived from its run identity, so an eval attempt and a fix attempt building from different Runner checkouts never overwrite each other's image. The run record SHALL store the image tag used. Run-specific images SHALL be removed together with the claim's clones when its card reaches Done, and SHALL be retained while the claim is running, waiting, blocked, or in Review. A host-mode attempt SHALL record no image tag and cleanup SHALL attempt no image removal for it.

#### Scenario: Build while an eval is running

- **WHEN** a Docker-mode fix attempt starts while an eval attempt's container is running
- **THEN** the fix builds and runs under its own tag and the eval's image and provenance are unaffected

#### Scenario: Clean up a host attempt

- **WHEN** a claim whose attempts all ran in host mode reaches Done
- **THEN** cleanup removes its clones and does not invoke Docker

### Requirement: Preserve fix evidence

The factory SHALL retain each attempt's workflow output, structured outcome, validator and CI results as available, and the PR reference under the attempt's artifact directory, recorded in SQLite. For a host-mode attempt it SHALL additionally record the Runner session directory the attempt used and treat that directory as part of the attempt's evidence. Evidence SHALL survive clone cleanup and SHALL be retained until the evidence retention rule in `factory-operations` removes it.

#### Scenario: Inspect a declined attempt

- **WHEN** an operator inspects a `needs-input` attempt after clone cleanup
- **THEN** the attempt's reasons and workflow output remain available under its artifact directory

#### Scenario: Locate a host attempt's session

- **WHEN** an operator inspects a host-mode attempt
- **THEN** the run record names the Runner session directory and the attempt's evidence references it

### Requirement: Run a fix attempt on the host

In `host` mode the factory SHALL run the packaged fix workflow through the operator's installed Agent Runner, from the attempt's target clone, with the operator's own HOME so the selected CLIs use the operator's existing logins and installed codagent plugin. The launch SHALL write nothing to the operator's user-level configuration: git identity, the askpass helper, suppression of credential helpers and terminal prompts, and suppression of URL rewriting and extra headers SHALL be applied as process-local settings of the launched process; role profiles SHALL be written repo-locally in the clone; the packaged workflow SHALL be supplied to the Runner through the attempt's target clone, in the clone's project-local Runner workflow directory excluded from version control, so that it is removed with the clone and no file is left in the operator's Runner configuration; the Runner SHALL be given a session directory under the attempt's artifact directory; and the Skills plugin bootstrap SHALL be skipped. Git and `gh` operations of the launched process SHALL authenticate with the fix token. The Runner SHALL be told not to open an interactive interface. A host readiness failure SHALL hold the bug without falling back to Docker.

#### Scenario: Supply the workflow through the clone

- **WHEN** a host-mode attempt launches
- **THEN** the packaged workflow and its scripts are present only in the attempt's clone, ignored by git there, and the Runner resolves `factory-fix` from that project scope
- **AND** removing the clone at Done leaves no copy of the workflow behind

#### Scenario: Launch without touching the operator's configuration

- **WHEN** a host-mode attempt launches and finishes
- **THEN** the operator's global git configuration, Runner user settings, Runner workflow directory, and CLI plugin configuration are unchanged

#### Scenario: Push with the fix token

- **WHEN** the workflow pushes the fix branch and opens the PR in host mode
- **THEN** the push and the PR are authenticated as the fix credential's identity, not the operator's stored login

#### Scenario: Skip the Skills bootstrap

- **WHEN** a host-mode attempt launches
- **THEN** no plugin marketplace is added or replaced in any CLI
- **AND** the agents use the operator's installed codagent plugin

#### Scenario: Fail host readiness

- **WHEN** the fix kind is configured for `host` and a host prerequisite is unavailable
- **THEN** the bug is held with the reason and no Docker attempt is launched in its place

### Requirement: Keep the fix credential out of persisted state

In both execution modes the fix token SHALL NOT appear in the persisted execution plan, the SQLite run record, the launched command line, factory logs, or attempt evidence. The plan MAY record the path of the factory's private credential copy. The credential copy SHALL be deleted when the claim reaches Done, as today, and for a cancelled claim as soon as its execution has stopped.

#### Scenario: Inspect the run record after launch

- **WHEN** an operator inspects the persisted plan of a host-mode attempt
- **THEN** it names the credential file path and contains no token value

### Requirement: Supervise a host attempt by process

A host-mode attempt SHALL be owned through its recorded process identity alone; the factory SHALL NOT attempt container discovery, container inspection, or container termination for it. Stopping owned execution for cancellation, a limit, or recovery cleanup SHALL stop the Runner process and the agent processes it started. Restart reconciliation SHALL treat a host attempt whose process is gone and whose outcome is absent as a technical failure subject to the recovery policy, and SHALL resume monitoring a verified surviving process. Progress SHALL include activity in the attempt's artifact directory and in the recorded Runner session directory.

#### Scenario: Cancel a host attempt

- **WHEN** the issue of a running host-mode fix is closed
- **THEN** the factory stops the Runner and its child agent processes and no agent process of that attempt keeps running

#### Scenario: Restart while a host attempt runs

- **WHEN** the controller restarts while a host-mode attempt's process is still running
- **THEN** the factory resumes monitoring that process without launching another attempt or consuming a retry

#### Scenario: Make progress through the session directory

- **WHEN** subprocess output is quiet but the Runner session directory continues to advance
- **THEN** the factory recognizes progress rather than stopping the attempt for inactivity

### Requirement: Record host provenance

For a host-mode attempt the run record SHALL store the execution mode, the path and reported version of the Runner executable that ran, and the Runner session directory. The attempt's evidence SHALL state that the attempt ran on the host and that the claim's recorded Runner and Skills commits were not the versions that executed. The claim's recorded commits SHALL be unchanged.

#### Scenario: Inspect a host attempt's provenance

- **WHEN** an operator inspects a host-mode attempt
- **THEN** the run record shows mode `host`, the Runner executable and version used, and the session directory
- **AND** the evidence states that the recorded Runner and Skills commits were not executed

