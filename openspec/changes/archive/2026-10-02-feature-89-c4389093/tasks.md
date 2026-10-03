- [x] Make task-compliance reliably run, or visibly not run, in factory feature runs

## Task: Gate feature runs on a bound task-compliance verdict

Implement the whole change described in these files, in the change directory
`openspec/changes/feature-89-c4389093/`:

- `proposal.md`;
- the delta specs under `specs/`, both modified capabilities:
  - `factory-feature-execution`:
    - added "Gate the feature on a bound task-compliance verdict";
    - added "Qualify the validator status in the feature outcome";
    - modified "Classify review attention without blocking";
  - `factory-feature-reporting`: added "Report the task-compliance result";
- `design.md`;
- the decision log `decisions.md`. Where its rows differ, later rows override earlier ones. In
  particular:
  - the `spec` row on the base, and the `approach-review` rows RA-001 to RA-004, override the
    earlier base, evidence, outcome, and coverage rows;
  - the `design` row that changes the re-review trigger from "files the validator gates" to
    "any path outside `openspec/`" overrides the earlier `spec` row;
- the automated obligations in `test-plan.md`: INT-001 to INT-004 and E2E-001.

Rules:

- Always compare against `origin/main`.
- Never edit a release, the service clone, the live `~/.agent-factory` state, other claims' clones,
  or `/Users/paul/codagent/*`.
- Never deploy, and never change a real GitHub issue, board item, or branch.
- Fix, task, and review-round behavior must stay exactly as it is.
- Agent Runner and Agent Validator are outside this repository. Do not change them.

### Scope

1. **Gate script.** Add `task-compliance-gate.py` in
   `src/agent_factory/work_kinds/pull_request/workflow/`. Follow design "`task-compliance-gate.py
   review`", steps 0 to 6:
   - **Effective target ref:** `base-merge.json`'s `base_head`, or else `target_head`.
   - **Declaration:** from `agent-validator list`, reading `Review Gates:` and `Entry Points:`.
   - **Reuse:** a verdict is reused only when nothing outside `openspec/` changed, the
     checkbox-normalized tasks hash is unchanged, and the target ref and base are unchanged.
   - **Binding:** head, tree, and merge base.
   - **Review:** run `agent-validator review --gate task-compliance --enable-review
     task-compliance --context-file <copy> --base-branch <target_ref>` in a `git clone --shared`
     review clone of the head, made under a temporary directory and always removed.
   - **Verdict:** decide from review records in `validator_logs/` and every `previous*/` directory,
     plus the captured `[PASS]/[FAIL]/[ERROR] review:<entry>:task-compliance` stdout lines. Retry
     an error once. Never treat the exit code as evidence, and never treat preserved or skipped
     state as a dispatch.
   - **Coverage:** a changed path outside `openspec/` and outside every dispatched declaring entry
     point makes the result `not-run`, naming those paths.
   - **Record:** write `{{artifact_dir}}/task-compliance.json` atomically with the design's schema,
     including `target_ref`, `declaring_entry_points`, `uncovered_paths`, and `runs`. Copy the
     evidence to `{{artifact_dir}}/task-compliance/<phase>-<n>/`. Exit 1 only for `failed`.
   - Any unrecognized validator output gives `not-run`, never `passed`.
2. **Feature workflow** (`factory-feature-v1.0.yaml`, design "Workflow steps"):
   - Add an `implementor-agent` session (`agent: implementor`).
   - Insert the `verified` gate after `restore-skipped-verify-status` and before `classify`, with
     `tasks_file: {{archived_dir}}/tasks.md`. Skip it only when an outcome exists or
     `validator_status` is not `passed`.
   - Give the gate a loop of `max: 3`: a review step with `break_if: success`, and a repair step on
     `implementor-agent` with `skip_if: previous_success`. Follow the loop with one
     verification-only run, `continue_on_failure: true`.
   - Pass `artifact_dir` and `target_head` to the script.
   - No gate step captures `validator_status`.
   - Write the repair prompt as the design describes: fix what the tasks require, run `agent-validator
     check`, commit as `[{{step_id}}] …`, and leave unfixed any violation the implementor disagrees
     with, explaining why. It must never run `agent-validator review` or `update-review`.
   - Add one sentence to the `classify` prompt saying the workflow adds the task-compliance items.
   - Pass `task_compliance: "{{artifact_dir}}/task-compliance.json"` to `record-outcome`.
   - Add `task-compliance-gate.py` to `FEATURE_STAGED_FILES` in `kinds.py`, and keep it executable.
3. **Annotation** (`annotate-pr.py`, design "Annotation"):
   - Add `flag_task_compliance`. It removes items with the fixed titles from every tier and adds
     exactly one item per the design table.
   - A missing record gives a red `Task-compliance did not run` item with `no task-compliance
     record`.
   - Append `Not covered by task-compliance: …` for the commits in `<reviewed_head>..HEAD` to the
     `Commits after acceptance` item, creating the item if needed.
   - Add a short `Task-compliance` paragraph to the acceptance evidence.
   - Counts written back to `review-attention.json` must include the item.
4. **Outcome** (`record-outcome.sh`, design "Outcome"):
   - For `factory-feature/1` only, add `validator.checks`, map `validator.status` to `passed`,
     `incomplete`, or `review-failed` when the checks passed, and always include `task_compliance`
     then.
   - When the checks passed and the record is missing or unreadable, synthesize `not-run` with
     reason `no task-compliance record`.
   - The outcome value is still chosen from the checks-only status.
   - Fix and task outcomes must be byte-identical to today's.
5. **Issue comment** (`handler._pr_message`): append `Task-compliance did not run: <reason>.` for
   `not-run`, or `Task-compliance violations remain.` for `failed`, after the flag counts. Add
   nothing for other results or when `task_compliance` is absent.
6. **Tests**:
   - unit tests the specs imply: result mapping, reuse rules, tasks normalization, coverage, the
     outcome mapping, annotation items, and the comment sentence;
   - INT-001 and INT-002 in `tests/integration/test_task_compliance_gate.py`. INT-001 skips, naming
     the reason, when the real validator or a stub adapter is unavailable;
   - INT-003 in `tests/integration/test_feature_workflow_catalog.py`, with the structural assertions
     in `tests/integration/test_feature_workflow_scripts.py`;
   - INT-004 in `tests/integration/test_feature_workflow_scripts.py`;
   - E2E-001 in `tests/e2e/test_feature_cycle.py`.

   No test may call a real model. The existing fix, task, feature, review-contract, and catalog
   suites must pass unmodified, except where they list staged files.

### Done when

- On a claim clone whose head the validator already trusts, the gate runs a real task-compliance
  review in an isolated clone and records `failed` or `passed` bound to the merge base, head, and
  tasks hash. A pass is still recognized after the validator auto-cleans its logs, by rotation or
  deletion (INT-001).
- No-verdict cases record `not-run` with their reason and never `passed`. Resume merges review from
  the merged target head. OpenSpec-only commits keep the verdict, and code changes re-review.
  Subdirectory-only declarations name uncovered paths (INT-002).
- The Runner validates the feature workflow, and the single gate sits before classification
  (INT-003).
- The PR description shows exactly one task-compliance item in the right tier, with consistent
  counts. The outcome qualifies `validator.status`, including when the record is missing, and
  `outcome.py` accepts it (INT-004).
- A `not-run` or `failed` feature PR reaches Review with `pending-human-review` and a comment that
  names the result (E2E-001).
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest` pass,
  and `agent-validator run` is green.
- `openspec validate feature-89-c4389093 --strict` passes.
