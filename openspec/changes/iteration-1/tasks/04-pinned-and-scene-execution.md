# Task: Integrate pinned and-scene execution and worktree cleanup

## Goal

Execute the approved suite using retained immutable inputs, preserve its outcome/resume contracts, deliver exact review handoffs, and remove only reviewed worktrees when Done is observed.

## Background

All repository-relative code paths below are in `agent-factory` unless a sibling repository is explicitly named. Planning sources are `openspec/changes/iteration-1/proposal.md`, `design.md`, the cited files under `specs/`, and `test-plan.md` in that same change directory. Read the relevant design sections and the approved test plan; the excerpts below preserve the requirements and assigned automated obligations verbatim.

The repository begins as a Python 3.12/uv scaffold: `src/agent_factory/__init__.py`, `pyproject.toml`, `README.md`, and an empty `tests/` tree. The package areas named below are intended implementation locations, not claims that an API already exists. Use small protocols and dataclasses with static registration for only `eval` and `and-scene`. Preserve GitHub intent, SQLite execution history, and suite-owned evidence as separate authorities. Keep organization names, logical field mappings, local paths, and defaults configurable. Do not add more work kinds, suites, workers, queue/storage providers, a dynamic plugin loader, a generic outbox, or another evaluator.

The App, native Eval type, and board were already provisioned and confirmed; reuse the records in `openspec/changes/iteration-1/setup/`. That prior confirmation satisfies the pre-controller board-layout gate and is not evidence of implementation acceptance. Public configuration and documentation must use portable paths and contain no private credentials.

Read the design sections “Interface contracts”, “Worktrees and suite invocation”, “Progress, quota, and failure classification”, and “Reporting and handoff”. Implement concrete suite behavior under `src/agent_factory/suites/and_scene/` and finish the eval preparation/presentation path under `src/agent_factory/work_kinds/eval/`. Consume the production controller/store/supervisor and installed CLI contracts for normal admission, process observation, recovery, and reporting. Keep paths, score details, and suite filenames out of the generic core.

The companion repository is `/Users/paul/codagent/agent-evals`. Inspect the selected deployed revision's `evals/agent-runner/and-scene/run.sh`, `human-review.sh`, `lib/phases.mjs`, `lib/outcomes.mjs`, and `lib/result.mjs`; inspect the selected Runner revision's `scripts/sandbox-run.sh` in `/Users/paul/codagent/agent-runner`. Working checkout contents are not an implicit deployment pin. The live source inspected during planning still contains calibration-gate code; the approved design requires the separately delivered gate removal (`2fc8443` or its integrated equivalent) in the chosen deployed harness. Integrate that prerequisite only after any existing evaluation using the live checkout has finished. Do not implement the separately owned automated-score-cutoff change, assume it is deployed, or bypass missing compatibility. Record an actionable readiness failure if the selected full harness commit does not provide the approved suite contract.

Own the final shared-configuration harness pin: update `config/codagent.toml` to the full immutable `agent-evals` commit containing the companion linked-worktree mount change, calibration-gate removal, and separately delivered outcome contract. Replace any provisional execution pin and update its recorded readiness gaps. Verify the selected commit’s contents, not just a branch name or an uncommitted working checkout. Run `E2E-004` against a detached harness worktree at that exact configured SHA and record the same SHA in the evidence alongside the selected Runner/Skills commits. This ties the configured deployment to the wrapper actually tested, as required by `openspec/changes/iteration-1/design.md` (“Configuration and credentials” and “Worktrees and suite invocation”), `specs/factory-operations/spec.md` (“Apply shared deployment changes through explicit updates”), and `test-plan.md` (`E2E-004`). Preserve the normal companion-repository publication/deployment sequence and existing-claim pins; no additional merge gate or automatic live-checkout update is introduced.

Freeze Runner/Skills resolved commits, configured full harness SHA, suite identity, role profiles, validator setting, repetitions, fixture/reference pins, rubrics, workflow, judge profile, and execution defaults. Fetch through configured source repositories and create clean detached owned worktrees under `worktrees/<claim-id>/{runner,skills,evals}/`; never switch shared working checkouts. Persist partial preparation/ownership references so retry/restart does not repoint or leak ownership. A distinct claim has distinct worktrees. Preserve completed units and accepted pins across source/default changes, quota waits, and recovery. Use stable Git-ref-safe claim/repetition artifact basenames with separate attempt logs; recovery retains the same evidence path while recording another attempt.

Build argv arrays with `shell=False` for the pinned `evals/agent-runner/and-scene/run.sh`: `--run-agent`, `--agent-runner-dir`, `--agent-skills-dir`, `--artifact-dir`, `--env-file`, supported lead/implementor/reviewer flags, and accepted validator participation. The suite retains control of workflow execution, Docker, model-auth forwarding, Claude waits, candidate branches/draft PRs, scoring, and review. Construct an allowlisted environment with separate candidate-delivery credentials; never forward the factory App key/token. Check entry points, fixtures, selected launcher arguments/mount capability, and required suite credentials/files before reserving execution. Do not require calibration receipts or pass removed calibration-record arguments.

Include the small companion mount change in the actual and-scene wrapper. Discover each mounted linked source worktree's Git directory/common directory on the host, preserving the paths `.git`/`commondir` resolve to, and pass backing metadata read-only through Runner's existing repeated `--docker-run-arg`. Source mounts remain read-only. Do not rewrite metadata, relax clean-source checks, replace the requested Runner revision, or add a container-name passthrough. Verify the actual wrapper/selected-launcher combination rather than inventing an equivalent Docker command. The host runs controller/supervisor; the existing suite creates one container per repetition attempt, and controller restarts preserve survivors.

Use `--resume` only with valid supported saved state. A prior attempt proven stopped before checkpoint creation and before candidate execution may retry without `--resume` under the same unit identity and existing retry budget. Corrupt, incompatible, or unexplained missing state never permits a fresh run; preserve diagnostic/evidence and honor rejection. Read progress from known logs/state plus output. In particular recognize the suite's bounded Claude wait from the pre-wait message, because its structured wait event is persisted only after the sleep. Normalize these observations for the supervisor's persistent timers. Parse Codex quota only from actual recognized diagnostics, including saved judge errors in result/state/logs; source fixtures from captured or authoritative documented diagnostics and record the source. No generic error or merely missing reset may trigger a five-hour hold.

Read `result.json` as the primary available outcome and preserve `evaluation_status`, `failure.owner`, `failure.code`, `resumable`, and independent `product_verdict`. Non-resumable workflow failure settles one repetition without inventing a product score/verdict; resumable workflow/harness failure follows bounded recovery; recognized quota has its own hold. Consume the suite's 39/70 failure and eligible 40/70 result without computing a threshold locally. Missing/incomplete scores/costs remain unavailable, and later technical errors do not erase established product evidence. Aggregate mixed results through the eval handler and deliver via generic reporting. Record immutable Docker image identity from the supervisor's actual observed container `.Image`, never acceptance-time guesses or later tag lookup.

Render a correctly quoted absolute retained `human-review.sh --run-dir` command for each reviewable repetition, even when another fails. State the Mac holding the files and validity until the reviewed item moves to Done; failed/incomplete units get explanations. Never execute human judging. Include all required pins/settings, observed provenance, per-repetition evidence and candidate links, and the exact `runner@<7> skills@<7> evals@<7>` display based on full recorded SHAs.

Complete cleanup in the normal controller cycle after execution/closure reconciliation. Only a reviewed owned item moving Review to Done releases its recorded worktrees via Git worktree removal. Keep them during running/waiting/Review; a live Done drag restores Running before cleanup. Persist each removal and last error, tolerate already removed worktrees, and retry partial failure after restart while other jobs proceed. Preserve source checkouts, other claims, results, logs, SQLite history, candidate refs, and PRs. The configurable local root defaults to `~/.agent-factory/`; evidence and candidate outputs remain until manual cleanup. Document suite prerequisites, pins, the companion patch/deployed revision, review-command lifetime, and storage cleanup in `docs/suite-integration.md` and the relevant README/setup links.

Scope of shared scenarios: own concrete Git preparation/retention, suite readiness/planning/observation/outcomes, review-command content, Docker wrapper compatibility, and reviewed-worktree cleanup. Existing generic admission, supervision, and durable delivery remain real in end-to-end tests. This outcome completes the automated Git/suite/Docker boundaries without live model calls or GitHub mutations.

## Spec

The following requirement blocks are copied verbatim from the approved specifications. For shared requirements, the Background states this delivery unit’s portion; retain the complete scenario semantics.

Source: `openspec/changes/iteration-1/specs/factory-eval-intake/spec.md`.

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve Runner, Skills, and `agent-evals` revisions to immutable commits before execution. `agent-evals` is the evaluation harness and may contain multiple suites; iteration 1 SHALL support `and-scene` as the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

Source: `openspec/changes/iteration-1/specs/factory-eval-execution/spec.md`.

### Requirement: Evaluate Runner and Skills using an existing suite

The factory SHALL support evaluation of requested Agent Runner and Agent Skills revisions through the existing `agent-evals` harness. Iteration 1 SHALL support `and-scene` as the default eval suite. The deployed harness version SHALL be explicitly configured as a full commit SHA, recorded, and retained as part of the evaluation environment; requests SHALL NOT select or evaluate different `agent-evals` revisions.

Suite-specific readiness checks, invocation, evidence interpretation, supported resume behavior, and human-review instructions SHALL remain separate from generic queueing, scheduling, claim tracking, and recovery. Adding another suite to the harness SHALL be able to reuse that generic behavior. Implementing additional suites or a dynamic plugin system is outside iteration 1.

#### Scenario: Evaluate selected component revisions

- **WHEN** an accepted request specifies Runner and Skills refs
- **THEN** the factory evaluates their resolved revisions with the default `and-scene` suite
- **AND** records the suite identity and deployed harness revision as part of the test environment

#### Scenario: Add a suite later

- **WHEN** another suite is integrated with `agent-evals` in a later change
- **THEN** its suite-specific behavior can be supplied without replacing the factory's generic queueing, claim tracking, scheduling, and recovery behavior

### Requirement: Execute against clean pinned worktrees

The factory SHALL prepare factory-owned clean Git worktrees for the accepted Runner and Skills commits and retain the deployed `agent-evals` version in a pinned worktree. These worktrees SHALL remain associated with the claim for its lifetime, including automatic deferrals and recovery, and SHALL NOT be repointed for another claim. Repetitions SHALL use the same accepted inputs rather than re-resolving moving refs.

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

### Requirement: Invoke the suite with accepted execution settings

For `and-scene`, the factory SHALL invoke `evals/agent-runner/and-scene/run.sh` from the retained harness worktree using its agent-execution mode. It SHALL supply the pinned Runner and Skills worktree paths, complete lead/implementor/reviewer profiles, the repetition's artifact directory, and required environment-file paths through the suite's supported interface. It SHALL pass the accepted `skip_validator` setting to the suite. The factory SHALL use the suite's existing workflow and execution behavior rather than implement a second evaluator. The existing suite SHALL launch one container per repetition attempt, with the controller and supervisor on the host. A recovery attempt SHALL reuse the repetition's artifacts through a new container; controller restart SHALL preserve verified surviving execution. Integration SHALL verify the selected Runner sandbox launcher supports required mounts and arguments, and expose any missing compatibility as an actionable readiness problem before model execution.

Selected-suite readiness SHALL be verified before execution. Calibration SHALL remain an optional suite-maintainer diagnostic; the factory SHALL NOT require a calibration receipt or pass removed calibration-record arguments. Unavailable prerequisites SHALL follow the readiness-hold behavior in `factory-claim-lifecycle`.

#### Scenario: Launch an accepted repetition

- **WHEN** the claim is eligible and its prerequisites are available
- **THEN** the suite receives the saved role profiles, component worktree paths, artifact directory, environment paths, and validator setting
- **AND** the invocation uses the retained harness version

#### Scenario: Fail selected-suite readiness

- **WHEN** the selected suite's required entry-point or fixture files are unavailable in the pinned environment
- **THEN** no evaluation starts and the factory reports the readiness problem without consuming an execution attempt or recovery retry

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

The factory SHALL resume interrupted `and-scene` work with a valid checkpoint through the suite's `--resume` interface using saved inputs and the artifact directory, subject to the lifecycle recovery budget. When reconciliation establishes that an attempt stopped before creating a checkpoint and before candidate execution, the factory SHALL retry without `--resume` using the same repetition identity, inputs, artifact directory, and remaining retry budget. A corrupt checkpoint or missing state alongside evidence of execution SHALL NOT authorize a fresh start. It SHALL respect the suite's checks of input revisions, settings, candidate identity, and evidence. A rejected resume SHALL NOT be bypassed by silently changing inputs, discarding evidence, or starting a fresh evaluation under the same repetition identity. Completed repetitions SHALL remain completed.

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

The factory SHALL retain the selected suite identity, full Runner and Skills commit SHAs, deployed `agent-evals` commit SHA, fixture and reference pins, rubric identities, workflow, judge profile, role settings, validator setting, and repetition count. It SHALL record the actual container's immutable Docker image ID after the image is built rather than infer it from a mutable tag or claim that a future image digest was known when the request was accepted. If that observation is unavailable, provenance SHALL say so rather than substitute a later tag lookup. These records SHALL distinguish requested inputs from observed execution provenance.

#### Scenario: Inspect a completed evaluation

- **WHEN** the user inspects a repetition's saved evaluation details
- **THEN** the tested Runner and Skills revisions and the harness, suite, scoring inputs, and execution settings used to evaluate them are identifiable
- **AND** the recorded image identifies the image actually used

### Requirement: Preserve suite-owned evidence and candidate outputs

The suite SHALL own its evaluation evidence, results, candidate branches, and draft PRs. The factory SHALL preserve those artifacts and record their locations and candidate links. Factory logs SHALL be kept separately from suite-owned evidence. Artifacts SHALL remain available until manual cleanup. The suite worktree needed for human review SHALL remain available until the reviewed item moves to Done, when the worktree cleanup policy in `factory-operations` applies; iteration 1 SHALL NOT automatically prune evidence, delete candidate branches or PRs, merge changes, or perform human review.

#### Scenario: Finish or stop a request

- **WHEN** a request completes, is cancelled, or stops after exhausting recovery
- **THEN** existing evaluation evidence and candidate links remain available for inspection
- **AND** artifacts for repetitions ready for human review remain usable by the retained suite's human-review command until the reviewed item moves to Done

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

Source: `openspec/changes/iteration-1/specs/factory-eval-reporting/spec.md`.

### Requirement: Consume suite-established outcomes

The factory SHALL use suite result artifacts, including `result.json` for `and-scene`, as the authority for established execution and product outcomes. It SHALL preserve execution status separately from product verdict and SHALL NOT infer product quality from an exit code alone. A suite-established product failure SHALL remain visible even if a later technical error also occurs.

The integration SHALL use the separately delivered `and-scene` behavior that reports a completed automated subtotal below 40 out of 70 as a product failure before human review. The factory SHALL consume that verdict rather than calculate or enforce the threshold itself. Implementing that suite change is outside this change. A score of exactly 40 SHALL meet the automated subtotal minimum, subject to other suite requirements; missing or incomplete scoring SHALL NOT be treated as a below-threshold product result.

#### Scenario: Report an automated score below the minimum

- **WHEN** the suite reports a completed score of 39/70 with `product_verdict=fail` because it is below the automated minimum
- **THEN** the factory reports that repetition as failed with its score and the suite's reason
- **AND** it supplies no human-review command or technical recovery retry for that product failure
- **AND** remaining requested repetitions may continue under the admission controls

#### Scenario: Reach the minimum exactly

- **WHEN** the suite reports 40/70 and a result ready for human review
- **THEN** the factory reports the repetition as awaiting human review rather than failed or officially passed
- **AND** it supplies the human-review command

#### Scenario: Encounter incomplete scoring

- **WHEN** scoring cannot finish because of a technical problem and no product verdict is established
- **THEN** the factory reports the technical outcome and unavailable product verdict
- **AND** it does not turn missing scoring into a zero or below-threshold result

#### Scenario: Retain product evidence after a technical error

- **WHEN** a suite-established product failure is followed by a technical error
- **THEN** reporting preserves both the product failure and the technical error

### Requirement: Report traceable inputs and per-repetition results

The factory SHALL post the frozen evaluation inputs and report each repetition independently. Reporting SHALL include claim and suite run identities, suite identity, full Runner and Skills revisions, the deployed harness revision, accepted role and validator settings, repetition count, and relevant pinned evaluation inputs. The Project's `Refs` field SHALL display `runner@<7> skills@<7> evals@<7>`, using the first seven characters of each recorded commit SHA. The harness revision SHALL be identified as the test environment version.

For each repetition, results SHALL include execution status, established product verdict, available automated subtotal out of 70, duration, available costs, artifact location, and recorded candidate branch and draft-PR links. Unstarted, interrupted, completed, and failed repetitions SHALL remain distinguishable. Missing scores, costs, or other result data SHALL be described as unavailable rather than zero or fabricated values. Reporting SHALL NOT present absolute results as evidence of improvement over a baseline.

#### Scenario: Report mixed repetition outcomes

- **WHEN** one repetition is ready for human review, another has a product failure, and another remains unstarted
- **THEN** the issue shows each repetition's own status, available score, evidence location, and candidate links
- **AND** the aggregate board verdict does not hide those distinctions

#### Scenario: Omit unavailable cost data honestly

- **WHEN** a completed repetition lacks cost data
- **THEN** its result reports cost as unavailable while retaining its other available results

### Requirement: Provide executable human-review instructions

Each repetition reported by the suite as ready for human review without a product failure SHALL receive its own copyable review command in its completion comment. For `and-scene`, the command SHALL invoke the retained suite's `human-review.sh` with `--run-dir` pointing to that repetition's actual artifact directory. Script and artifact paths SHALL be absolute and safely quoted, with no placeholders for the user to fill in. The comment SHALL state that the command runs on the Mac mini holding those files and remains usable until the reviewed item moves to Done, which triggers removal of its suite worktree.

Failed or incomplete repetitions SHALL receive explanations rather than commands presenting them as ready for human review. A repetition that is ready SHALL receive its command even when another repetition in the same request failed. The factory SHALL NOT perform human review, invent human ratings, or assign an official pass.

#### Scenario: Finish a repetition ready for review

- **WHEN** the suite finishes a repetition ready for human review
- **THEN** the factory posts its available automated score and a complete command for reviewing that exact repetition on the Mac mini

#### Scenario: Include reviewable work in a failed request

- **WHEN** one repetition is ready for human review and another establishes a product failure
- **THEN** the reviewable repetition still receives its own review command
- **AND** the failed repetition does not

### Requirement: Apply aggregate board verdicts without hiding partial results

When all requested repetitions complete their automated evaluation or settle with a confirmed non-resumable implementation-workflow failure, the factory SHALL move the issue to Review. If any repetition has a suite-established product failure or a confirmed non-resumable implementation-workflow failure, the aggregate Verdict SHALL be `failed`; otherwise it SHALL be `pending-human-review`. The factory SHALL never assign `passed` in iteration 1 and SHALL NOT close the issue as part of automated completion.

If a technical failure exhausts its recovery retry, the lifecycle's stop behavior SHALL take precedence: move to Review with `infra-error`, preserving all completed results and explaining which repetitions remain unstarted. Any already-established product failures SHALL remain visible in the results comment. While unfinished work is automatically deferred, the card SHALL use Ready with the applicable `quota-deferred` or `infra-error` verdict so the existing claim can continue under the intake rules.

#### Scenario: Complete without a product failure

- **WHEN** all repetitions finish ready for human review without a suite-established product failure
- **THEN** the card moves to Review with `pending-human-review`
- **AND** the issue remains open without an official pass assigned by the factory

#### Scenario: Complete with a failed repetition

- **WHEN** all repetitions finish and at least one has a suite-established product failure
- **THEN** the card moves to Review with `failed`
- **AND** the issue retains the separate results and review commands for any reviewable repetitions

#### Scenario: Complete with a non-resumable workflow failure

- **WHEN** all repetitions have settled and at least one has a suite-confirmed non-resumable implementation-workflow failure
- **THEN** the card moves to Review with `failed` and explains the workflow failure
- **AND** the report preserves the suite's separate product verdict, including unavailable, and any other repetition's review command

#### Scenario: Stop after exhausting technical recovery

- **WHEN** a technical recovery retry fails before all repetitions finish
- **THEN** the card moves to Review with `infra-error`
- **AND** the comment preserves completed results, any established product failures, and the list of repetitions left unstarted

#### Scenario: Defer unfinished work automatically

- **WHEN** a quota or recoverable infrastructure interruption defers an unfinished claim
- **THEN** the card returns to Ready with the corresponding deferral verdict
- **AND** the factory preserves the claim's frozen inputs, completed results, and retry history

Source: `openspec/changes/iteration-1/specs/factory-operations/spec.md`.

### Requirement: Keep local data under a configurable root

The default local root SHALL be `~/.agent-factory/`, configurable by the operator. Factory configuration, SQLite state, controller logs, owned worktrees, and evaluation artifacts SHALL reside under the selected root. Suite-owned evidence and factory logs SHALL remain separate. Public examples SHALL use portable paths rather than Paul's machine-specific locations.

Iteration 1 SHALL retain evidence and candidate outputs until manual cleanup. Setup documentation SHALL explain how to locate logs and artifacts, inspect storage use, and perform operator-managed cleanup while preserving work still needed for execution, recovery, or human review. Automatic evidence and candidate-output pruning is outside this change; factory-owned worktrees follow the cleanup requirement below.

#### Scenario: Choose a different local root

- **WHEN** the operator configures another local storage root
- **THEN** the factory uses that root for its configuration, state, logs, owned worktrees, and artifacts
- **AND** commands and result reports identify the actual paths in use

#### Scenario: Retain evidence after handoff

- **WHEN** an evaluation is handed off for human review
- **THEN** its artifacts and required suite files remain available for the posted review command until the reviewed item moves to Done; evidence remains retained after worktree cleanup

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees. It SHALL preserve results, logs, SQLite history, candidate branches, and PRs. Worktrees SHALL remain available while work is running, waiting, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees and SHALL NOT remove shared source checkouts or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned Runner, Skills, and evals worktrees are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, or in Review, including an active item incorrectly dragged to Done
- **THEN** its worktrees remain available for execution, recovery, and human judging
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's worktrees fails or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

## Test Plan

Own `INT-003`, `E2E-001`, and `E2E-004` in full. Adapter integration uses versioned real-file fixtures and controlled observations; the Git CLI journey uses real repositories/worktrees/storage/commands with a controlled suite executable; the Docker journey uses the actual wrapper/launcher with real test-owned containers and a controlled command replacing model work. These are three distinct approved boundaries, not interchangeable substitutes. Read every setup, action, assertion, and cleanup detail below, including pre-checkpoint recovery in the Git journey and locked-worktree cleanup retry. Register markers and provide a required Docker-enabled check where ordinary hosts lack Docker. Retag only a test-owned image and preserve the same-image decoy. Record fixture sources and selected launcher/harness revisions in test evidence.

Use implementation-time TDD for the copied specification scenarios within the scope stated above. Keep unit cases close to the behavior rather than reproducing every case at every test layer. Use isolated roots, temporary SQLite/files, and controlled external responses; automated verification must not read the operator's database, live queue, or private credentials. Run the relevant collected pytest tests and the repository's Ruff format/lint and strict Pyright checks. Register any markers in `pyproject.toml`; pytest exit code 5 or an unexecuted required platform test does not count as passing.

Implementors do not execute `AT-001`, `AT-002`, or `HT-001`. Preserve their requirements in `openspec/changes/iteration-1/test-plan.md` for the separate acceptance stage: real issue-event routing, one real suite repetition and launchd restart, and the operator's board-drag-to-API observation. Controlled automated evidence cannot satisfy those live boundaries. Do not change the approved definition artifacts or weaken their testing obligations.


### INT-003: Suite adapter commands, progress, and result contracts

- Covers: Accepted execution settings; suite-specific readiness and resume; progress/runtime accounting; quota interpretation; provenance; result/score reporting and human-review command generation.
- Boundary: Production eval handler and and-scene adapter reading real temporary artifact files; controlled process/clock responses and representative versioned artifacts from the deployed suite interface.
- Setup: Fixtures for automated completion ready for review, a completed 39/70 product failure, exactly 40/70 with eligible outcome, incomplete scoring, and product evidence followed by a technical error. Include actual structured suite fixtures for a non-resumable workflow-side-effect violation, a resumable workflow failure, and a recoverable harness failure. Include known suite log/state updates, a bounded Claude wait, a recognized Codex rate-limit diagnostic with a reset, the same recognized limit without an interpretable reset, and an unrelated execution error. Source Codex detection fixtures from captured or authoritative documented CLI diagnostics and record their source; invented parser examples do not establish compatibility. Use paths containing spaces and shell metacharacters as ordinary argument data.
- Action: Build initial, valid-checkpoint resume, and proven pre-checkpoint retry execution plans; reject corrupt or unexplained missing checkpoint states; advance logs/state while stdout stays quiet; enter/leave recognized quota waits; read results and aggregate mixed repetition outcomes; render review commands.
- Assertions: Arguments carry all accepted profiles, worktrees, environment-file references, validator setting, and stable artifact identity without shell evaluation. Resume retains the same inputs and path. A proven pre-checkpoint interruption retries without `--resume` under the same budget; corrupt or unexplained missing state never falls back to a fresh run. Readiness and invocation work without a calibration receipt and pass no removed calibration-record argument. Known artifact activity counts as progress; bounded quota waits affect the correct timers; restart reconstruction does not reset timing. The adapter consumes suite-established failure and never calculates a substitute threshold or treats missing scores as zero. Retry exhaustion and completed product failures produce the required distinct board outcomes. A non-resumable workflow failure settles its repetition and permits later repetitions, contributing board Verdict failed without changing an unavailable product verdict; a resumable workflow failure keeps the bounded retry. Only recognized Codex rate limits create holds; the five-hour fallback requires a recognized limit and generic errors follow ordinary recovery. Eligible repetitions get correctly quoted absolute review commands even in a mixed-result claim; failed/incomplete repetitions do not. Image provenance is recorded from observed execution, not fabricated before build.
- **Constraints:** Fixtures must preserve real suite field names and distinctions; a fixture mismatch is an integration failure, not a reason to relax the result contract. This does not re-test the suite's scoring algorithm. The companion linked-worktree mount change is verified separately by E2E-004.
- Execution: `tests/integration/`; ordinary automated checks without Docker, model calls, or candidate PR creation.

### E2E-001: Real Git worktrees through factory admission and continuation

- Covers: Clean pinned worktrees; frozen inputs; distinct claim/repetition identity; continuation and fresh requests; configurable repository paths; worktree cleanup after review reaches Done.
- Surface: Delivered factory CLI, including `tick`, operating through the normal admission path.
- Setup: Isolated factory root and SQLite; real temporary Git repositories/remotes representing Runner, Skills, and the deployed harness, with the minimal supported fixture layout. Local GitHub stub supplies the request. A controlled executable stands in for the suite and records its received arguments; Git, worktree creation, revision resolution, and persistence are real.
- Journey: Admit a request, finish one unit, defer the remaining unit, advance the source branches and configured defaults, and continue the same claim. Then admit a separate fresh request. Finish the first claim and observe retention in Review, move its reviewed card to Done in the GitHub stub, and tick again. Exercise a partial cleanup failure using a test-owned locked Git worktree, restart the controller, remove that lock, and retry while another eligible job can proceed.
- Assertions: The initial claim uses clean detached worktrees at its original SHAs throughout continuation. Source working checkouts remain unchanged. A second claim has separate worktrees and can resolve the newer inputs without repointing the first. Repetitions have distinct Git-ref-safe artifact identities, while a resumed unit retains its path. Reported pins agree with the actual worktrees. Shared configuration changing outside the installed version does not silently change the running installation. Worktrees survive waits and Review, then disappear from disk and Git worktree registrations after reviewed Done is observed. Results, logs, SQLite history, candidate refs, and other claims/source checkouts remain. Failed cleanup persists across restart and retries without blocking other jobs; already-removed worktrees are tolerated. Verify candidate PR preservation through the stub API: cleanup sends no PR deletion or closure requests.
- Execution: `tests/e2e/`; automated checks on hosts with Git installed. No GitHub network, Docker, or model calls. Include interruption before the controlled suite creates its checkpoint and verify recovery uses the same repetition without `--resume` and consumes only the existing retry budget. Clean only the test-owned repositories/worktrees after assertions.

### E2E-004: Real Docker linked-worktree mounts, provenance, and ownership

- Covers: Companion suite-wrapper mount support; selected Runner launcher compatibility; in-container Git provenance; exact container ownership and immutable image identity.
- Surface: Delivered suite/Runner sandbox wrapper path and factory container observation/termination code, using real Docker and Git. A controlled command replaces agent/model execution.
- Setup: Test-owned detached linked Runner/Skills/harness worktrees at the selected deployment commits, isolated artifacts, an explicitly test-owned image tag supplied through the launcher's `--image` option, and a Docker-enabled host. Use the actual suite wrapper mount construction and actual selected Runner launcher, not a separately invented Docker command claiming to test their compatibility. Expose the wrapper's command assembly to this controlled test if needed. Create a second test-owned decoy container from the same image with a different artifact mount.
- Journey: Start the controlled command through the wrapper/launcher combination. Inside its container, run Git revision and cleanliness checks against the mounted source worktrees and confirm the expected pins. Observe container identity and `.Image`, retag the test-owned image to a different test image, then stop only the recorded owned container using the factory's ownership checks. Exercise a contradictory artifact mount and verify termination is refused.
- Assertions: Backing Git metadata is accessible read-only; provenance checks succeed without rewriting `.git` or changing host source checkouts. Source and metadata mounts remain read-only. Recorded image identity matches the running container's immutable ID despite tag movement. Termination requires the recorded container identity and exact artifact mount; the same-image decoy survives. Missing launcher capability is reported as a readiness problem before model execution.
- Execution: `tests/e2e/`; required on a Docker-enabled host. This uses real containers and may build the selected Runner image, but consumes no model calls or GitHub mutations. Remove only test-owned containers, images/tags, and worktrees. Preserve unrelated host resources. Record a missing Docker environment as incomplete coverage, not a pass.

## Done When

- The production and-scene adapter invokes the retained pinned suite with accepted settings and safe argv/environment, reads real suite contracts, preserves execution/product distinctions and evidence, and selects resume versus proven pre-checkpoint retry without exceeding the lifecycle budget.
- The companion wrapper exposes required linked-worktree Git metadata read-only through the selected Runner's existing interface, with actionable readiness errors for missing compatibility and no required calibration receipt/argument.
- `config/codagent.toml` records the integrated full harness commit containing the required companion behavior and separately delivered suite prerequisites, and `E2E-004` evidence identifies that same configured commit. An earlier provisional pin, uncommitted patch, or test against a different harness revision does not satisfy this completion condition.
- Every reviewable repetition gets its executable absolute review command and honest provenance/results, including mixed failures; no suite threshold implementation, human rating, official pass, or automatic issue closure is added.
- Reviewed Done worktrees are removed from disk and Git registrations with durable retry, while active/waiting/Review worktrees, all evidence, candidate refs/PRs, and unrelated resources remain intact.
- `INT-003` and `E2E-001` pass with the approved controlled boundaries, and `E2E-004` passes on a Docker-enabled host using the actual wrapper/launcher and production ownership code. Missing Docker or selected-suite prerequisites remain incomplete coverage, not a silent pass.
- All copied scenarios within this concrete integration/cleanup scope pass. Companion changes, separately delivered suite prerequisites, fixture provenance, deployment pin, and portable operator documentation are reviewable; no live acceptance result is claimed.
