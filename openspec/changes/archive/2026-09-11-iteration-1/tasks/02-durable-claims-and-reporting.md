# Task: Persist and reconcile evaluation claims and GitHub reporting

## Goal

Turn the configured queue into a durable, deterministic claim lifecycle that preserves accepted inputs and recovery budgets and repairs reporting independently of execution.

## Background

All repository-relative code paths below are in `agent-factory` unless a sibling repository is explicitly named. Planning sources are `openspec/changes/iteration-1/proposal.md`, `design.md`, the cited files under `specs/`, and `test-plan.md` in that same change directory. Read the relevant design sections and the approved test plan; the excerpts below preserve the requirements and assigned automated obligations verbatim.

The repository begins as a Python 3.12/uv scaffold: `src/agent_factory/__init__.py`, `pyproject.toml`, `README.md`, and an empty `tests/` tree. The package areas named below are intended implementation locations, not claims that an API already exists. Use small protocols and dataclasses with static registration for only `eval` and `and-scene`. Preserve GitHub intent, SQLite execution history, and suite-owned evidence as separate authorities. Keep organization names, logical field mappings, local paths, and defaults configurable. Do not add more work kinds, suites, workers, queue/storage providers, a dynamic plugin loader, a generic outbox, or another evaluator.

The App, native Eval type, and board were already provisioned and confirmed; reuse the records in `openspec/changes/iteration-1/setup/`. That prior confirmation satisfies the pre-controller board-layout gate and is not evidence of implementation acceptance. Public configuration and documentation must use portable paths and contain no private credentials.

Read the design sections “Interface contracts”, “Persistent model”, “Controller cycle and admission”, and “Reporting and handoff”. Implement `src/agent_factory/store.py`, `src/agent_factory/controller.py`, and the request/aggregation behavior under `src/agent_factory/work_kinds/eval/`, using the configured `github` client and routing interface. This is an independently verifiable controller/handler/store boundary: external suite/process observations and time are controlled in integration tests, while production policy, API serialization, reporting, and SQLite are real. Do not embed a fake production evaluator or make a fake handler a public configurable work kind.

Use exactly the three designed application tables (`claim`, `run`, `settings`) with foreign keys, WAL, bounded busy timeout, short explicit transactions, and SQLite `user_version`. Never hold a transaction during network, Git, Docker, or suite operations. Store the parsed override fingerprint separately from versioned frozen settings, accepted SHAs, unit progress, reporting progress, and preparation/cleanup references. Unique claim/unit/attempt identity and a database constraint must permit at most one nonterminal run, including reserved startup. Preserve history for settled, cancelled, and superseded claims. Supervisors own observation/terminal columns; the controller owns aggregate/reporting/control columns and cancellation requests. Update owned columns without overwriting a concurrent writer's snapshot.

Define small `RequestSnapshot`, `FrozenSpec`, `WorkUnit`, `ExecutionPlan`, `Observation`, `AttemptResult`, and `ClaimPresentation` contracts with the contents described in design. A frozen spec is opaque versioned JSON to the generic core; only the eval handler owns repetitions, Runner/Skills settings, suite selection, and outcome presentation. The execution boundary carries an argv array, working directory, explicitly allowed environment/file references, progress sources, ownership hints, and resume mode. Keep App credentials out of this allowed environment. Interface tests may supply controlled execution observations without OS process supervision.

Parse only fenced `eval` TOML using `tomllib`. Support exactly the approved overrides, complete role triples, boolean `skip_validator`, and positive integer repetitions (reject booleans); an optional repetition ceiling rejects rather than truncates. Apply defaults only when freezing a new claim. Resolve through the work-kind preparation boundary and persist immutable accepted inputs before execution; the concrete Git/suite preparation may be controlled in this boundary's tests. Persist small pre-claim feedback receipts in `settings`, so invalid input gets red `needs-input` and one explanatory comment without manufacturing a claim/run or `infra-error`. Revalidate while paused, remove the label on correction, and restore it if only the label was removed.

Read Project items in paginated manual `POSITION` order, then filter configured source, issue marker/native type, factory ownership, open state, writer permission, valid request, and applicable holds. Priority/age never reorder the queue. Read native issue Type from issue data, not from the Project item’s field-value or grouping connections: the provisioned Project legitimately omits that native field in those connections. Follow `openspec/changes/iteration-1/design.md` under “GitHub routing and board representation” and the recorded evidence in `openspec/changes/iteration-1/setup/project-board.md`; absence from the Project connection does not establish that an issue lacks Type Eval. Reconcile tracked execution and closure first. Correct idle owned Running to its true queued/handoff state; verified active work dragged to Ready/Review/Done returns to Running without a new claim, attempt, retry, or cleanup. Leave non-owned cards alone. Closure requests cancellation before any stale Running repair. An API outage delays board changes without inferring that known execution stopped.

Serialize cycles with an advisory lock shared by service and tick. Check factory pause, timezone/window (default 00:00 inclusive to 15:00 exclusive), quota, free-space/shared prerequisites, and suite readiness before each initial/repetition/recovery launch. Reserve no run for a prelaunch readiness hold. Pause and unrelated holds remain independent; recheck readiness without credential/configuration repair. Completed units remain complete; returning to Ready between units preserves the unfinished claim without inventing a deferral verdict. Queue reordering affects the next selection only.

Continuation uses the accepted parsed-request fingerprint and acknowledged field delivery, not changed defaults, formatting, or prose. An unchanged unfinished deferred claim resumes frozen inputs and budget. Changed parsed settings or a human clearing a successfully delivered Verdict on an otherwise eligible Ready card creates a fresh claim and retains prior history. An undelivered Verdict is not a human reset. Active edits cannot mutate execution or overlap work; a completed card merely dragged to Ready with its old verdict is outside scope.

Charge one technical recovery retry when admitting it and retain the charge across restarts and drags. Exhaustion settles the claim in Review/infra-error with later units unstarted. A normalized recognized Codex limit stores the reported reset or configured five-hour fallback from original detection, without a technical retry charge. A missing reset without recognized quota is ordinary failure. Resume after quota still obeys pause/window/readiness. A suite-confirmed product failure or non-resumable workflow failure settles its unit and permits later units; resumable workflow/harness failures retain bounded recovery. Preserve failure owner/code/resumability and independent product verdict, including unavailable. The suite supplies these outcomes; the generic controller computes no threshold.

Generate meaningful stable claim/unit/attempt events and per-repetition/aggregate presentations. Persist desired payloads, comment markers/IDs, acknowledgments, and per-field progress in claim JSON, not a general outbox. After lost comment acknowledgment search all comment pages before reposting. Refetch current fields/closure before repairs and regenerate desired values from current lifecycle state, preserving later human intent except approved Status correction. Record results before reports, and recover reporting without rerunning units. Aggregate all settled units to Review/failed if any product or confirmed non-resumable workflow failure exists, otherwise pending-human-review; technical exhaustion takes precedence as infra-error. Deferred unfinished work uses Ready with the applicable quota/infra verdict. Preserve partial scores, evidence, candidate links, and unavailable values; never assign passed or close a result issue.

Scope of shared scenarios: deliver policy, persistence, configured GitHub reconciliation, event delivery, and eval aggregation from normalized results. Physical process survival/termination is exercised through the execution interface here; concrete suite artifact decoding, pinned filesystem preparation, and absolute human-review command construction belong to the suite boundary. Carry suite-provided presentation details unchanged through durable delivery, and preserve all those downstream obligations. For the copied pause/resume/status/tick behavior, this unit owns the persisted control operations, readable saved state, and their admission/reconciliation effects; installed command entry-point packaging and wiring are outside this unit’s scope. Exercise those production control operations directly in this boundary’s integration tests, rather than creating a second CLI implementation.

## Spec

The following requirement blocks are copied verbatim from the approved specifications. For shared requirements, the Background states this delivery unit’s portion; retain the complete scenario semantics.

Source: `openspec/changes/iteration-1/specs/factory-eval-intake/spec.md`.

### Requirement: Restrict automatic execution to repository writers

Routing and execution admission SHALL verify that the issue author has effective write, maintain, or admin permission on its source repository. Organization membership or the presence of the request label alone SHALL NOT satisfy this check. An eval request from an author without sufficient access SHALL enter Backlog without factory assignment and SHALL NOT execute. Failure to establish the author's permission SHALL NOT be treated as authorization.

#### Scenario: Receive an outside contributor's request

- **WHEN** a public-repository contributor without write access creates an issue from the eval template
- **THEN** the request enters the Project in Backlog without factory ownership
- **AND** no evaluation is admitted even though the template applied the request label

#### Scenario: Recheck permission at execution admission

- **WHEN** a Ready card has the eval marker but its author lacks the required repository access
- **THEN** the factory does not accept it for execution based only on its label or board fields

### Requirement: Interpret one evaluation configuration per request

The factory SHALL read TOML execution overrides from a fenced `eval` block in the issue body and ignore surrounding prose for execution settings. Supported keys SHALL be `agent_runner_ref`, `agent_skills_ref`, `lead`, `implementor`, `reviewer`, `skip_validator`, and `repetitions`. Each supplied role override SHALL contain a complete `cli / model / effort` triple as a TOML string; `skip_validator` SHALL be a boolean. Omitted settings SHALL use configured defaults. A request SHALL describe one configuration with a repetition count, without automatic matrix expansion.

Revision selection SHALL apply to Agent Runner and Agent Skills. The factory SHALL use the deployed `agent-evals` harness version; request-level selection or evaluation of harness revisions is outside iteration 1. Recording the harness revision SHALL identify the test environment used for the result.

Repetitions SHALL be a positive integer. An optional configured maximum SHALL reject excessive requests rather than reduce them silently. Iteration 1 SHALL NOT require a repetition ceiling.

#### Scenario: Override selected defaults

- **WHEN** a valid block supplies a Runner ref and a complete lead profile while omitting other settings
- **THEN** those supplied settings replace their respective defaults
- **AND** all omitted settings retain their configured defaults

#### Scenario: Supply invalid execution settings

- **WHEN** a block is malformed, a supplied role profile is incomplete, or repetitions is not a positive integer
- **THEN** the request is invalid and no evaluation starts

#### Scenario: Exceed an optional repetition limit

- **WHEN** a repetition maximum is configured and a request exceeds it
- **THEN** the factory rejects the request settings and explains the limit
- **AND** it does not silently run fewer repetitions

### Requirement: Flag invalid requests for correction

An invalid request SHALL remain in Ready and receive the red `needs-input` label and a comment explaining how to correct it. Validation failures SHALL start no evaluation, consume no execution retry, and SHALL NOT be classified as `infra-error`. Unchanged invalid input SHALL NOT produce the same corrective comment on every poll. The factory SHALL revalidate corrected input and remove `needs-input` automatically when it becomes valid.

#### Scenario: Encounter unchanged invalid input repeatedly

- **WHEN** successive polls encounter the same invalid request settings
- **THEN** the request remains flagged in Ready without execution or repeated copies of the same corrective comment
- **AND** the factory can consider other eligible requests

#### Scenario: Correct the request

- **WHEN** the user corrects the eval block so its settings are valid
- **THEN** the factory removes `needs-input` and the request can become eligible for admission

#### Scenario: Remove only the attention label

- **WHEN** a user removes `needs-input` while the settings remain invalid
- **THEN** the factory does not admit the request and restores the attention label

### Requirement: Select eligible work in manual Project order

The factory SHALL select open issues from configured source repositories whose authors have write, maintain, or admin access, with native `Type=Eval`, `Owner=factory`, and `Status=Ready`, valid execution settings, and no applicable admission hold. Selection SHALL follow manual Project order, matching the unsorted Ready column within the Eval horizontal group. Priority values and issue age SHALL NOT override that order. Invalid or otherwise ineligible requests SHALL NOT prevent selection of a later eligible request. Reordering SHALL NOT interrupt active work.

#### Scenario: Reorder queued evaluations

- **WHEN** a user drags one eligible eval above another before the next selection
- **THEN** the higher eval is selected first, regardless of issue age or Priority values

#### Scenario: Skip an ineligible request

- **WHEN** the highest queued request is invalid or cannot yet resume and a lower request is eligible under current admission controls
- **THEN** the factory selects the lower eligible request

#### Scenario: Reorder while an evaluation is running

- **WHEN** a user changes queue order during active execution
- **THEN** the active evaluation continues and the new order governs subsequent selection

### Requirement: Freeze accepted evaluation inputs

A new claim SHALL record the effective evaluation settings, including the selected eval suite, and resolve Runner, Skills, and `agent-evals` revisions to immutable commits before execution. `agent-evals` is the evaluation harness and may contain multiple suites; iteration 1 SHALL support `and-scene` as the default suite. Those accepted inputs SHALL remain fixed for the claim, including its repetitions and automatic recovery. Later changes to branches, defaults, or the issue SHALL NOT mutate an existing claim's frozen inputs.

#### Scenario: Continue after refs or defaults change

- **WHEN** an unfinished claim resumes after its requested branches or configured defaults have changed
- **THEN** it uses the same accepted settings and immutable revisions
- **AND** its completed repetitions remain completed

### Requirement: Distinguish continuation from an explicit fresh request

A Ready card with `Verdict=quota-deferred` or `infra-error` SHALL continue its existing unfinished claim when eligible, preserving completed repetitions and retry history. On an otherwise eligible Ready card, a human clearing Verdict or changing the parsed eval settings SHALL request a new claim using current inputs while retaining prior claim history. Formatting changes and edits to surrounding prose SHALL NOT request a new claim.

The factory SHALL reconcile local claim and reporting state with board observations so that a Verdict missing because the factory has not finished reporting is not interpreted as a human reset. Edits during execution SHALL NOT alter the running claim or launch overlapping work. Interpreting a completed card merely dragged to Ready with its old verdict is outside iteration 1.

#### Scenario: Resume an automatically deferred claim

- **WHEN** an eligible Ready card still has its deferral verdict and unchanged parsed request settings
- **THEN** the factory continues the existing unfinished claim with its frozen inputs and remaining retry budget

#### Scenario: Request a fresh evaluation explicitly

- **WHEN** a human clears a previously reported Verdict or changes the parsed eval settings on an otherwise eligible Ready card
- **THEN** the factory creates a new claim, resolves current inputs, and retains the prior claim's history

#### Scenario: Recover incomplete factory reporting

- **WHEN** a claim exists but its expected Verdict update has not been delivered
- **THEN** the missing Verdict does not by itself create a new claim
- **AND** the factory reconciles the existing claim and pending reporting

#### Scenario: Edit text without changing execution settings

- **WHEN** a user changes surrounding prose or formatting without changing the parsed eval settings
- **THEN** the edit does not request a fresh claim

#### Scenario: Edit a running request

- **WHEN** a user changes request settings during execution
- **THEN** the active claim keeps its frozen inputs and no overlapping evaluation starts

Source: `openspec/changes/iteration-1/specs/factory-claim-lifecycle/spec.md`.

### Requirement: Keep lifecycle behavior independent of work kind

Core scheduling, claim/run persistence, retries, cancellation, restart recovery, and durable reporting SHALL remain independent of Codagent identities and eval-specific parsing or scoring. Work kinds SHALL supply request interpretation, execution behavior, and result details through a small interface. The eval handler SHALL own repetition semantics and suite integration. The generic core SHALL NOT interpret a score out of 70, Runner/Skills fields, or an `and-scene` command path.

#### Scenario: Integrate another work kind later

- **WHEN** a later change supplies another work-kind handler
- **THEN** it can reuse the saved-claim, execution-attempt, scheduling, cancellation, and recovery behavior without adding that kind's request or scoring rules throughout the controller
- **AND** iteration 1 does not require implementing that additional handler or a dynamic plugin loader

### Requirement: Persist accepted work and execution history

The factory SHALL persist accepted requests as claims in local SQLite, including issue and Project-item identity, work kind, frozen settings and revisions, selected eval suite, progress, and outcome. Each execution attempt SHALL have a run record identifying its claim and repetition, execution ownership, timing, outcome, and evidence location. Completed repetitions and consumed recovery retries SHALL survive controller restarts and card moves. Detailed suite evidence SHALL remain in files, with their locations recorded in SQLite. GitHub SHALL remain the source of user intent; board fields alone SHALL NOT replace saved execution history.

#### Scenario: Inspect progress after a restart

- **WHEN** the factory restarts after a claim has completed one repetition and attempted another
- **THEN** it retains the frozen inputs, completed result, attempt history, retry usage, and evidence locations
- **AND** it does not reconstruct execution history solely from the card's current fields

### Requirement: Prevent overlapping execution

Iteration 1 SHALL execute at most one repetition at a time across the service and manual execution commands. The factory SHALL reconcile saved execution records with surviving processes and suite evidence before dispatching new work. Status and pause controls SHALL remain usable while execution is active.

#### Scenario: Attempt simultaneous dispatch

- **WHEN** a manual command attempts execution while the service already has an evaluation running
- **THEN** no second evaluation starts
- **AND** status and pause controls remain available

### Requirement: Correct status edits that contradict factory execution

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. Moving a request with verified active execution to Ready, Review, or Done SHALL restore Running and continue that same execution without restarting it or admitting overlapping work. Such status changes SHALL NOT cancel execution. Issue closure SHALL retain its defined cancellation behavior and take precedence over restoring Running.

The factory SHALL leave status edits on cards without factory ownership alone. Corrective updates SHALL include a brief issue comment explaining the actual state and correction, using durable reporting to avoid repeating the same correction comment on each poll. The default correction interval SHALL be the normal five-minute poll; unavailable GitHub access SHALL delay the correction rather than change execution state based on an unverified board observation.

#### Scenario: Move an idle request to Running

- **WHEN** a human moves a factory-owned queued request to Running while it has no active execution
- **THEN** the next successful poll restores its appropriate queued status
- **AND** the drag itself creates no execution attempt and bypasses no admission control
- **AND** the factory briefly explains the correction on the issue

#### Scenario: Move a running request to Ready

- **WHEN** a human moves a factory-owned request with verified active execution to Ready
- **THEN** the next successful poll restores Running and continues the existing execution
- **AND** no fresh claim, duplicate attempt, restart, or recovery retry results from the status edit

#### Scenario: Move a running request to a handoff or completion column

- **WHEN** a human moves a factory-owned request with verified active execution to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running without cancelling execution or reporting it as complete

#### Scenario: Close an issue whose status also changed

- **WHEN** the factory observes that an active request's issue is closed and its card has moved out of Running
- **THEN** it follows the issue-closure cancellation policy rather than restoring Running

#### Scenario: Change a card that is not factory-owned

- **WHEN** a human changes the Status of a card without factory ownership
- **THEN** the factory does not correct that card or infer an execution request from the drag

#### Scenario: Retry delivery of a status correction

- **WHEN** a corrective update or its explanatory comment needs delivery again after a poll or controller restart
- **THEN** the factory reconciles current execution and board state before retrying delivery
- **AND** it does not duplicate a correction comment already delivered

### Requirement: Bound technical recovery per repetition

The factory SHALL allow one automatic recovery retry per repetition after a technical execution failure, using supported suite resume behavior and preserving completed repetitions. It SHALL record execution attempts and consumed retries durably. A completed evaluation with a poor product result SHALL NOT trigger a technical retry. A suite-confirmed non-resumable implementation-workflow failure SHALL settle that repetition as failed without a retry and SHALL allow remaining repetitions to proceed. Resumable workflow failures and recoverable harness failures SHALL follow the bounded technical recovery policy. The factory SHALL preserve the suite's failure owner, code, and resumable value and SHALL NOT infer non-resumability from the workflow-failed status alone. Recognized quota waiting, unavailable prerequisites detected before execution, and waiting for an admission window SHALL NOT consume this retry budget.

If the recovery retry fails, the factory SHALL stop the claim, leave remaining repetitions unstarted, retain completed results and failure evidence, and move the issue to Review with `infra-error` and an explanation. Other eligible requests MAY proceed. Another evaluation SHALL require the explicit fresh-request behavior defined by intake and SHALL start the requested repetitions anew while preserving prior claim history.

#### Scenario: Recover the second repetition

- **WHEN** repetition 1 completed and repetition 2 encounters its first technical failure
- **THEN** the factory can resume repetition 2 once when execution is permitted
- **AND** repetition 1 remains completed

#### Scenario: Exhaust recovery

- **WHEN** repetition 2's recovery retry fails in a request for three repetitions
- **THEN** the claim stops, repetition 3 remains unstarted, and the issue moves to Review with `infra-error` and an explanation
- **AND** completed results and both failed attempts' evidence remain available
- **AND** the factory may process other eligible requests

#### Scenario: Preserve the retry limit across interruption

- **WHEN** the controller restarts or the card moves after the repetition has consumed its recovery retry
- **THEN** those events do not grant another automatic recovery retry

#### Scenario: Complete with a product failure

- **WHEN** an evaluation completes and establishes that the tested product failed
- **THEN** the factory reports the result without retrying it as a technical failure

#### Scenario: Settle a non-resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=false`, such as a typed prohibited workflow action
- **THEN** the factory records a failed repetition without attempting technical recovery and permits remaining repetitions
- **AND** it preserves the suite's product verdict and failure details instead of inventing a product score or verdict

#### Scenario: Recover a resumable workflow failure

- **WHEN** the suite reports an implementation-workflow failure with `resumable=true` and no recognized quota hold
- **THEN** the repetition follows the existing bounded technical recovery policy rather than being treated as a settled product failure

### Requirement: Wait for provider quota without discarding progress

On a detected Codex usage limit, including during product judging, the factory SHALL preserve claim progress and persist an admission hold until the reliably interpreted reset time reported by the CLI. Only after recognizing a Codex rate-limit diagnostic, if no reset time can be interpreted reliably, it SHALL use a configurable default of five hours from detection. A generic execution error or an absent reset time alone SHALL NOT create a quota hold; generic errors SHALL follow ordinary failure recovery. The hold SHALL survive controller restarts. After it expires, execution SHALL remain subject to the admission window, pause state, and other applicable prerequisites.

The `and-scene` suite SHALL continue to manage Claude quota waiting internally. Recognized quota waiting SHALL NOT consume the technical recovery retry, although any later execution attempt SHALL still be recorded. Automatic continuation SHALL retain the same frozen claim and completed repetitions.

#### Scenario: Detect a reported Codex reset

- **WHEN** Codex reports a usage limit with a reliable reset time
- **THEN** the factory records the hold and preserves the unfinished claim until execution becomes eligible after that reset
- **AND** waiting consumes no recovery retry

#### Scenario: Use the fallback after a restart

- **WHEN** a recognized Codex rate-limit error had no reliable reset time and the controller restarts during the resulting five-hour hold
- **THEN** the original hold remains in effect without restarting the five-hour countdown

#### Scenario: Avoid a false quota hold

- **WHEN** an execution error contains no recognized Codex rate-limit diagnostic
- **THEN** the factory applies ordinary failure recovery without creating a five-hour quota hold

#### Scenario: Reach reset outside the admission window

- **WHEN** the usage hold expires after 15:00 under the default schedule
- **THEN** the factory waits for the next admission window before continuing eligible execution

### Requirement: Observe admission and pause controls between repetitions

The factory SHALL check admission controls before starting each repetition and before starting a recovery attempt. Under the default schedule, starts SHALL be permitted from 00:00 up to but not including 15:00 local time. An attempt already running when the window closes SHALL be allowed to finish subject to supervision limits. Remaining repetitions SHALL retain the same claim and inputs while waiting.

`agent-factory pause` SHALL prevent new execution while allowing the current repetition to finish. `agent-factory resume` SHALL permit eligible execution subject to the window and other holds. Pause state SHALL survive controller restarts. These controls SHALL apply to the whole factory and SHALL NOT introduce additional Project statuses.

#### Scenario: Finish after the cutoff

- **WHEN** repetition 1 of three finishes at 15:30 under the default schedule
- **THEN** its result is saved and repetition 2 waits until the next admission window
- **AND** the claim, frozen inputs, completed work, and recovery budget are preserved

#### Scenario: Pause and resume the factory

- **WHEN** the user pauses during an active repetition
- **THEN** that repetition may finish, but no subsequent repetition or recovery attempt starts while paused
- **AND** resuming permits execution only when the other admission conditions also allow it

### Requirement: Cancel a request when its issue is closed

On the next successful GitHub check after the user closes a request's issue, the factory SHALL stop verified owned execution for that request, skip its remaining repetitions, and preserve existing evidence. Cancellation SHALL NOT trigger a recovery retry or prevent other eligible requests from proceeding once execution has stopped. The factory SHALL respect the closed issue rather than moving it back into the active queue; GitHub closure automation SHALL move its card to Done.

#### Scenario: Close an active request

- **WHEN** the user closes an issue while its evaluation is running
- **THEN** the next successful GitHub check triggers cancellation of that request's owned execution
- **AND** remaining repetitions are skipped and existing evidence is retained
- **AND** other eligible requests can proceed after the cancelled execution stops

### Requirement: Recheck unavailable prerequisites without retrying execution

Before accepting work for execution, the factory SHALL check shared readiness and, before launch, the selected suite's readiness. An unavailable prerequisite SHALL hold affected work without launching an evaluation, recording an execution attempt, or consuming a recovery retry. The factory SHALL explain the problem and any required operator action, then recheck readiness on subsequent polls. These checks SHALL NOT attempt to repair credentials or configuration. Holds SHALL apply to work that depends on the unavailable prerequisite.

#### Scenario: Wait for Docker or authentication

- **WHEN** Docker is unavailable or required authentication is invalid before launch
- **THEN** the factory reports the prerequisite problem and starts no affected evaluation
- **AND** it rechecks availability without consuming attempts or retries

#### Scenario: Detect a repaired prerequisite

- **WHEN** the operator fixes a prerequisite and a subsequent readiness check succeeds
- **THEN** the associated hold clears and affected work may proceed under the other admission controls without restarting the factory

### Requirement: Recover reporting independently of execution

The factory SHALL retain enough reporting progress to recover missing issue activity comments, results, and field updates independently of evaluation execution. Restarting the controller or retrying GitHub delivery SHALL NOT repeat completed evaluation work or duplicate already delivered activity. Reporting SHALL preserve later human intent subject to the explicit correction policy for status edits that contradict factory execution.

#### Scenario: Retry a failed result update

- **WHEN** evaluation results are saved but delivery to GitHub fails
- **THEN** the factory retries the missing reporting without rerunning the evaluation
- **AND** it reconciles already delivered comments and later human changes before repairing remaining updates

Source: `openspec/changes/iteration-1/specs/factory-eval-reporting/spec.md`.

### Requirement: Comment on meaningful factory activity

The factory SHALL post issue comments for meaningful request events: starting work, a technical failure and its retry, waiting for a usage reset, repetition completion, cancellation, and final handoff. Comments SHALL identify the affected repetition and attempt where relevant, explain what happened, and state what happens next. Routine polls and unchanged waiting states SHALL NOT produce repeated activity comments.

#### Scenario: Fail and retry a repetition

- **WHEN** a repetition fails technically and the factory starts its permitted recovery retry
- **THEN** issue activity identifies the failure, the affected repetition, and the recovery action

#### Scenario: Wait for a usage reset

- **WHEN** a usage limit defers unfinished work
- **THEN** a comment explains the reason and expected reset or next eligible start time
- **AND** unchanged subsequent polls do not repeat the same waiting comment

#### Scenario: Cancel a request

- **WHEN** the factory cancels execution after the user closes the issue
- **THEN** it records the cancellation in issue activity, including retained results and unstarted repetitions
- **AND** it respects the user's closed issue and Done card

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

### Requirement: Deliver reports durably without duplicates

The factory SHALL persist per-claim reporting progress for comments and Project-field updates, including completion flags, stable report identifiers, and returned comment IDs. Activity identifiers SHALL distinguish repetitions and attempts where necessary. If a post succeeds but its response is lost, the factory SHALL discover the existing comment rather than create another copy. Pending delivery SHALL survive controller restarts and SHALL NOT cause completed evaluation work to run again.

When repairing reporting, the factory SHALL reconcile its saved delivery state with current GitHub state and preserve later human intent, including issue closure and subsequent field changes, subject to the explicit correction policy for Status edits that contradict factory execution in `factory-claim-lifecycle`. This requirement does not introduce a general-purpose outbox subsystem.

#### Scenario: Lose a successful comment response

- **WHEN** GitHub accepts a report comment but the factory does not receive the response
- **THEN** subsequent reconciliation finds the comment by its stable identifier and completes local reporting progress without duplicating the post

#### Scenario: Restart between comment and field delivery

- **WHEN** a result comment succeeds but a field update remains undelivered when the controller restarts
- **THEN** the factory recovers the missing field update without repeating the comment or evaluation
- **AND** it reconciles any newer human changes before writing

#### Scenario: Close the issue before pending delivery recovers

- **WHEN** the user closes an issue before a pending factory status update is repaired
- **THEN** the factory preserves the closure and does not move the card back to an active state while recovering reporting

Source: `openspec/changes/iteration-1/specs/factory-operations/spec.md`.

### Requirement: Persist pause and enforce configured admission controls

`agent-factory pause` SHALL save a factory-wide pause while allowing the current repetition to finish. `agent-factory resume` SHALL clear that pause without clearing unrelated usage or prerequisite holds. The saved pause SHALL survive controller restarts. Under the default schedule, each repetition or recovery attempt SHALL start only from 00:00 up to but not including 15:00 local time. Already-running attempts SHALL follow the lifecycle limits rather than stop solely because the window closes.

Configuration SHALL support the approved default limits of 30 minutes without progress, six hours of execution excluding recognized quota waits, 12 hours total per attempt, and a five-hour fallback for Codex reset holds. These values SHALL be configurable. The configured free-space minimum SHALL be checked before admission; insufficient space SHALL hold new affected work without consuming an execution retry.

#### Scenario: Resume while another hold remains

- **WHEN** the operator resumes a paused factory while a usage hold remains active
- **THEN** the pause clears but execution waits until the usage hold and other admission conditions permit it

#### Scenario: Run below the free-space minimum

- **WHEN** free disk space is below the configured minimum before admission
- **THEN** the factory starts no affected evaluation, reports the storage problem, and rechecks readiness without consuming a recovery retry

## Test Plan

Own `INT-001` and `INT-002` in full, including the already-deliverable routing/client behavior when exercised together with the controller. Put them under `tests/integration/`. Use production handler/controller/store/client components with fresh real temporary SQLite connections across restarts; only external GitHub transport, suite/process observations, preparation results, and time are controlled. Preserve the versioned suite semantics in supplied observations. These obligations are complete at this boundary without a model call or actual suite process. Include the execution-plan environment policy in credential-leak assertions. The following approved obligation text is the completion contract.

For `INT-002`, include a realistic Project response whose field/group connections omit native issue Type, with Type supplied by issue data, and prove the eligible Eval is still selected. This exercises the approved design’s documented provider shape rather than a fabricated Project Type field.

Also verify the shipped template/parser contract from `factory-eval-intake`’s “Create explicitly assigned evaluation requests” and “Interpret one evaluation configuration per request” requirements. Capture the exact delivered `agent-evals/.github/ISSUE_TEMPLATE/eval-request.md` as `tests/fixtures/eval-request.md`, recording its companion source revision and checking that the snapshot matches the delivered file when creating or updating it. Feed its actual default fenced eval block through the production parser and validator with the supplied deployment defaults; it must be valid without repairing keys or values in the test. Keep this focused automated check collected in the normal pytest suite, without a live issue or GitHub dependency. Template changes must refresh the source-matched fixture and rerun this contract check.

Use implementation-time TDD for the copied specification scenarios within the scope stated above. Keep unit cases close to the behavior rather than reproducing every case at every test layer. Use isolated roots, temporary SQLite/files, and controlled external responses; automated verification must not read the operator's database, live queue, or private credentials. Run the relevant collected pytest tests and the repository's Ruff format/lint and strict Pyright checks. Register any markers in `pyproject.toml`; pytest exit code 5 or an unexecuted required platform test does not count as passing.

Implementors do not execute `AT-001`, `AT-002`, or `HT-001`. Preserve their requirements in `openspec/changes/iteration-1/test-plan.md` for the separate acceptance stage: real issue-event routing, one real suite repetition and launchd restart, and the operator's board-drag-to-API observation. Controlled automated evidence cannot satisfy those live boundaries. Do not change the approved definition artifacts or weaken their testing obligations.


### INT-001: SQLite lifecycle, continuation, and delivery progress

- Covers: Persist accepted work/history; freeze inputs; continuation versus fresh request; bounded recovery; quota and admission holds; durable pause and reporting; separate execution/product outcomes.
- Boundary: Production controller/handler/store components with a real temporary SQLite database; stub GitHub observations, suite observations, and time.
- Setup: One accepted multi-repetition claim with frozen settings, a completed first repetition, and an unfinished second repetition. Use realistic versioned suite outcome fixtures and named admission conditions. Open fresh store/controller instances to represent restarts rather than merely resetting an in-memory object.
- Action: Continue after a technical interruption; exhaust the one permitted recovery retry; separately defer for Codex quota and reopen the database before reset. Exercise an unchanged continuation, an explicit fresh request, and missing Verdict delivery. Reconstruct pending report progress after restart. Apply pause/window/prerequisite decisions between units.
- Assertions: Completed repetitions and frozen inputs survive; a technical retry is charged only once and exhaustion leaves later units unstarted; quota continuation preserves its original reset and does not consume that retry; pause and holds remain independent. A changed parsed request or cleared successfully reported Verdict can create a fresh claim only when eligible, while formatting or undelivered reporting cannot. Product failure remains distinct from execution failure. A suite-confirmed non-resumable workflow failure settles only its repetition, preserves the separate product verdict, and allows the next repetition; a resumable workflow failure consumes the normal bounded recovery budget. No run is created for a pre-launch readiness hold. Reopening storage retains the intended delivery/event identities.
- **Constraints:** Branch movement and OS process lifetime are covered by E2E tests; this obligation does not mock SQLite or enumerate every score/parser branch.
- Execution: `tests/integration/`; ordinary automated checks on supported development/CI hosts, with no network or model credentials.

### INT-002: Routing and reporting against a controlled GitHub API

- Covers: Shared routing configuration; author permission gate; initialize fields once; manual queue order; invalid-request feedback; Status corrections; cancellation precedence; comments and field-update recovery.
- Boundary: Production configuration, routing/controller code, and GitHub client serialization against a stub transport or local HTTP service; real temporary SQLite for controller reporting. Stub responses must follow the REST/GraphQL shapes actually used by the implementation.
- Setup: Paginated Project items in deliberate manual order; trusted and untrusted authors; valid and invalid eval bodies; routing receipts; issue comments with stable markers. Include configuration for an organization/repository/field mapping other than Codagent to detect hard-coded deployment assumptions.
- Action: Deliver the same routing event twice and interrupt initialization between field updates. Lose the response to a successful comment write, restart delivery, and paginate to find that comment. Change current board state between deliveries, including closure. Exercise a Ready card moved while execution is active and a non-factory card. Supply missing/denied author-permission responses and verify generated calls when App authentication refreshes.
- Assertions: Authorized evals route to Ready even with invalid settings; outsiders remain Backlog without ownership, and unknown permission never authorizes execution. Repeated routing preserves subsequent human changes. Source rules and manual position drive selection, not age/Priority. Corrections affect only the approved factory-owned cases; closure overrides a pending Running repair. A successful lost-response comment is found and not duplicated. Unknown input is reported once until it changes; correction removes the attention label. Authentication is kept out of request bodies, log output, suite environment, and command arguments.
- **Constraints:** No actual organization mutations, Actions runs, or credential rotation. Real installation-token permissions and workflow event wiring are proven by AT-001; no second live outside-contributor account is required for this controlled authorization coverage.
- Execution: `tests/integration/`; ordinary automated checks. Capture API requests as structured values so assertions verify methods, identifiers, and payload semantics rather than shell formatting.

## Done When

- The production controller/handler/store boundary admits, freezes, continues, supersedes, defers, settles, and cancels claims with durable retry, hold, and reporting state; no active overlap or false human-reset interpretation is possible in the covered scenarios.
- All copied scenarios within this policy/persistence/reporting scope pass, including invalid-input repair while paused, author rechecking, manual order, current-state status corrections, quota versus technical recovery, workflow resumability, partial result visibility, and lost-response deduplication.
- `INT-001` and `INT-002` are collected and pass under `tests/integration/` with real SQLite and contract-accurate controlled APIs. Restart tests reopen persistence and prove stable result/event identities rather than resetting in-memory objects.
- Eligibility coverage passes with native Type present only in issue data, and the exact source-matched shipped template block passes the production parser/validator under the deployment defaults. The template fixture’s source revision is recorded; the test does not substitute a handwritten valid request for the shipped block.
- The designed records and ownership of database writes are explicit enough for independent process supervision and suite implementations to consume without introducing suite rules into the controller. New schema, configuration/control behavior, and recovery/reporting diagnostics are documented alongside the code.
