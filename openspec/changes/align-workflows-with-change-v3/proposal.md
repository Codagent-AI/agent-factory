## Why

Agent Runner's change v3 workflow runs Agent Validator once, near the end, in `verify-change`, and adds a crosscheck audit of the implementation inside `implement-change`. It plans no tasks, runs no per-task Validator, and does not enable task-compliance. The factory's fix, feature, task, and review-round workflows still follow the earlier shape: a feature writes `tasks.md`, runs `implement-task` (Validator with task-compliance on), `verify-change` (Validator again), and then its own task-compliance gate (up to three more repairs), with no audit; fix and task run two double Validator gates plus a tester and a lead review; review rounds run the Validator twice. Research into Validator cost points at fix loops and at churn inside fix steps, not at running a review once.

## What Changes

- **Feature:** `factory-feature` and `factory-define` become `-v2.0`. Definition no longer writes `tasks.md`. Implementation runs the Runner's `core/implement-change-v2.0` (checklists, crosscheck audit, one Validator run, draft pull request, acceptance). The change is archived after verification, as in change v3. The task-compliance gate, its repair loop, record, outcome field, and annotation items are removed.
- **Fix and task:** after triage, a lead step writes a small definition outside the repository and runs the same `implement-change`. The initial and final Validator gates, the tester flow test, and the lead review are removed. Fix and task gain a `crosscheck` role.
- **Review rounds:** the implementation sub-workflow gains a crosscheck audit of the round's diff against the plan, and runs the Validator once instead of twice.
- **Constraints:** every change's `design.md` gets the factory implementation constraints, including a ban on loosening timeouts or changing dependencies to pass a check.
- **Failure and resume handling:** a blocked implementation becomes `needs-input`; verification failures are found inside `implement-change`; checkpoints record verification so a resume does not repeat it; the archive and checkpoint commits do not raise the orange "Commits after acceptance" item.
- **Launch checks:** the recorded Runner commit must carry the builtins the workflows call.
- The design, role mapping, resume rules, risks, and open decisions are in `design.md`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `factory-feature-execution`: implementation through `implement-change`, archive after verification, no `tasks.md`, no task-compliance gate, qualified validator status, resume rules for verified checkpoints.
- `factory-feature-reporting`: the task-compliance report is removed; archive and checkpoint commits are not flagged as unreviewed.
- `factory-fix-execution`: the fix is defined, audited, and validated once through `implement-change`.
- `factory-task-execution`: the same for tasks, with the boundary guard ordering, chore commits, and the crosscheck role.
- `factory-review-execution`: a round's crosscheck audit and single Validator run.
- `factory-operations`: the task role profiles include `crosscheck`.

## Out of Scope

- Any change to Agent Runner, Agent Validator, or Agent Evals. Two Runner changes that would improve the result are listed in `design.md`.
- The `factory-*/1` contracts, outcome files, and claim lifecycle.
- Choosing which model profiles are best.

## Impact

- **Code:** `src/agent_factory/work_kinds/pull_request/` (`kinds.py`, `launch.py`, `handler.py`, `readiness.py`) and its `workflow/` directory (six workflows renamed to `-v2.0`; new `add-constraints.sh`, `record-blocked.sh`, `verify-pending.sh`, `factory-change-rules.md`; changed `checkpoint.sh`, `prepare-branch.sh`, `record-outcome.sh`, `verify-failure.py`, `annotate-pr.py`, `mark-later-commits.py`, `pr_description.py`, `verify-classification.py`; removed `task-compliance-gate.py`).
- **Configuration:** `config/codagent.toml` gains `crosscheck` in `[fix.defaults]` and `[task.defaults]`.
- **Deploy:** deploy the factory with the Runner (the default), because the workflows call `core/implement-change-v2.0`. A live run on this build is required before merge.
