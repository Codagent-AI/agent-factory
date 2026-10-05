# factory-eval-execution Specification

## Purpose
TBD - created by archiving change iteration-1. Update Purpose after archive.
## Requirements
### Requirement: Evaluate Runner and Skills using an existing suite

The factory SHALL support evaluation of requested Agent Runner and Agent Skills revisions through the existing `agent-evals` harness. `and-scene` SHALL be the default eval suite. The harness SHALL be taken from a configured branch (default `main`), resolved to a commit at claim admission, recorded, and retained as part of the evaluation environment for that claim; requests SHALL NOT select different `agent-evals` revisions. Suite setup documentation SHALL state which harness behavior (score-failure contract, calibration-gate removal, linked-worktree metadata mounts, persisted sessions) the factory depends on, and `doctor` SHALL verify the resolved harness commit contains the suite entry points the factory invokes.

Suite-specific readiness checks, invocation, evidence interpretation, supported resume behavior, and human-review instructions SHALL remain separate from generic queueing, scheduling, claim tracking, and recovery. Adding another suite to the harness SHALL be able to reuse that generic behavior. Implementing additional suites or a dynamic plugin system is outside this change.

#### Scenario: Evaluate selected component revisions

- **WHEN** an accepted request specifies Runner and Skills refs
- **THEN** the factory evaluates their resolved revisions with the default `and-scene` suite at the harness commit resolved for the claim
- **AND** records the suite identity and that harness commit as part of the test environment

#### Scenario: Add a suite later

- **WHEN** another suite is integrated with `agent-evals` in a later change
- **THEN** its suite-specific behavior can be supplied without replacing the factory's generic queueing, claim tracking, scheduling, and recovery behavior

#### Scenario: Advance the harness branch during a claim

- **WHEN** `agent-evals` `main` receives commits while a claim has unfinished repetitions
- **THEN** the remaining repetitions and any recovery retry use the claim's recorded harness commit

### Requirement: Execute against clean pinned worktrees

The factory SHALL prepare factory-owned clean Git worktrees for the accepted Runner and Skills commits and retain the deployed `agent-evals` version in a pinned worktree. These worktrees SHALL remain associated with the claim for its lifetime, including automatic deferrals and recovery, and SHALL NOT be repointed for another claim. Repetitions SHALL use the same accepted inputs rather than re-resolving moving refs. The worktrees SHALL remain the factory-host record of the accepted inputs in every execution mode; under `fly` execution the sandbox clones Runner and Skills at the same commits and receives the harness input delivered from the pinned `agent-evals` worktree, as defined in `factory-fly-execution`.

#### Scenario: Defer a claim while source branches advance

- **WHEN** a claim waits overnight or for quota and its original source branches advance
- **THEN** its next repetition or recovery attempt uses the original pinned worktrees and accepted revisions

#### Scenario: Prepare another claim

- **WHEN** the factory prepares another claim while an earlier claim retains resumable work
- **THEN** the earlier claim's worktrees remain pinned to their accepted revisions
- **AND** the new claim does not repoint them

#### Scenario: Verify linked worktree provenance inside Docker

- **WHEN** the selected suite runs using factory-owned linked worktrees
- **THEN** its container can read the required backing Git metadata and verify the actual pinned source SHAs and cleanliness
- **AND** source and Git metadata mounts remain read-only without mutating shared checkouts

#### Scenario: Verify pinned source provenance on Fly

- **WHEN** the selected suite runs under `fly` execution against the claim's pinned Runner and Skills revisions
- **THEN** the Machine verifies the actual pinned source SHAs and cleanliness through clones at the recorded commits
- **AND** the shared checkouts and their Git metadata on the factory host are not mutated

### Requirement: Invoke the suite with accepted execution settings

For `and-scene`, the factory SHALL invoke `evals/agent-runner/and-scene/run.sh` from the retained harness worktree using its agent-execution mode. It SHALL supply the pinned Runner and Skills worktree paths, complete lead/implementor/tester profiles, the repetition's artifact directory, and required environment-file paths through the suite's supported interface. Under Docker execution the integration SHALL support the selected suite's Codex, Claude, and Cursor role adapters; under Fly execution it SHALL support Codex and Claude only. It SHALL pass the accepted `skip_validator` setting to the suite. The factory SHALL use the suite's existing workflow and execution behavior rather than implement a second evaluator. The suite SHALL run in one sandbox per repetition in the configured execution mode, with the controller and supervisor on the host. Under Docker a recovery attempt SHALL reuse the repetition's artifacts through a new container; under Fly it SHALL reuse the repetition's surviving Machine as defined in `factory-fly-execution`. Controller restart SHALL preserve verified surviving execution in every mode. Under Docker, integration SHALL verify the selected Runner sandbox launcher supports required mounts and arguments; under Fly it SHALL verify that the pinned harness invokes the factory's launcher with an argument set the launcher supports. The launcher SHALL accept the harness's credential-mount flags in any order, because the harness emits them in role order and that order varies with the configured role CLIs. The Fly compatibility check SHALL exercise a role selection that produces the widest credential-mount shape the harness can emit, so that an ordering or grammar mismatch is detected before model execution rather than during an attempt. Missing compatibility SHALL be exposed as an actionable readiness problem before model execution.

Selected-suite readiness SHALL be verified before execution. Calibration SHALL remain an optional suite-maintainer diagnostic; the factory SHALL NOT require a calibration receipt or pass removed calibration-record arguments. Unavailable prerequisites SHALL follow the readiness-hold behavior in `factory-claim-lifecycle`.

#### Scenario: Launch an accepted repetition

- **WHEN** the claim is eligible and its prerequisites are available
- **THEN** the suite receives the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting
- **AND** the invocation uses the retained harness version

#### Scenario: Launch with Cursor role profiles

- **WHEN** the eval kind runs under Docker execution, an accepted request selects Cursor for one or more role profiles, and the host Cursor CLI is available
- **THEN** the factory passes those profiles unchanged to the selected suite
- **AND** the suite remains responsible for validating the mounted Cursor authentication at launch

#### Scenario: Fail selected-suite readiness

- **WHEN** the selected suite's required entry-point or fixture files are unavailable in the pinned environment
- **THEN** no evaluation starts and the factory reports the readiness problem without consuming an execution attempt or recovery retry

#### Scenario: Hold Fly readiness on an unexpected launcher argument

- **WHEN** the eval kind runs under Fly execution and the pinned harness would invoke the launcher with an argument the launcher does not support
- **THEN** no Machine is created, eval admission is held, and the readiness problem names the unsupported argument and the harness commit

### Requirement: Isolate repetitions with stable identities

Each requested repetition SHALL run with the same accepted settings and revisions but its own globally distinct, Git-ref-safe suite run identity and artifact directory. For `and-scene`, the artifact-directory basename SHALL include claim and repetition identity because the suite uses that identity in candidate branch names. Recovery attempts for the same repetition SHALL reuse its suite identity and artifact directory while retaining distinct execution-attempt records.

#### Scenario: Execute several repetitions

- **WHEN** a claim requests three repetitions
- **THEN** each repetition has a distinct suite run identity, evidence directory, and associated candidate identity
- **AND** all three use the same accepted settings and code revisions

#### Scenario: Recover a repetition

- **WHEN** the factory starts the allowed recovery attempt for an interrupted repetition
- **THEN** it records a new attempt while reusing that repetition's suite identity and artifact directory

### Requirement: Resume through the suite's supported interface

The factory SHALL resume interrupted `and-scene` work with a valid checkpoint through the suite's `--resume` interface using saved inputs and the artifact directory, subject to the lifecycle recovery budget. When reconciliation establishes that an attempt stopped before creating a checkpoint and before candidate execution, the factory SHALL retry without `--resume` using the same repetition identity, inputs, artifact directory, and remaining retry budget. A corrupt checkpoint or missing state alongside evidence of execution SHALL NOT authorize a fresh start. It SHALL respect the suite's checks of input revisions, settings, candidate identity, and evidence. A rejected resume SHALL NOT be bypassed by silently changing inputs, discarding evidence, or starting a fresh evaluation under the same repetition identity. Completed repetitions SHALL remain completed. Under Fly execution, resume SHALL run only inside the repetition's surviving Machine; a missing Machine, or a Machine without a checkpoint it was known to have created, SHALL follow the lost-Machine outcome in `factory-fly-execution` rather than the corrupt-state scenario below, while a surviving Machine whose attempt verifiably stopped before checkpoint creation follows the fresh-retry scenario below inside that Machine.

#### Scenario: Resume compatible saved work

- **WHEN** saved work is eligible for recovery and passes the suite's resume checks
- **THEN** the suite continues that repetition using the existing evidence and accepted inputs

#### Scenario: Recover an attempt that never reached checkpoint creation

- **WHEN** the prior attempt is verified stopped before checkpoint creation and candidate execution, and a technical retry remains
- **THEN** recovery invokes the suite without `--resume` using the same repetition identity, artifact directory, and frozen inputs
- **AND** it consumes the existing technical retry allowance rather than granting another repetition or unlimited retries

#### Scenario: Preserve unexplained or corrupt saved state

- **WHEN** the checkpoint is corrupt, or is missing despite evidence that candidate execution began
- **THEN** the factory preserves the evidence and reports the recovery problem without silently starting fresh

#### Scenario: Reject incompatible saved work

- **WHEN** the suite rejects resume because saved evidence or score-affecting inputs do not match
- **THEN** the factory preserves the diagnostic and existing evidence and applies its technical-failure policy
- **AND** it does not bypass the checks or substitute new inputs

### Requirement: Record the evaluated environment accurately

The factory SHALL retain the selected suite identity, full Runner and Skills commit SHAs, deployed `agent-evals` commit SHA, fixture and reference pins, rubric identities, workflow, judge profile, role settings, validator setting, and repetition count. It SHALL record the sandbox image actually used: under Docker, the actual container's immutable image ID after the image is built; under Fly, the immutable image digest observed on the launched Machine together with the Machine identity, size, and region. It SHALL NOT infer the image from a mutable tag or claim that a future image digest was known when the request was accepted. If that observation is unavailable, provenance SHALL say so rather than substitute a later tag lookup. These records SHALL distinguish requested inputs from observed execution provenance.

#### Scenario: Inspect a completed evaluation

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the tested Runner and Skills revisions and the harness, suite, scoring inputs, and execution settings used to evaluate them are identifiable
- **AND** the recorded image identifies the image actually used and, for Fly, the Machine used

### Requirement: Preserve suite-owned evidence and candidate outputs

The suite SHALL own its evaluation evidence, results, candidate branches, and draft PRs. The
factory SHALL preserve those artifacts and record their locations and candidate links.
Factory logs SHALL be kept separately from suite-owned evidence. Evidence under the
factory's artifact root SHALL remain available until the evidence retention rule in
`factory-operations` removes it. That rule SHALL keep each repetition's result and
provenance records and SHALL NOT delete candidate branches or PRs. Once the claim is
terminal, the factory SHALL reduce the candidate checkout to its served build, as "Slim
finished attempts" in `factory-operations` defines.

The suite worktree needed for human review SHALL remain available until the first of these
events:

- the reviewed item moves to Done, when the worktree cleanup policy in `factory-operations`
  applies;
- the request's human-review commands expire under `factory-eval-reporting`, when the
  terminal release in `factory-operations` applies.

A cancelled or superseded request's suite worktree SHALL be released under that terminal
release. The factory SHALL NOT delete candidate branches or PRs, merge changes, or perform
human review.

#### Scenario: Finish or stop a request

- **WHEN** a request completes, is cancelled, or stops after exhausting recovery
- **THEN** existing evaluation evidence and candidate links remain available for inspection
- **AND** artifacts for repetitions ready for human review remain usable by the retained suite's human-review command until the reviewed item moves to Done or the command expires

#### Scenario: Prune a settled eval's evidence

- **WHEN** an eval claim has been Done for longer than the configured retention and nothing still needs its evidence
- **THEN** its logs, session state, and agent output under the artifact root are removed
- **AND** its result and provenance records, candidate branches, and PRs remain

#### Scenario: Release an expired review worktree

- **WHEN** a settled eval's human-review commands have expired and the expiry comment has been delivered
- **THEN** its suite worktree is removed
- **AND** its captured results, result records, candidate branches, and PRs remain

### Requirement: Interpret suite failure ownership and resumability

The suite adapter SHALL consume `evaluation_status`, `failure.owner`, `failure.code`, and `resumable` from suite results together with the independently reported `product_verdict`. A suite-confirmed non-resumable implementation-workflow failure SHALL settle as a failed repetition and allow later repetitions, as defined in `factory-claim-lifecycle`. It SHALL NOT be relabeled a product failure when the suite reports the product verdict as unavailable. A workflow failure that the suite marks resumable SHALL retain bounded technical recovery. Recognized provider quota SHALL use the separate hold policy; a failure's phase or owner alone SHALL NOT imply a quota limit or a conclusive product failure.

#### Scenario: Preserve an unscored workflow failure

- **WHEN** the suite reports `implementation-workflow-failed`, `failure.owner=implementation-workflow`, and `resumable=false` with `product_verdict=unavailable`
- **THEN** the adapter reports a settled failed repetition with its workflow failure details
- **AND** it preserves the unavailable product verdict and missing score without requesting human judging

### Requirement: Preserve execution and product outcomes separately

The factory SHALL read the suite's result artifacts as the authority for established outcomes when available. It SHALL retain execution status separately from the tested product's verdict. An exit code alone SHALL NOT establish product quality, and unavailable results SHALL NOT be invented. Board verdicts and human-review handoff SHALL follow `factory-eval-reporting`.

#### Scenario: Record product evidence alongside an execution error

- **WHEN** the suite records a product failure and a subsequent execution or evaluation error
- **THEN** both facts remain available for reporting
- **AND** the execution error does not erase the established product evidence

#### Scenario: Receive an exit code without a product result

- **WHEN** execution ends without a suite-established product verdict
- **THEN** the factory records the execution outcome while leaving product quality unavailable

### Requirement: Run against the claim's frozen fixture revision

When a claim recorded a fixture revision, every invocation of the and-scene suite for that claim SHALL pass the full recorded SHA through the suite's `--fixture-ref` argument. This covers each repetition's initial launch, a fresh retry after an attempt that stopped before checkpoint creation, and a `--resume` recovery. It applies under both Docker and Fly execution. Each such invocation SHALL also pass the and-scene fixture repository `https://github.com/Codagent-AI/and-scene.git` through the suite's `--repo` argument. That is the repository admission certified the commit against, so the suite clones the fixture from it whatever repository default the pinned harness carries. The factory SHALL NOT pass the requested ref name, re-resolve the ref, or substitute the harness's own fixture pin. The suite clones and checks out the fixture itself, so the factory SHALL NOT create a fixture worktree, and the Fly image build and Machine transport SHALL be unchanged.

When a claim recorded no fixture revision, the invocation SHALL NOT include `--fixture-ref` or `--repo`, so the suite uses the fixture its pinned harness selects, exactly as before this change.

Before launching an attempt for a claim with a fixture revision, selected-suite readiness SHALL verify that the claim's pinned harness accepts both `--fixture-ref` and `--repo`. If it does not, the factory SHALL report an actionable readiness problem naming the harness commit and follow the readiness-hold behavior in `factory-claim-lifecycle`. No attempt SHALL launch and no execution attempt or recovery retry SHALL be consumed. Support SHALL be judged from the pinned harness's `run.sh` option dispatch, which must contain a `--fixture-ref` case and a `--repo` case. Readiness SHALL NOT start a dry run or contact Fly for this check.

If the suite fails to fetch or check out the frozen fixture commit, for example because the commit's only branch was deleted after admission, the attempt SHALL follow the existing failure and recovery policy for a suite failure. The factory SHALL NOT substitute another fixture.

The recorded evaluated environment SHALL keep these three things distinguishable:

- the requested fixture ref and the frozen fixture SHA, which are requested inputs;
- the fixture commit the suite reports it checked out, which is observed provenance;
- the harness's default fixture pin, for claims without a fixture revision.

The requested ref and the frozen SHA SHALL be read from the claim's frozen inputs (`settings.fixture_ref` and `revisions.fixture`). The observed commit is the suite's own evidence in the repetition's retained artifact directory: `result.json` `candidate_source.fixture_commit`, and `proof-metadata.json` `repo`, `fixture_ref`, and `fixture_commit`. The factory SHALL retain that evidence unmodified with the repetition's other suite-owned evidence. The factory SHALL NOT itself compare the observed commit with the frozen SHA: the suite checks out the exact SHA it was given, and its resume checks revalidate the recorded fixture.

#### Scenario: Launch a repetition with a pinned fixture

- **WHEN** a claim with fixture revision `F` launches a repetition under Docker or Fly execution
- **THEN** the suite invocation includes `--fixture-ref F` with the full SHA and `--repo https://github.com/Codagent-AI/and-scene.git`, alongside the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting

#### Scenario: Recover a repetition with a pinned fixture

- **WHEN** a claim with fixture revision `F` recovers an interrupted repetition, with or without `--resume`
- **THEN** the recovery invocation includes `--fixture-ref F` and the same `--repo` value, and the suite's resume checks compare saved evidence against `F`

#### Scenario: Launch a default repetition

- **WHEN** a claim without a fixture revision launches or recovers a repetition
- **THEN** the suite invocation contains no `--fixture-ref` or `--repo` argument and is otherwise unchanged from before this change

#### Scenario: Pinned harness lacks fixture selection

- **WHEN** a claim with a fixture revision is ready to launch and its pinned harness does not accept `--fixture-ref` or `--repo`
- **THEN** no attempt launches, the claim is held with a readiness problem naming the harness commit, and no execution attempt or recovery retry is consumed

#### Scenario: Pinned harness defaults to another repository

- **WHEN** a claim with fixture revision `F` launches with a pinned harness whose own repository default is not `https://github.com/Codagent-AI/and-scene.git`
- **THEN** the invocation passes `--repo https://github.com/Codagent-AI/and-scene.git`, so the suite clones the fixture from the repository admission certified `F` against

#### Scenario: Fixture commit disappears after admission

- **WHEN** a claim's frozen fixture commit is no longer fetchable from the and-scene origin when a later repetition runs
- **THEN** the suite's checkout failure is handled as a suite failure under the existing failure and recovery policy
- **AND** the factory does not run the repetition against a different fixture

#### Scenario: Inspect the fixture used by a repetition

- **WHEN** the user inspects the saved evaluation details of a repetition from a claim with a fixture revision
- **THEN** the requested fixture ref and the frozen fixture SHA are identifiable from the claim's frozen inputs
- **AND** the fixture commit the suite observed is identifiable from the repetition's retained `result.json` (`candidate_source.fixture_commit`) and `proof-metadata.json`, unmodified by the factory

