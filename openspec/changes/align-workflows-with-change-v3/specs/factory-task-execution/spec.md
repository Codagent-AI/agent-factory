## MODIFIED Requirements

### Requirement: Invoke the versioned task workflow

The factory SHALL ship a packaged task workflow, `factory-task`, declaring contract `factory-task/1`. It SHALL stage that workflow and its scripts beside the other packaged workflows, which the task kind also needs because review rounds run `factory-review`. The task kind SHALL run on the host only, through the same host execution rules as a host fix attempt: the operator's installed Runner, process-local git and credential settings, the workflow supplied through the attempt's clone, the fix credential for pushes, and recorded host provenance. The factory SHALL pass the workflow the configured task role profiles (lead, implementor, tester, crosscheck), the target repository and issue number, the recorded branch names and commits, the eligible issue comments and any prior factory pull request, the attempt number, the location of the fix credential, and the attempt's artifact directory. A task attempt SHALL run on a fresh branch named from the issue and claim with the `factory/task` prefix, starting at the recorded target commit.

The workflow SHALL return exactly one structured outcome:

- `pull-request` with the pull request reference;
- `needs-input` with reasons;
- `failed` with reasons;
- a technical failure.

The outcome SHALL be written to `task-outcome.json` in the attempt's artifact directory and SHALL declare contract `factory-task/1`. The absence of a valid structured outcome SHALL be treated as a technical failure. The factory SHALL refuse to launch when the packaged workflow does not declare a compatible contract or the Runner in use lacks what it requires, and SHALL report this as a readiness problem.

#### Scenario: Launch a task attempt

- **WHEN** a task is admitted, the packaged workflow declares `factory-task/1`, and task readiness passes
- **THEN** the attempt starts under its own supervisor on the host with the configured task roles
- **AND** the workflow receives the attempt's artifact directory and writes `task-outcome.json` there

#### Scenario: Launch with an incompatible workflow

- **WHEN** the packaged task workflow declares an unsupported contract or the Runner lacks a feature it requires
- **THEN** no attempt is recorded, the task is held, and status and doctor name the incompatibility

#### Scenario: Finish without an outcome

- **WHEN** the workflow exits without writing a valid `task-outcome.json`
- **THEN** the factory records a technical failure and applies the task recovery policy

#### Scenario: Write an outcome with a wrong contract

- **WHEN** `task-outcome.json` declares a contract other than `factory-task/1` or an outcome value outside the four defined
- **THEN** the factory treats the attempt as a technical failure

### Requirement: Implement and verify a task

When triage accepts, the lead SHALL write a small definition of the task outside the repository, including a requirement that the change stay within the task boundary, checked by the deterministic scope script. The workflow SHALL append the factory's implementation constraints and the task boundary to the definition's design and implement the plan on the task branch through the Runner's `core/implement-change` on it, following the repository's conventions and without requiring a test that fails first: the lead's and the implementor's checklists merged by the crosscheck, the implementation, the crosscheck's audit by inspection, the implementor's resolution of its findings, and `core/verify-change`, whose single validator run, with its bounded repair, and clean-tree check precede the draft pull request and bounded acceptance. When the lead rules that no compliant implementation is possible without the user, the workflow SHALL return `needs-input`. It SHALL then reuse the Runner's generic finalization workflow to push the branch, open or update the pull request, wait for CI, and address failures within its bounded loop. A validator or clean-tree check that remains red after its bounded repair SHALL return `failed` with reasons and open no pull request; a failure before verification starts SHALL be a technical failure. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue exactly once with `Closes #N`, so GitHub links the pull request and closes the issue on merge, and SHALL carry the factory claim marker.

#### Scenario: Normalize the task pull request body

- **WHEN** a finalized body has no closing keyword for the task issue or has `Refs #N`
- **THEN** annotation leaves exactly one `Closes #N` for that issue and the factory claim marker

#### Scenario: Deliver maintenance

- **WHEN** an accepted Task updates development tooling without changing runtime behavior
- **THEN** the validator and tests pass, CI is addressed, and the workflow returns `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator still fails after the bounded repair
- **THEN** the workflow returns `failed` with the failing checks and opens no pull request

#### Scenario: CI stays red

- **WHEN** CI remains red after the workflow's fix cycles
- **THEN** the workflow returns `failed` naming the failing checks and leaves the pull request open

### Requirement: Guard every pushed head against the task boundary

The Runner's verification pushes the branch and opens the draft pull request, so the boundary SHALL be checked twice before the pull request is marked ready: as a requirement of the task's definition, which the implementor verifies with the deterministic scope script and the crosscheck audits, and by a guard step after implementation, finalization excluded, that checks the complete diff from the recorded target commit against the task boundary of "Triage a task against its risk boundary" and "Separate toolchain work from release configuration". The check SHALL treat as definite crossings changes to OpenSpec specifications, to workflows that release, publish, deploy, or run on tag pushes, and to code-owner files. A lead judgment SHALL cover the rest of the boundary. When the guard finds the diff crossing the boundary, the workflow SHALL NOT mark the pull request ready or finalize it, and SHALL return `failed` naming each crossing and its route to a Bug or Feature, with the draft pull request reference, leaving the draft open for a human. A crossing found before any pull request exists SHALL return `needs-input`.

When finalization's CI repair adds commits, the workflow SHALL repeat the boundary check on the complete diff after finalization, A crossing SHALL return `failed` with the reasons and the pull request reference, leaving the pull request open. A task attempt SHALL NOT return `pull-request` for a head that skipped the required boundary check.

#### Scenario: Implementation edits a release workflow

- **WHEN** an accepted Task's implementation also edits the workflow that publishes the package
- **THEN** the guard returns `failed` naming the release configuration change, with the draft pull request reference
- **AND** the pull request is not marked ready and CI is not awaited

#### Scenario: A finding repair introduces a crossing

- **WHEN** the diff was within scope at the lead review, but the implementor's repair of a finding changes a function's return value
- **THEN** the guard on the complete diff returns `failed` naming the behavior change, and the draft pull request is left open

#### Scenario: CI repair adds an out-of-scope commit

- **WHEN** finalization's CI repair pushes a commit that edits an OpenSpec specification
- **THEN** the post-finalization check returns `failed` naming the crossing, and the pull request stays open

#### Scenario: Finalization adds nothing

- **WHEN** CI passes on the first wait and finalization adds no commit
- **THEN** no post-finalization check runs and the outcome is `pull-request`

### Requirement: Test user-visible tooling flows

When triage marks tooling behavior as user-visible, the task's definition SHALL name the tooling flows the Runner's acceptance testing exercises. A Task with no user-visible tooling change SHALL state in its test plan that no agent acceptance flow is required beyond the automated checks and current-head CI.

#### Scenario: Docs-only task

- **WHEN** an accepted Task only reorganizes documentation
- **THEN** the definition requires no acceptance flow, and the workflow proceeds to the guard and finalization

### Requirement: Mark task commits and pull requests as chores

Commits that a task attempt creates SHALL use the `chore:` conventional-commit type in their subject, after any workflow step marker; the constraints appended to the task's design say so. The Runner pushes the branch before the workflow can rewrite subjects, so the workflow SHALL NOT rewrite them. Any subject without `chore:`, among the implementation's commits and the commits that finalization's CI repair adds, SHALL be recorded in the attempt's evidence and in the pull request description, and SHALL NOT change the outcome. The task pull request's title SHALL start with `chore:`. When finalization leaves a title without that prefix, the workflow SHALL correct the title before recording the outcome. A title it fails to correct SHALL be recorded in the attempt's evidence and SHALL NOT change the outcome.

#### Scenario: Open a chore pull request

- **WHEN** an accepted Task produces a pull request
- **THEN** the pull request title starts with `chore:` and the attempt's commit subjects use the `chore:` type

#### Scenario: Correct a title set by finalization

- **WHEN** finalization opens the pull request with a title that lacks `chore:`
- **THEN** the workflow retitles it with the `chore:` prefix before recording `pull-request`
