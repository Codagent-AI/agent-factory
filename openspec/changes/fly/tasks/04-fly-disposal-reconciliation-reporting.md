# Task: Dispose, hold, reconcile, and report Fly attempts end to end

## Goal

Close the Fly eval lifecycle in the controller cycle: after each run is classified, destroy, stop,
or keep its Machine; stop a Machine across a Codex quota hold with a deadline that covers the
earliest possible restart and start it again in place; reconcile every factory-tagged Machine each
cycle so nothing bills past its deadline; report lost repetitions honestly in the aggregate
verdict; prove the whole journey with an end-to-end test; document Fly operation; and remove the
absorbed proof of concept. After this task, `eval.execution = "fly"` is a complete, supported mode.

## Background

Read `openspec/changes/fly/proposal.md` and `openspec/changes/fly/design.md` (sections "Disposal
after classification", "Reconciliation", "Doctor and status", "Risks / Trade-offs", "Migration
Plan") first. Billing containment is the priority: losing artifacts is preferred over unbounded
billing.

### What already exists

- `src/agent_factory/config.py`: `eval.execution = "docker" | "fly"` and the local `[fly]` table
  (`app`, `region`, `cpu_kind`, `cpus`, `memory_mb`, `image`, `token_file`,
  `collection_grace_seconds`, `heartbeat_seconds`).
- `src/agent_factory/fly/api.py`: urllib Machines REST client — create, get, list by
  `metadata.<key>`, set one metadata key, update a stopped Machine's config env, stop, start,
  destroy (404 = success), typed errors.
- `src/agent_factory/fly/launcher.py`: the `SANDBOX_RUNNER` adapter. Each attempt's Machine carries
  metadata `{factory-owner, run_id, claim_id, nonce, deadline_epoch, unit_key}`; the guest init
  enforces `max(env FACTORY_DEADLINE_EPOCH, /var/lib/factory/deadline)`. Resume mode, for a
  **stopped** Machine, writes the new deadline into the config env and metadata and then starts it;
  with manifest `expect_checkpoint: true` it verifies `/artifacts/run-state.json` and exits `71`
  when missing. Outside stand-in mode the launcher never destroys or stops a Machine.
- `src/agent_factory/fly/backend.py` (`FlyMachineBackend`): `readiness`, `identity_from_plan`,
  `probe` (`alive | stopped | gone | mismatch | unknown`), `terminate`, `attach_argv`, `provenance`,
  and `dispose(identity, "destroy")`. `dispose` for `"stop"`/`"keep"` and `reconcile` are
  unimplemented.
- `src/agent_factory/supervisor.py` dispatches to the backend for plans with
  `ownership_hints["backend"] == "fly-machine"`, copies `machine.json` into `progress["machine"]`,
  finishes a vanished Machine as `machine lost`, records `("runtime", "fly:mismatch")`, and destroys
  the Machine itself after a verified cancel.
- `src/agent_factory/work_kinds/eval/handler.py`: `classify` settles a lost Machine with
  `result["failure"] = {"owner": "factory", "code": "machine-lost"}` without retry; `plan_attempt`
  sets `expect_checkpoint` from the previous attempt's `checkpoint_seen` progress.
- `src/agent_factory/operations.py`: `status` renders `progress["machine"]`,
  `("runtime", "fly:machine:<claim_id>")` = `{machine_id, decision, deadline_epoch, state}` as
  `stopped (quota hold), deadline …`, and `("runtime", "fly:unknown")` /
  `("runtime", "fly:cleanup-failed")` = `{"machines": [{machine_id, deadline_epoch, reason, …}]}`
  and `("runtime", "fly:mismatch")` under blocking conditions.
- `tests/fixtures/fly/`: fake Machines REST server, fake `flyctl` shim operating on a temp guest
  directory, and the vendored pinned `run.sh`.

### Disposal after classification (`src/agent_factory/runtime.py`, `controller.py`, `fly/backend.py`)

`runtime._consume_results` calls `backend.dispose(identity, decision)` after the handler classifies
a Fly run. Collection is already verified complete before any destroy or stop (an unverified
collection is the lost-Machine outcome).

| Classification | Decision |
|---|---|
| settled (completed, product failure, non-resumable), exhausted recovery | `destroy` |
| cancelled | `destroy` — already done by the supervisor; do not double-handle |
| quota | `stop` (deadline := next eligible start + total limit + grace, written to metadata + file first; the next eligible start is the later of the hold's expiry and the next admission-window opening) |
| technical, retry remaining | `keep` (deadline unchanged until the recovery attempt extends it) |
| lost Machine | record; if the Machine still exists (e.g. checkpoint missing after a stop, or an unverified collection) destroy it |

Ownership comes first: every `stop` or `destroy` disposal re-probes the Machine and compares all
immutable ownership metadata (`factory-owner`, `run_id`, `claim_id`, `unit_key`, `nonce`) with the
recorded attempt. On `mismatch` it issues no stop or destroy, records `("runtime", "fly:mismatch")`
(which already holds new eval launches), and leaves the Machine to its deadline; on `unknown` (API
failure) it issues nothing and retries the disposal on the next cycle, recording the failure under
`("runtime", "fly:cleanup-failed")` until it succeeds. The reconciler's deadline rule is the only
path that destroys a tagged Machine without a matching recorded attempt.

`dispose` persists `{machine_id, decision, deadline_epoch, state}` under
`("runtime", "fly:machine:<claim_id>")` and clears it on destroy. While a quota hold stands, each
cycle recomputes the stopped Machine's deadline from the current hold (Codex holds move as the suite
reports new reset times) and updates Machine metadata and the setting when it changes. For a stopped
Machine the persisted-file deadline cannot be written; the metadata governs the reconciler and the
launcher's resume refreshes the config env before start. Starting happens through the normal
launch path when execution is eligible (window open, hold expired, other holds clear): the next
attempt's launcher resume starts the stopped Machine, with `expect_checkpoint` true so a missing
checkpoint settles as a lost Machine and the Machine is destroyed. Quota waiting consumes no
recovery retry.

**`auto_destroy` risk:** the quota path depends on a stopped Machine surviving an API stop. The
recorded fallback (apply only if told the live observation failed) is `auto_destroy: false` with the
guest watchdog ending the process at the deadline and the reconciler performing every destroy.
Structure `dispose`/`reconcile` so that fallback is a small change.

### Reconciliation (`FlyMachineBackend.reconcile(store)`, called once per `runtime.cycle()` before dispatch, only under `fly`)

`GET /v1/apps/{app}/machines?metadata.factory-owner=<marker>`; for each: destroy when
`now > metadata.deadline_epoch` (idempotent — 404 is success — retried on later cycles, verified by
GET); Machines unknown to the store but within deadline are left running and recorded under
`("runtime", "fly:unknown")`; failures go under `("runtime", "fly:cleanup-failed")` and clear when
resolved. Machines recorded under `fly:mismatch` are touched only by the deadline rule; when one is
gone the setting is cleared and the uncertain run finishes as lost. Machines recorded under
`fly:machine:<claim_id>` with decision `stop` are destroyed only by the deadline rule. Untagged
Machines are never touched. A reconcile failure (API down) must not abort the cycle.

### Reporting (`src/agent_factory/work_kinds/eval/handler.py` `settle()`, `report_events`)

`settle()` computes the aggregate verdict from the surviving repetitions only (`failed` or
`pending-human-review`), lists each lost repetition with its infrastructure reason in the results
comment, never presents a lost repetition as a product result, carries review commands only for
surviving reviewable repetitions, and yields `infra-error` with each loss explained when every
repetition is lost. The posted human-review command works against the collected directory on the
Mac with no Fly resource.

### Documentation and cleanup

- `docs/installation.md`: eval execution mode and what Fly keeps and gives up versus Docker; one-time
  Fly organization, app, and app-scoped deploy-token setup; the token file beside the other
  controller credentials; the amd64 base image build with Fly's remote builder and the Agent Runner
  revision it requires (Dockerfile Chrome discovery accepting `chrome-linux64/chrome`, plus a
  `.dockerignore`); every `[fly]` setting and default; `flyctl` on the LaunchAgent PATH; Cursor role
  profiles unavailable on Fly; worst-case cost per attempt implied by the deadline (about $0.75 at
  defaults); the rollout order from the design's Migration Plan including the token-refresh
  production gate; rollback by setting `eval.execution` back to `docker`.
- `docs/operations.md`: `doctor`'s `eval` and `eval-fly` groups, `status` Machine lines and
  reconciliation findings, the mismatch remedy, that a lost Machine costs its repetition and is not
  rerun, quota-hold stop/start, that human review runs on the Mac against the collected artifact
  directory, the launcher's `stand-in` and `attach` modes for diagnosis.
- `docs/suite-integration.md`: the `SANDBOX_RUNNER` adapter seam, the manifest, the pinned argument
  grammar and the dry-run readiness hold when the harness changes it.
- Keep the Fly deploy token documented as separate from the candidate-branch, fix PR, and board
  credentials. No personal paths or credentials in public examples.
- Remove `poc/` (its content is absorbed into `src/agent_factory/fly/`).

## Spec

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Collect the whole artifact tree once, then destroy the Machine

When the Machine writes its completion marker, the factory SHALL collect the repetition's entire artifact tree, including the suite's checkpoint, phase results, judging output, session state, and the built candidate output the human-review command serves, verify the collected tree against a file manifest the Machine wrote with its completion marker, and only then place it in the attempt's artifact directory and record the guest exit code. A collection that does not verify SHALL NOT be recorded as a result and SHALL settle the attempt as a lost Machine. When a limit or cancellation stops an attempt, the factory SHALL stop the job inside the Machine and collect before the attempt is finished. After the attempt is classified, the factory SHALL destroy the Machine unless the attempt is retained for same-Machine recovery or stopped for a quota hold as defined below. Collection SHALL be verified complete before the Machine is destroyed or stopped. If collection has not completed within the collection grace period, the Machine's own enforcement SHALL destroy it and the attempt SHALL be settled as a lost Machine. The posted human-review command SHALL work against the collected directory on the factory host without any Fly resource.

#### Scenario: Complete a repetition

- **WHEN** the suite finishes and the Machine writes its completion marker
- **THEN** the full artifact tree is present under the repetition's host artifact directory, the exit code is recorded, and once the repetition is settled the Machine no longer exists

#### Scenario: Miss the collection grace period

- **WHEN** the factory does not complete collection within the grace period after the completion marker
- **THEN** the Machine destroys itself and the attempt is settled as a lost Machine with whatever evidence was streamed

#### Scenario: Collect a partial tree

- **WHEN** the transfer of the artifact tree is interrupted so the collected files do not match the Machine's manifest
- **THEN** no result is recorded from the partial tree and the attempt is settled as a lost Machine

#### Scenario: Cancel a running repetition

- **WHEN** the issue behind a running Fly repetition is cancelled
- **THEN** the job is stopped in the Machine, the evidence so far is collected, and the Machine is destroyed

This task's portion: destroy, stop, or keep after classification ("Complete a repetition" — once
settled the Machine no longer exists).

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Resume only inside the surviving Machine

Bounded technical recovery for a Fly attempt SHALL run the suite's `--resume` inside the same Machine, as a new recorded attempt with a fresh deadline, subject to the existing recovery budget and the suite's resume checks. When the previous attempt verifiably stopped before the suite created its checkpoint and before candidate execution, recovery SHALL run the suite without `--resume` inside the same surviving Machine, consuming the ordinary retry; such a stop SHALL NOT be treated as a lost Machine. The factory SHALL NOT attempt to resume a repetition in a different Machine. When the recovery budget is exhausted, the Machine SHALL be destroyed after evidence collection and the existing exhaustion policy SHALL apply.

#### Scenario: Recover after a limit stops the suite

- **WHEN** a Fly attempt is stopped by a limit and a recovery retry remains
- **THEN** the recovery attempt resumes the suite inside the same Machine with a new attempt record and fresh deadline

#### Scenario: Recover a failure before the checkpoint existed

- **WHEN** a Fly attempt fails while cloning, building, or logging in, before the suite created its checkpoint, and a recovery retry remains
- **THEN** the recovery attempt runs the suite without `--resume` inside the same Machine and the retry is consumed

#### Scenario: Exhaust recovery on Fly

- **WHEN** the recovery attempt in a Machine also fails
- **THEN** the evidence is collected, the Machine is destroyed, and the claim follows the existing exhausted-recovery policy

This task's portion: "Exhaust recovery on Fly" — evidence collected, Machine destroyed, existing
exhaustion policy applied; and keeping the Machine when a retry remains.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Stop the Machine during a Codex quota hold

When a recognized Codex quota hold interrupts a Fly attempt, the factory SHALL stop the Machine with its root filesystem retained rather than destroy it, record a deadline that covers the earliest time execution can become eligible again (the later of the hold's expiry and the next admission-window opening) plus the attempt's total elapsed-time limit plus the collection grace period, refresh that deadline whenever the hold moves, and start the Machine only when execution is eligible again under the admission window and other holds. Before resuming, the factory SHALL verify that the suite's checkpoint is present in the started Machine; a missing checkpoint SHALL take the lost-Machine path. A stopped Machine SHALL count as the repetition's surviving Machine for reconciliation and status.

#### Scenario: Resume after a hold inside the window

- **WHEN** a Codex hold expires while execution is eligible
- **THEN** the stopped Machine is started, its checkpoint is verified, and the suite resumes there without consuming a recovery retry

#### Scenario: Find the checkpoint gone after a stop

- **WHEN** the started Machine no longer holds the suite's checkpoint
- **THEN** the repetition is settled as a lost Machine and the Machine is destroyed

#### Scenario: Reach reset outside the admission window

- **WHEN** a Codex hold expires outside the admission window
- **THEN** the Machine stays stopped, its recorded deadline still lies beyond the next window opening, and it is started when the window opens

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Bound every Machine with one absolute deadline

Each attempt SHALL have one absolute deadline equal to its launch time plus the attempt's configured total elapsed-time limit plus a configured collection grace period. The supervisor, the Machine's in-Machine enforcement, the Machine metadata, and stale-Machine reconciliation SHALL all use that same recorded value. Existing eval limits SHALL be unchanged; Machine startup, cloning, and Runner compilation SHALL count against the attempt. When a recovery attempt starts in a surviving Machine, or when a stopped Machine is started after a quota hold, the factory SHALL record a fresh deadline computed from that attempt's start and update the Machine's enforcement and metadata before execution continues; for a stopped Machine the in-Machine enforcement SHALL carry the new deadline before the Machine is started, so it never boots against an expired one.

#### Scenario: Reach the total limit with the controller offline

- **WHEN** an attempt's total elapsed-time limit passes while the factory is offline
- **THEN** the Machine is destroyed no later than the collection grace period after that limit without factory involvement

#### Scenario: Start a recovery attempt

- **WHEN** a recovery attempt starts in a surviving Machine
- **THEN** the recorded deadline, the Machine metadata, and the in-Machine enforcement all reflect the new attempt's deadline before the suite resumes

#### Scenario: Start a stopped Machine whose old deadline has passed

- **WHEN** a stopped Machine is started after its previous deadline has passed
- **THEN** the in-Machine enforcement already holds the new deadline at boot and the Machine does not destroy itself

This task's portion: the recorded deadline for stopped Machines across a hold and its use by
reconciliation.

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Settle a lost Machine as a failed repetition

When a Machine is found destroyed, missing, or without a checkpoint it was known to have, or its collection cannot be verified, the factory SHALL record the repetition as failed for a factory-owned infrastructure reason, preserve the evidence already streamed, consume no recovery retry, and continue remaining repetitions in new Machines. The lost repetition SHALL NOT be rerun automatically, SHALL NOT be classified as a product failure, and SHALL NOT be offered recovery. The aggregate verdict SHALL follow the lost-repetition rule in `factory-eval-reporting`.

#### Scenario: Lose a Machine mid-run

- **WHEN** a running repetition's Machine no longer exists
- **THEN** that repetition is recorded as failed with an infrastructure reason and its streamed evidence, and the next repetition starts in a new Machine

#### Scenario: Lose every repetition

- **WHEN** all of a claim's repetitions are settled as lost Machines
- **THEN** the claim reports through the existing infrastructure-error path with each loss explained

_From `specs/factory-fly-execution/spec.md`:_

### Requirement: Reconcile stale Machines every cycle

Each controller cycle SHALL inspect every Machine in the factory's Fly app that carries the factory's ownership marker, including Machines the local store never recorded. Any such Machine past its recorded deadline SHALL be destroyed; a Machine within its deadline but unknown to the store SHALL be left running and reported. Cleanup SHALL be idempotent, retried, and verified against Fly, and a cleanup failure SHALL be reported for operator attention rather than silently dropped.

#### Scenario: Destroy an orphan

- **WHEN** a Machine carrying the factory's marker is past its deadline and no recorded attempt owns it
- **THEN** reconciliation destroys it and records that it did so

#### Scenario: Leave an unknown Machine within its deadline

- **WHEN** a Machine carrying the factory's marker is within its deadline but no recorded attempt owns it
- **THEN** reconciliation leaves it running and reports it, and it is destroyed once its deadline passes

_From `specs/factory-claim-lifecycle/spec.md`:_

### Requirement: Wait for provider quota without discarding progress

On a detected Codex usage limit, including during product judging, the factory SHALL preserve claim progress and persist an admission hold until the reliably interpreted reset time reported by the CLI. Only after recognizing a Codex rate-limit diagnostic, if no reset time can be interpreted reliably, it SHALL use a configurable default of five hours from detection. A generic execution error or an absent reset time alone SHALL NOT create a quota hold; generic errors SHALL follow ordinary failure recovery. The hold SHALL survive controller restarts. After it expires, execution SHALL remain subject to the admission window, pause state, and other applicable prerequisites. Under Fly execution, the hold SHALL stop the repetition's Machine with its root filesystem retained and start it when execution is eligible, as defined in `factory-fly-execution`.

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

#### Scenario: Hold a Fly attempt for Codex quota

- **WHEN** a Fly attempt's streamed output reports a Codex usage limit
- **THEN** the Machine is stopped with its disk retained, the hold is recorded, and the Machine is started and the suite resumed in place when execution is eligible without consuming a retry

_From `specs/factory-eval-reporting/spec.md`:_

### Requirement: Apply aggregate board verdicts without hiding partial results

When all requested repetitions complete their automated evaluation, settle with a confirmed non-resumable implementation-workflow failure, or settle as lost Machines under `factory-fly-execution`, the factory SHALL move the issue to Review. If any repetition has a suite-established product failure or a confirmed non-resumable implementation-workflow failure, the aggregate Verdict SHALL be `failed`; otherwise, when at least one repetition is reviewable, it SHALL be `pending-human-review`. Lost repetitions SHALL NOT influence the choice between `failed` and `pending-human-review`; the results comment SHALL list each lost repetition with its infrastructure reason and SHALL NOT present it as a product result. When every repetition is lost, the card SHALL move to Review with `infra-error` and each loss explained. The factory SHALL never assign `passed` in iteration 1 and SHALL NOT close the issue as part of automated completion.

If a technical failure exhausts its recovery retry, the lifecycle's stop behavior SHALL take precedence: move to Review with `infra-error`, preserving all completed results and explaining which repetitions remain unstarted. Any already-established product failures SHALL remain visible in the results comment. While unfinished work is automatically deferred, the card SHALL use Ready with the applicable `quota-deferred` or `infra-error` verdict so the existing claim can continue under the intake rules.

While a current claim has verified active execution, the card SHALL not retain a Verdict delivered for a superseded or earlier claim. The factory SHALL clear that stale Verdict while reconciling the active claim so the board does not present a concluded technical outcome as the status of running work.

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

#### Scenario: Complete with a lost repetition

- **WHEN** one repetition settled as a lost Machine and the others finished ready for human review
- **THEN** the card moves to Review with `pending-human-review`
- **AND** the results comment lists the lost repetition with its infrastructure reason and carries review commands only for the surviving repetitions

#### Scenario: Lose every repetition

- **WHEN** every repetition of a claim settled as a lost Machine
- **THEN** the card moves to Review with `infra-error` and each loss is explained

#### Scenario: Stop after exhausting technical recovery

- **WHEN** a technical recovery retry fails before all repetitions finish
- **THEN** the card moves to Review with `infra-error`
- **AND** the comment preserves completed results, any established product failures, and the list of repetitions left unstarted

_From `specs/factory-operations/spec.md`:_

### Requirement: Expose current operational status

`agent-factory status` SHALL show, per work kind, the slot holder and progress, waiting work and why it waits, blocked fix claims, settled fix claims with eligible review comments waiting for the slot, pending merge syncs and their last failure reason, pause state, blocking conditions, and the next permitted start time when it can be determined. For an eval attempt under Fly execution it SHALL show the Machine identity, the Machine's state including whether it is stopped for a quota hold, and the attempt's recorded deadline; it SHALL also list Machines that reconciliation reported as unknown to the store or as failed cleanup. It SHALL list only claims that are running, waiting, blocked, held, in Review, pending a merge sync, or holding a recorded cleanup failure; claims whose card is Done with nothing pending, and superseded claims, SHALL be omitted unless `--all` is given, which lists every saved claim. It SHALL expose enough saved state to distinguish active execution, an admission-window wait, a usage hold, a memory or disk hold, an unavailable prerequisite, a blocked claim, a waiting review round, and unfinished reporting. Status SHALL remain usable while execution is active and SHALL NOT start work or change execution controls.

#### Scenario: Inspect an active evaluation

- **WHEN** the operator requests status while a repetition is running
- **THEN** status identifies the active request and repetition progress without interrupting execution

#### Scenario: Inspect waiting work

- **WHEN** work cannot start because the factory is paused, outside its window, or held by a prerequisite, memory, or usage limit
- **THEN** status explains the blocking condition and shows the next permitted start time where known
- **AND** it does not invent a recovery time for a problem requiring operator action

#### Scenario: Inspect both slots

- **WHEN** an eval is running and a fix is blocked awaiting input with the `needs-input` label
- **THEN** status shows the eval slot's holder, the fix slot as free, and the blocked bug with its decline reason

#### Scenario: Inspect a waiting review round

- **WHEN** a settled claim has eligible review comments but the fix slot is busy
- **THEN** status names the claim, the PR, and that it waits for the slot

#### Scenario: Inspect an installation with history

- **WHEN** the database holds many Done and superseded claims and one running claim
- **THEN** status lists the running claim and none of the settled ones
- **AND** `status --all` lists every saved claim

#### Scenario: Inspect a Fly attempt

- **WHEN** the operator requests status while an eval repetition runs in, or is stopped in, a Fly Machine
- **THEN** status shows the Machine identity, whether it is running or stopped for a quota hold, and the attempt's deadline

#### Scenario: Inspect a reconciliation finding

- **WHEN** reconciliation has reported a Machine unknown to the store or a cleanup that could not be verified
- **THEN** status lists that Machine and the reported reason until it is resolved

This task's portion: writing the saved state that status renders for stopped Machines and
reconciliation findings ("Inspect a Fly attempt", "Inspect a reconciliation finding").

_From `specs/factory-operations/spec.md`:_

### Requirement: Document installation and service operation

The change SHALL provide installation, configuration, and service-management instructions for the supported Mac deployment, including Python/uv setup, required repositories, mirrors and working clones, Docker startup and memory allowance where Docker execution is used, the fix execution mode and what host mode keeps and gives up, the eval execution mode and what Fly execution keeps and gives up compared with Docker, model authentication, GitHub routing and board permissions, suite prerequisites, the packaged fix workflow and its contract version, explicit service paths including the LaunchAgent PATH, login behavior, preventing idle sleep, and the evidence retention period. For Fly execution the documentation SHALL cover the one-time Fly organization, app, and deploy-token setup, the amd64 base image build and the Runner revision it requires, each Fly setting and its default, that Cursor role profiles are unavailable on Fly, that the human-review command runs on the Mac against the collected artifact directory, that a lost Machine costs its repetition, and the worst-case cost per attempt implied by the deadline. Documentation SHALL state plainly that in host mode, on a machine where the operator's own GitHub login is available, the separate fix credential and the PR-only rulesets are conventions the launched process follows rather than boundaries an autonomous agent cannot cross, and that trusted-writer admission and human merge are the enforceable controls. Documentation SHALL explain doctor, status and `--all`, tick, pause, resume, service installation and restart, evidence locations including a host attempt's Runner session directory, the human-review handoff, the blocked-bug loop, and the merge sync. The GitHub setup documentation SHALL describe the harness setting as a branch resolved at admission, not a commit pin.

Credentials for the suite's candidate branch, the fix PR credential, the Fly deploy token, and board/routing credentials SHALL remain separately configured. Public example configuration SHALL contain no personal credentials or machine-specific paths. The documentation SHALL distinguish installing a working Codagent example from extending the factory with another work-kind or suite implementation; it SHALL NOT imply that unsupported kinds execute through configuration alone.

#### Scenario: Set up the supported deployment

- **WHEN** an operator follows the installation instructions with the required credentials, suite behavior, and a Runner branch that can run the packaged fix workflow
- **THEN** the operator can configure the board and local service, diagnose readiness, start normal execution of both kinds, inspect progress, pause and resume work, run a posted human-review command, and review a factory fix PR

#### Scenario: Reuse the public example

- **WHEN** another organization follows the public setup documentation
- **THEN** it can substitute its own GitHub identities, credentials, and paths without relying on Paul's local environment
- **AND** the documentation clearly identifies any additional handler, suite, or workflow implementation needed for different work behavior

#### Scenario: Set up host execution

- **WHEN** an operator follows the instructions to run fixes on the host
- **THEN** the documentation tells them the setting, the doctor checks to pass, the PATH the service needs, what the sandbox guarantees they lose, and that the credential and ruleset are not enforceable against the agent

#### Scenario: Set up Fly execution

- **WHEN** an operator follows the instructions to run evals on Fly
- **THEN** the documentation tells them the Fly setup steps, the image build, the settings and defaults, the doctor checks to pass, the transport the service needs, what changes for human review, and what a lost Machine costs


## Test Plan

Automated tests never contact Fly; use the fakes in `tests/fixtures/fly/`. The E2E follows
`tests/e2e/test_factory_cycle.py` (fake GitHub, factory CLI `tick`/`status`). Unit cases are your
TDD decisions. Agent-acceptance (`AT-*`) and human-only (`HT-*`) flows in `test-plan.md` are not
yours to execute.

### INT-006: Disposal and reconciliation in the cycle
- Boundary: controller result consumption and reconciler against the fake API.
- Setup: finished runs with results classified settled, exhausted, cancelled, Codex quota, and
  technical-with-retry; fake Machines tagged for this factory: one past deadline with no run,
  one within deadline with no run, one untagged.
- Action: run one controller cycle.
- Assertions: settled and exhausted → destroy; quota → stop with the deadline set to the later of
  hold expiry and the next window opening plus total limit plus grace, `fly:machine:<claim>`
  recorded, and refreshed on a later cycle when the hold moves; technical with retry → Machine
  kept; expired orphan destroyed; unknown orphan recorded under `fly:unknown` and untouched;
  untagged Machine untouched; a stopped held Machine within its deadline untouched; a failed
  destroy recorded under `fly:cleanup-failed` and surfaced by status; the aggregate verdict with
  one lost repetition and two reviewable ones is `pending-human-review` with the loss listed, and
  with all lost is `infra-error`.
- Execution: `tests/integration/test_fly_disposal.py`.

### E2E-001: Eval request to settled result through Fly with restart and Machine loss
- Surface: the factory CLI (`tick`, `status`) with the fake GitHub used by `test_factory_cycle.py`.
- Setup: local config with evals on `fly`; fake Machines API; fake `flyctl` shim whose job
  execution copies a fixture result tree into the guest artifacts and writes `DONE`; three
  repetitions.
- Journey: request an eval; tick until the first Machine is created and the heartbeat advances;
  stop the controller and watcher mid-run and tick again; let repetition one settle; during
  repetition two delete the fake Machine out from under the launcher; tick until repetition three
  settles.
- Assertions: after restart the same Machine id is adopted and no retry is consumed; repetition
  one is collected once and its Machine destroyed; repetition two settles as failed with reason
  machine lost and consumes no retry; repetition three runs in a new Machine; the card reaches
  Review with `pending-human-review`, the results comment lists repetition two as lost with its
  reason and carries review commands for repetitions one and three only; status shows no live
  Machine at the end.
- Execution: `tests/e2e/test_fly_eval_cycle.py`.

## Done When

- Every scenario in the Spec section that falls in this task's portion is covered by a passing test,
  and INT-006 and E2E-001 pass at the named paths.
- Beyond INT-006's listed cases, `tests/integration/test_fly_disposal.py` also covers a settled
  run and a quota-held run whose Machine metadata no longer matches: neither a stop nor a destroy
  call is issued and `fly:mismatch` is recorded.
- Under `eval.execution = "docker"` (or unset) no Fly API call is made in a cycle and existing
  reporting tests pass unmodified.
- `docs/installation.md`, `docs/operations.md`, and `docs/suite-integration.md` cover every item the
  documentation requirement lists for Fly; `poc/` is gone.
- `uv run ruff format --check . && uv run ruff check . && uv run pyright && uv run pytest` passes.
