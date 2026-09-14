# Task: Extract the work-kind handler seam behind the eval controller

## Goal

Move every eval-specific decision in the controller and runtime behind one `WorkKindHandler` interface so that the generic core owns admission serialization, claims, runs, recovery accounting, cancellation, presentation delivery, and event delivery, while `EvalHandler` owns everything that mentions repetitions, `Type=Eval`, `and-scene`, scores, or `EvalDefaults`. This is a behavior-preserving refactor: the existing test suite is the regression net and must pass without edits other than test setup that constructs `Controller` directly.

## Background

All paths are in `agent-factory`. Planning sources are `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "Components", "The handler interface", "Cycle", "Blocked loop" for the presentation rule, and "Decisions: Extract first, then add"), the specs under `specs/`, and `test-plan.md` in that change directory.

Today `src/agent_factory/controller.py` takes `EvalDefaults` and `harness_sha` in `Controller.__init__`, `_eligible` requires `Type=Eval`, `accept` writes `kind="eval"` and calls `ParsedRequest.freeze`, `_next_unit` and `_settle_if_complete` reason about repetitions, `_completion_message` renders scores, and `record_result` writes global quota holds. `src/agent_factory/runtime.py` (`cycle`, `_prepare_claim_worktrees`, `_plan_attempt`, `eval_defaults`, `_resolve`, `_resolve_revision`, `_snapshot`, `_fresh_requested`, `_consume_results`, `_review_command`, `_report`) constructs `AndSceneAdapter`, `GitWorktreeManager`, and `WorktreeCleanup` from `src/agent_factory/suites/and_scene/__init__.py` inline and forces every idle, unsettled claim to `Ready` in `_report`. `src/agent_factory/work_kinds/eval/__init__.py` holds only parsing, freezing, and result aggregation; `src/agent_factory/work_kinds/__init__.py` is an empty registration module. The supervisor in `src/agent_factory/supervisor.py` is already kind-agnostic and must not change in this task.

Deliver:

- `src/agent_factory/work_kinds/base.py`: a `WorkKindHandler` `Protocol` with the shape from the design, plus the shared dataclasses it needs. Design signature, to be adapted to existing types where they already exist in `controller.py` (`RequestSnapshot`, `ExecutionPlan`, `AttemptResult`, `ClaimPresentation`) rather than duplicated:

  ```python
  class WorkKindHandler(Protocol):
      kind: str
      def snapshot(self, card, client, shared) -> RequestSnapshot | None
      def accept(self, snapshot, store, resolve) -> ClaimDraft | Feedback
      def readiness(self, local, shared) -> list[Diagnostic]
      def prepare(self, claim) -> Preparation
      def next_unit(self, claim, runs) -> tuple[str | None, str]
      def plan(self, claim, run, preparation) -> ExecutionPlan
      def read_result(self, run) -> AttemptResult
      def classify(self, run, result) -> Classification   # technical | settled | blocked | quota
      def settle(self, claim, runs) -> Outcome | None
      def presentation(self, claim) -> ClaimPresentation
      def report_events(self, claim, run, result) -> list[Event]
      def gesture(self, claim, card, comments) -> "fresh" | "unblock" | None
      def limits(self, local) -> SupervisionLimits
      def window(self, local) -> ScheduleConfig
      def providers(self, claim) -> set[str]
      def cleanup(self, claim) -> None
  ```

  Exact names may follow existing conventions, but every listed responsibility must be reachable through the handler and nothing eval-shaped may remain in the core. `ClaimPresentation` gains `labels: Mapping[str, bool]` so a handler can request a label on or off; the runtime applies labels through one `set_attention_label` call per change with a delivery receipt, the same pattern used for `needs-input` today.
- `src/agent_factory/work_kinds/eval/__init__.py` (or a sibling module in that package): `EvalHandler` wrapping today's parse, freeze, worktree preparation, and-scene plan construction, result reading, quota deadline computation, repetition-based `next_unit` and `settle`, score rendering in completion messages, the review-command handoff text, `gesture` recognizing only the existing cleared-Verdict fresh-request rule, and `presentation`. The current `_report` fallback that forces idle, unsettled claims to `Ready` moves into `EvalHandler.presentation` (waiting eval claims present `Ready` with their deferral verdict) and the runtime applies the handler's presentation as authoritative for idle states. The generic correction rule then reads: if the card's column differs from the presented status, restore it and comment once, except where the handler's `gesture()` recognizes the drag. Eval behavior under this rule must be observably identical to today.
- `src/agent_factory/work_kinds/__init__.py`: static registration returning `{"eval": EvalHandler(...)}` built from `SharedConfig` and `LocalConfig`.
- `Controller.__init__` takes `handlers: Mapping[str, WorkKindHandler]` instead of `EvalDefaults` and `harness_sha`; `accept`, `reserve_next`, `record_result`, `presentation`, `cancel`, `deliver_reports`, `_eligible`, `_next_unit`, `_settle_if_complete`, and `_completion_message` dispatch to `handlers[claim.kind]`. Keep the admission advisory lock, `reserve_run`, recovery accounting (`recovery_attempts`, `max_recovery_attempts`), quota hold storage, event markers, and reporting progress in the core unchanged.
- `runtime.cycle` composes handlers by kind: consume results, reconcile per card through the card's claims and their handler, report, clean up on Done, then admit. Keep the single global slot, the single readiness setting, the global quota setting, and the eval window exactly as they behave today; do not add per-kind slots, memory checks, or any fix behavior here.
- `src/agent_factory/operations.py` `doctor` and `status` keep their current output; where they read eval defaults or the harness pin they may go through the eval handler but must print the same lines.

Constraints: no new work kind, no schema change, no configuration change, no supervisor change. The `and_scene` suite module stays where it is; `EvalHandler` composes it. Keep `pyright` strict and `ruff` clean under the settings in `pyproject.toml`.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-claim-lifecycle/spec.md`. This task delivers the extraction and the eval side of the gesture rule; the fix handler named below is not part of this task and must not be created here.

### Requirement: Keep lifecycle behavior independent of work kind

Core scheduling, claim/run persistence, retries, cancellation, restart recovery, and durable reporting SHALL remain independent of Codagent identities and eval-specific parsing or scoring. Work kinds SHALL supply request interpretation, execution planning, result interpretation, report presentation, admission-time input resolution, kind-specific readiness checks, and the kind's fresh-attempt and unblock gestures through one handler interface that the controller and runtime use for every kind. The eval handler SHALL own repetition semantics and suite integration; the fix handler SHALL own bug eligibility, branch resolution, workflow invocation, and outcome mapping. The generic core SHALL NOT interpret a score out of 70, Runner/Skills fields, an `and-scene` command path, a pull-request outcome, or the blocked state's label. Extracting this interface from the existing eval-shaped controller SHALL preserve all existing eval behavior.

#### Scenario: Integrate another work kind later

- **WHEN** a later change supplies another work-kind handler
- **THEN** it can reuse the saved-claim, execution-attempt, scheduling, cancellation, and recovery behavior without adding that kind's request or scoring rules throughout the controller

#### Scenario: Extract the interface without changing eval behavior

- **WHEN** the eval handler is moved behind the handler interface
- **THEN** existing eval intake, execution, reporting, and recovery behavior is unchanged and its existing tests pass unmodified

#### Scenario: Read a kind-specific gesture

- **WHEN** a human drags a settled fix card from Review to Ready
- **THEN** the controller asks the fix handler, which treats it as a fresh-attempt request
- **AND** an eval card dragged the same way keeps its existing correction behavior

This task's portion of that scenario: the controller asks the card's handler, and `EvalHandler.gesture` returns the existing correction behavior. The fix half is delivered elsewhere.

### Requirement: Correct status edits that contradict factory execution (extract)

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. [...] A drag that the request's work-kind handler recognizes as a fresh-attempt gesture SHALL be honored rather than corrected.

#### Scenario: Move an idle request to Running

- **WHEN** a human moves a factory-owned queued request to Running while it has no active execution
- **THEN** the next successful poll restores its appropriate queued status
- **AND** the drag itself creates no execution attempt and bypasses no admission control
- **AND** the factory briefly explains the correction on the issue

## Test Plan

- `E2E-001` (Handler extraction preserves the eval suite): the whole existing `tests/` tree runs at the extraction commit with `uv run pytest`. Every pre-existing test passes with no edits other than constructing `Controller` with `{"eval": EvalHandler(...)}` where tests build it directly. Any other test modification is a review finding. Enforced by this task's ordering and PR review rather than a new test file.

## Done When

- `src/agent_factory/work_kinds/base.py` defines `WorkKindHandler` and the shared dataclasses; `EvalHandler` implements it; `Controller` and `runtime.cycle` contain no references to `EvalDefaults`, `Type=Eval`, `and-scene`, repetitions, or score rendering (a `grep -n "repetition\|and-scene\|EvalDefaults\|harness_sha" src/agent_factory/controller.py src/agent_factory/runtime.py` is empty apart from generic pass-through names).
- `ClaimPresentation` carries `labels`, and the runtime applies label changes through one `set_attention_label` call per change with a delivery receipt; eval claims request no label changes beyond today's `needs-input` feedback.
- The idle-claim `Ready` fallback lives in `EvalHandler.presentation`; the runtime treats the handler's presentation as authoritative and applies the generic correction rule with the `gesture()` exception.
- `uv run pytest`, `uv run ruff check .`, and `uv run pyright` pass; the diff under `tests/` touches only `Controller(...)` construction sites (E2E-001).
- Eval `doctor` and `status` output is unchanged for an unchanged configuration.
