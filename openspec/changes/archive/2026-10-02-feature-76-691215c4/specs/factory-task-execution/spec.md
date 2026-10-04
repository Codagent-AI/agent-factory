## ADDED Requirements

### Requirement: Invoke the versioned task workflow

The factory SHALL ship a packaged task workflow, `factory-task`, declaring contract `factory-task/1`. It SHALL stage that workflow and its scripts beside the other packaged workflows, which the task kind also needs because review rounds run `factory-review`. The task kind SHALL run on the host only, through the same host execution rules as a host fix attempt: the operator's installed Runner, process-local git and credential settings, the workflow supplied through the attempt's clone, the fix credential for pushes, and recorded host provenance. The factory SHALL pass the workflow the configured task role profiles (lead, implementor, tester), the target repository and issue number, the recorded branch names and commits, the eligible issue comments and any prior factory pull request, the attempt number, the location of the fix credential, and the attempt's artifact directory. A task attempt SHALL run on a fresh branch named from the issue and claim with the `factory/task` prefix, starting at the recorded target commit.

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

### Requirement: Triage a task against its risk boundary

Before creating a branch, the task workflow SHALL read the issue and its supplied comments and decide whether the work is low-risk maintenance an autonomous agent can deliver without a human decision. It SHALL return `needs-input`, naming the specific decision or the boundary crossed, instead of proceeding when any of the following holds:

- the work would change runtime behavior, a public API or CLI, an OpenSpec specification, or persisted data or its format;
- it needs a product, design, compatibility, or threshold decision that neither the issue nor the repository's conventions bound;
- it is too large to review comfortably as one pull request;
- it touches credentials or secrets, release or deploy configuration, or branch protection;
- it requires changes outside the target repository.

When the work belongs in another kind, the reasons SHALL say so: "belongs in a Bug" for a defect fix, or "belongs in a Feature" for a behavior or specification change. On a `needs-input` decline the workflow SHALL create no branch, push nothing, and open no pull request. On a re-attempt, triage SHALL treat the writer's comments as answers to what caused the prior decline and re-evaluate in light of them. When triage accepts the task it SHALL produce a plan, the delegated choices it will make, the new or tightened gates the work introduces, and whether the change alters user-visible tooling behavior.

#### Scenario: Decline a behavior change

- **WHEN** a Task asks to "clean up" a function in a way that changes the values it returns
- **THEN** the workflow returns `needs-input` naming the behavior change and that it belongs in a Bug or Feature
- **AND** no branch is pushed and no pull request is opened

#### Scenario: Decline an open threshold decision

- **WHEN** a Task asks to "add a duplication gate" without naming a threshold or range, and the repository has no existing threshold
- **THEN** the workflow returns `needs-input` naming the threshold as the decision needed

#### Scenario: Decline oversized work

- **WHEN** a Task asks to enable a strict type-check mode that would require edits across most of the repository's modules with no baseline mechanism allowed by the issue
- **THEN** the workflow returns `needs-input` stating that the work is too large for one pull request and suggesting how to split it

#### Scenario: Decline a credential or branch-protection change

- **WHEN** a Task asks to rotate a CI secret or change branch protection rules
- **THEN** the workflow returns `needs-input` naming the excluded area

#### Scenario: Accept after an answer

- **WHEN** a Task was declined for an open threshold and a writer then comments with the threshold to use
- **THEN** the next attempt's triage accepts the task using that threshold

### Requirement: Accept bounded, evidence-based choices

Triage SHALL accept a choice that the issue explicitly hands to the work, such as "fix, or baseline with per-file ignores (decide during the work)" or "measure before fixing the threshold". Two conditions apply: the choice changes no runtime behavior, and its range is bounded by the issue (a stated target or limit) or by the repository (an existing CI tool version, `.nvmrc`, or current configuration). The workflow SHALL record each such choice in the pull request description with its evidence, such as a measured value and the threshold chosen. A threshold SHALL meet the issue's stated target when the measured repository meets it. Otherwise it SHALL be set at the measured baseline, never looser, and the gap SHALL be stated. A violation that cannot be fixed without risking behavior SHALL be baselined rather than fixed when the issue permits baselining, and declined otherwise.

#### Scenario: Measure before fixing a threshold

- **WHEN** a Task asks for a jscpd gate with a 1.5% target and to measure the repository before fixing the threshold, and the repository measures 2.1%
- **THEN** triage accepts the task, the gate is set at the measured baseline of 2.1%, and the pull request description records the measurement, the target, and the gap

#### Scenario: Fix or baseline as delegated

- **WHEN** a Task asks to enable stricter lint rules and to fix or baseline existing violations, deciding during the work
- **THEN** violations with behavior-preserving fixes are fixed, the others are baselined with per-file ignores, and the description lists which were which

#### Scenario: Take a version from repository evidence

- **WHEN** a Task asks to declare the supported Node version and CI already pins one Node version
- **THEN** triage accepts the task and the declaration uses the version CI pins

#### Scenario: No evidence bounds the version

- **WHEN** a Task asks to declare the supported Node version and nothing in the issue or repository fixes one
- **THEN** the workflow returns `needs-input` naming the supported Node version as a compatibility decision

### Requirement: Separate toolchain work from release configuration

The task scope SHALL include:

- development toolchain and dev-dependency bumps;
- linter, formatter, type-checker, and test-runner configuration;
- CI jobs that only check: lint, type, test, duplication, and dead-code jobs.

Release configuration SHALL be excluded from task scope. It is anything that publishes, versions, signs, or deploys an artifact: release and publish workflows, version fields, tags and changelog releases, registry and deploy settings, and the secrets they use. Runtime-dependency bumps SHALL also be excluded, as SHALL changes to a shipped runtime requirement such as a package's `engines` field, unless the issue explicitly requests the change and the value is one the repository already uses. Removing exports reachable from a published package's entry points or CLI SHALL be treated as a public interface change.

#### Scenario: Add a checking CI job

- **WHEN** a Task asks to add a CI job that runs lint and tests
- **THEN** triage accepts it as toolchain work

#### Scenario: Touch a release workflow

- **WHEN** a Task's plan requires editing the workflow that publishes the package
- **THEN** the workflow returns `needs-input` naming the release configuration

#### Scenario: Remove an unused published export

- **WHEN** a dead-code tool reports an export that is reachable from the published package's entry point as unused
- **THEN** the task configures that export as an entry point or baselines it, and does not delete it

### Requirement: Implement and verify a task

When triage accepts, the workflow SHALL implement the plan on the task branch following the repository's conventions, without requiring a test that fails first. It SHALL run the repository's tests, linters, and the existing validator workflow after the initial implementation, repair once and recheck when the validator fails, and have the lead review the diff and evidence. It SHALL return the findings to the implementor, run the validator again, and require a clean working tree. It SHALL then reuse the Runner's generic finalization workflow to push the branch, open or update the pull request, wait for CI, and address failures within its bounded loop. A validator or clean-tree gate that remains red after its bounded repair SHALL return `failed` with reasons before any push or pull request. CI that remains red after the loop SHALL return `failed` with reasons while leaving the pull request open. The pull request SHALL reference the issue without a closing keyword and SHALL carry the factory claim marker.

#### Scenario: Deliver a lint baseline

- **WHEN** an accepted Task tightens lint configuration and baselines the existing violations
- **THEN** the validator and tests pass, CI is addressed, and the workflow returns `pull-request`

#### Scenario: Validator stays red

- **WHEN** the validator still fails after the bounded repair and recheck
- **THEN** the workflow returns `failed` with the failing checks, and pushes no branch and opens no pull request

#### Scenario: CI stays red

- **WHEN** CI remains red after the workflow's fix cycles
- **THEN** the workflow returns `failed` naming the failing checks and leaves the pull request open

### Requirement: Guard every pushed head against the task boundary

Before the first push, after all implementation, finding repairs, and validator repairs, the workflow SHALL check the complete diff from the recorded target commit against the task boundary of "Triage a task against its risk boundary" and "Separate toolchain work from release configuration". The check SHALL treat as definite crossings changes to OpenSpec specifications, to workflows that release, publish, deploy, or run on tag pushes, and to code-owner files. A lead judgment SHALL cover the rest of the boundary. When the diff crosses the boundary, the workflow SHALL push nothing, open no pull request, and return `needs-input` naming each crossing and its route to a Bug or Feature.

When finalization's CI repair adds commits, the workflow SHALL repeat the boundary check on the complete diff after finalization, and SHALL repeat the confirmed exercise of every named gate whose files those commits changed. A crossing or an unconfirmed gate SHALL return `failed` with the reasons and the pull request reference, leaving the pull request open. A task attempt SHALL NOT return `pull-request` for a head that skipped either check.

#### Scenario: Implementation edits a release workflow

- **WHEN** an accepted Task's implementation also edits the workflow that publishes the package
- **THEN** the pre-push check returns `needs-input` naming the release configuration change
- **AND** no branch is pushed and no pull request is opened

#### Scenario: A finding repair introduces a crossing

- **WHEN** the diff was within scope at the lead review, but the implementor's repair of a finding changes a function's return value
- **THEN** the pre-push check on the complete diff returns `needs-input` naming the behavior change, and nothing is pushed

#### Scenario: CI repair adds an out-of-scope commit

- **WHEN** finalization's CI repair pushes a commit that edits an OpenSpec specification
- **THEN** the post-finalization check returns `failed` naming the crossing, and the pull request stays open

#### Scenario: CI repair changes a gate's configuration

- **WHEN** finalization's CI repair loosens the configuration of a gate triage named
- **THEN** that gate's exercise runs again, and the attempt returns `failed` unless the gate is still confirmed to reject its planted violation

#### Scenario: Finalization adds nothing

- **WHEN** CI passes on the first wait and finalization adds no commit
- **THEN** no post-finalization check runs and the outcome is `pull-request`

### Requirement: Exercise new and tightened gates

For each new or tightened check or CI job that triage named, the workflow SHALL record two exercises before finalization: the gate passes on the delivered tree, and the gate fails on a deliberately introduced violation. Both exercises SHALL run the command triage named for the gate. For a CI job, that is the command the job runs, run locally. After the failing exercise the violation SHALL be reverted. The planted change and both outputs SHALL be kept in the attempt's evidence, and the results summarized in the pull request description. When the issue states a rejection criterion, the introduced violation SHALL match it.

A session other than the one that planted the violation SHALL confirm that the failing output contains a diagnostic identifying the planted violation, and that the plant satisfies any issue-stated criterion. A failing exit status alone SHALL NOT count as proof: the confirmed diagnostic SHALL appear in the failing output and not in the passing output. When a gate does not fail on its violation, or its failure is not confirmed in this way, the implementor SHALL repair the gate once and the exercise SHALL be repeated. A gate that still does not fail SHALL return `failed` with the reason before any push. When triage marks other tooling behavior as user-visible, the workflow SHALL also run a flow-testing step for it. A documentation-only or behavior-preserving refactor task with no new or tightened gate and no user-visible tooling change SHALL skip both.

#### Scenario: Prove a duplication gate rejects duplication

- **WHEN** a Task's issue says a deliberately duplicated 70+ token block must fail the new duplication check
- **THEN** the evidence shows the check passing on the delivered tree and failing on an introduced 70+ token duplicate, and the delivered tree contains no such duplicate

#### Scenario: An unrelated error does not prove a gate

- **WHEN** the negative exercise exits non-zero because the tool crashed on a configuration error, and its output does not identify the planted duplicate
- **THEN** the exercise is not confirmed, the gate is repaired once and exercised again, and a second unconfirmed result returns `failed` before any push

#### Scenario: A different command does not prove a gate

- **WHEN** the recorded negative exercise ran a command other than the one triage named for the gate
- **THEN** the exercise does not count and the gate is treated as unproven

#### Scenario: A new gate never fails

- **WHEN** the introduced violation still passes the new gate after one repair
- **THEN** the workflow returns `failed` stating that the gate does not reject violations, and pushes nothing

#### Scenario: Docs-only task

- **WHEN** an accepted Task only reorganizes documentation
- **THEN** no gate exercise or flow-testing step runs, and the workflow proceeds to review and finalization

### Requirement: Mark task commits and pull requests as chores

Commits that a task attempt creates before finalization SHALL use the `chore:` conventional-commit type in their subject, after any workflow step marker. The workflow SHALL correct non-conforming subjects of its own unpushed commits before the first push, without changing their content. Commits that finalization's CI repair adds are already pushed when the workflow sees them. Any subject among them without `chore:` SHALL be recorded in the attempt's evidence and in the pull request description, and SHALL NOT change the outcome. The task pull request's title SHALL start with `chore:`. When finalization leaves a title without that prefix, the workflow SHALL correct the title before recording the outcome. A title it fails to correct SHALL be recorded in the attempt's evidence and SHALL NOT change the outcome.

#### Scenario: Open a chore pull request

- **WHEN** an accepted Task produces a pull request
- **THEN** the pull request title starts with `chore:` and the attempt's commit subjects use the `chore:` type

#### Scenario: Correct a title set by finalization

- **WHEN** finalization opens the pull request with a title that lacks `chore:`
- **THEN** the workflow retitles it with the `chore:` prefix before recording `pull-request`

### Requirement: Apply task limits, window, and recovery

Each task attempt SHALL have configurable limits with defaults of 15 minutes without progress, two hours of execution, and three hours of total elapsed time. Task admission SHALL use its own configurable window, defaulting to always open. It SHALL honor pause, disk and memory admission checks, and provider quota holds for the providers the task roles use, and SHALL NOT be bound to the eval window. A task attempt that fails technically SHALL receive at most one automatic recovery retry, launched from fresh clones at the recorded commits after side-effect reconciliation. Exhausted recovery SHALL settle the claim with `infra-error`. Quota waits and unavailable prerequisites SHALL NOT consume the retry.

#### Scenario: Exceed a task limit

- **WHEN** a task attempt exceeds its inactivity, execution, or total limit
- **THEN** the factory stops verified owned execution, records which limit was exceeded, preserves evidence, and applies the recovery policy

#### Scenario: Exhaust recovery

- **WHEN** a task attempt and its retry both fail technically
- **THEN** the claim settles with `infra-error` and its evidence is retained

### Requirement: Treat task claims as pull-request claims in shared lifecycle rules

Wherever the claim lifecycle, operations, or execution-backend requirements name fix and feature claims as pull-request claims, task claims SHALL be included and follow the fix rules unless a task requirement says otherwise. That includes:

- one slot per kind, with a blocked task claim occupying no slot;
- correcting contradicting status edits, where a triage-blocked task stays in Running and a review-blocked task stays in Review;
- pre-suite failure handling;
- exemption of settled work from closure cancellation;
- worktree cleanup after review;
- evidence retention, including keeping the task outcome;
- the deploy's rule that skips the Agent Validator build while a host attempt runs;
- the post-run audit.

Feature-only behavior SHALL NOT apply to task claims: definition, phase checkpoints, resume from a step, OpenSpec archive, acceptance, attention classification, and merging the target branch.

#### Scenario: Leave a declined task in Running

- **WHEN** a task claim declined by triage sits in Running with the `needs-input` label and a human moves its card to Done
- **THEN** the factory restores the card to Running

#### Scenario: Deploy while a task runs on the host

- **WHEN** the operator deploys while a task attempt runs on the host
- **THEN** the deploy skips the Agent Validator fast-forward and build with a warning, as it does for a host fix

#### Scenario: Close the issue of a settled task before sync

- **WHEN** a human closes the issue of a task claim whose pull request merged before the factory synced
- **THEN** the claim is not cancelled and still receives its merge sync
