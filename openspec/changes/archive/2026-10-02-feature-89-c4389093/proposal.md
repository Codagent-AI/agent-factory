## Why

Factory feature runs ask Agent Validator to run the `task-compliance` review against the change's
`tasks.md`. The request goes through Agent Runner's `core/implement-task` builtin: its
`run-validator.sh` passes `--enable-review task-compliance --context-file <task_file>`. When the
review runs, it finds real gaps. On host feature runs from 2026-09-27 to 2026-09-29 it failed the
first validator attempt on 7 of the 13 runs where it ran. But it is often skipped, and nothing notices:

- `Status: Trusted` (51640878, 691215c4). The implementer ran `agent-validator run` itself, so the
  trust ledger already covered the tree. The validator returned before it evaluated any gate,
  including the requested review (agent-validator#176).
- `Status: no_applicable_gates` (5992f3a5, 97bee934, f4a710d7, 15449656). The implementer had
  already committed, so there was nothing to gate, and the runner recorded a pass (agent-runner#201).
- `Status: no_changes` (d2165a71, on a resumed attempt).
- Later retries can return `Trusted` after an earlier retry ran the review (aea51318, retry 1). A
  `Passed` line does not name the gates that ran, so even a pass does not prove that task-compliance
  ran.

In each case the factory records `validator.status: passed` in the feature outcome, and the PR
description does not say that task-compliance never ran. A reviewer then sees a PR that looks
checked against its tasks when it was not, which is the guarantee this check is meant to provide.

The validator and runner root causes are tracked in their own repositories. This change makes the
factory own what it delivers. A Feature PR either carries a task-compliance verdict that covers the
code it ships, or says plainly, in the PR and in the outcome, that it does not.

## What Changes

- **A verdict is bound to what it reviewed.** A task-compliance verdict counts only for:
  - an explicit base: the merge base of the reviewed head and the target branch head the attempt
    started from or last merged, so the reviewed diff is the change the PR carries;
  - the reviewed head and its tree;
  - the hash of the `tasks.md` content given as context;
  - a review that actually ran to a terminal result.

  Skips, `skipped_prior_pass`-style records, and runs whose base or scope cannot be shown are not
  verdicts.
- **The factory runs the review it relies on.** At two points it runs a task-compliance-only
  validator pass over the full base-to-head diff, with the change's `tasks.md` as context:
  - after `implement`, so gaps are repaired before archive and acceptance;
  - before classification, once verification, acceptance repairs, and any resume merge are done,
    whenever the head has changed in validated code since the last verdict.

  The first run uses the implement step's bounded repair. The second records its result and repairs
  within the same bound. Its commits are post-acceptance commits, so the existing orange rule
  covers them. A verdict from the implement step is reused only when evidence proves all the
  binding facts above. Otherwise the factory runs its own. The tasks file is read from the archived
  change directory after archive, and its hash is checked against the one reviewed.
- **The factory records one result per attempt** in the attempt's evidence:
  - `passed`, with the reviewed head;
  - `failed`: violations remain after repair;
  - `not-run`: no verdict could be produced, with the validator status that caused it, such as
    `Trusted`, `no_applicable_gates`, `no_changes`, or an error;
  - `not-declared`: the target does not declare task-compliance.
- **The outcome stops reporting an unqualified validator pass.** In `feature-outcome.json`:
  - `validator.status` is `passed` only when the checks passed and a declared task-compliance review
    passed, or task-compliance is not declared;
  - it becomes `incomplete` when a declared review is `not-run`, and `review-failed` when it is
    `failed`;
  - a new `validator.checks` keeps the checks-only status;
  - a new `task_compliance` object carries the result, the reviewed head, the base, and the tasks
    hash.

  The outcome stays `pull-request`, so the PR is not blocked. The `factory-feature/1` contract name
  and outcome values are unchanged.
- **The PR and issue show the result.**
  - `not-run` and `failed` are deterministic **red** items in "Review first", added by the
    annotation step as the existing "Validator red after acceptance fixes" item is. The classifying
    agent cannot drop them.
  - Commits after the final reviewed head, such as finalization's CI repairs, join the existing
    orange "commits after acceptance" item, which also says task-compliance does not cover them.
  - `not-declared` is a yellow item, and a pass is a white item with its evidence.
  - The issue comment that links the PR names the task-compliance result whenever it is not
    `passed`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-feature-execution`: implementing the change, classifying review attention, and merging on
  resume gain the factory-owned task-compliance gate. That means the binding rules, the
  post-implement and pre-classification runs, and red classification for `not-run` and `failed`.
- `factory-feature-reporting`: the PR annotation shows the task-compliance result and the commits it
  does not cover. The PR comment names a result other than `passed`. The outcome carries the
  qualified validator status and the `task_compliance` object.

## Technical Approach

The work fits the existing feature workflow, `factory-feature-v1.0.yaml`, in
`src/agent_factory/work_kinds/pull_request/workflow/`. The launcher stages that directory into the
clone's untracked `.agent-runner/workflows/`. The change adds:

- a step that records the base;
- a gate step after `implement`/`complete-task` and before `seed-archive-status`;
- a second gate step after `verify` and before `classify`.

One script in the workflow directory runs both gates. `annotate-pr.py` and `record-outcome.sh` gain
the deterministic items and the qualified status. Agent Runner's `core/implement-task`,
`core/run-validator`, and `core/verify-change` builtins are unchanged, because they live outside
this repository.

**Base and resumes.** The base is the merge base of the head and the target branch head the
attempt started from or last merged. The reviewed diff is therefore what the PR carries, and it
excludes target-branch changes that a resume merge brought in. A resumed attempt that skips
`implement` computes the base the same way. A resume merge moves the head, so the
pre-classification gate always re-reviews after it. An earlier attempt's verdict never covers a
merged head.

**Running the review when the implement step's verdict is unproven.** This is the choice the issue
asks design to make explicit. The trust ledger lives in the git common directory, at
`<git-common-dir>/agent-validator/trusted-snapshots.jsonl`. `run`, `review`, `--gate`, `--commit`,
and `--base-branch` all consult it before evaluating gates, so a plain re-run on a trusted tree
skips again until agent-validator#176 is fixed. The candidates are:

1. **Isolated run.** Run `agent-validator review --gate task-compliance --enable-review
   task-compliance --context-file <tasks.md> --base-branch <base>` in a throwaway local clone of the
   head. The clone has its own git directory, and so an empty ledger. Repairs go to the real clone,
   and the review re-runs within the repair bound. This works today with no change outside the
   repository, but it relies on where the validator keeps its ledger.
2. **Direct re-run with an explicit base.** The same command in the claim's clone. It is the
   simplest, but it gives the guarantee only after agent-validator#176 and agent-runner#201 land.
   Until then it mostly turns silent skips into visible `not-run` results.
3. **Validate from the task-start commit.** This is the shape the issue mentions. But the only tool
   for moving the baseline, `agent-validator skip`, moves it forward, not back.

Recommendation: design option 1, falling back to option 2 when isolation fails. Either way, a run
that produces no verdict is `not-run`, never a pass. Design also decides whether the validator's
per-gate review records (`validator_logs/review_._task-compliance_<agent>.N.json` and rotated
`previous*/` copies) can prove a verdict's binding facts. If they cannot, the factory always runs its
own review, and the implement step's review serves only as early repair.

**Gating on the declaration.** The factory reads `.validator/config.yml` in the target clone for a
review named `task-compliance` under any entry point. When it is absent, the gate records
`not-declared` and runs nothing. That covers and-scene until and-scene#42 lands, and avoids the
silent no-op in agent-validator#175. When the base-to-head diff touches only paths the declaring
entry point excludes, the validator reports `no_applicable_gates`. The factory records that as
`not-run` with that reason, so the reviewer decides.

**Risks.**

- Cost and time. Most feature runs gain one or two task-compliance review calls, plus repairs,
  because verification and acceptance usually add commits. This stays within the feature execution
  limits.
- Coupling to the validator's CLI and log layout. It is kept in one script. Any unproven or
  unexpected state falls back to `not-run`, so a format change can only make reports more
  conservative.
- Semantics of `validator.status`. Only scripts in this repository write or read a feature outcome's
  `validator.status`, and no Python consumer reads it. The new values therefore stay inside the
  repository, and the specification states them.

## Out of Scope

- Bug and Task issues (factory fix and task runs), review rounds on factory PRs (`factory-review`
  and `factory-implement-v1.0.yaml`), and evals.
- Fixing the validator and runner root causes (agent-validator#175 and #176, agent-runner#201), and
  declaring task-compliance in and-scene (and-scene#42) or agent-skills.
- Making a skipped or failed task-compliance review block the PR or change the attempt's outcome.
- Re-reviewing after finalization's CI repairs. Those commits are reported as uncovered, not
  re-reviewed.

## Impact

- Code: `factory-feature-v1.0.yaml` in `src/agent_factory/work_kinds/pull_request/workflow/` (the
  launcher stages it into the untracked `.agent-runner/workflows/`), a new task-compliance gate script in the workflow
  directory, `annotate-pr.py`, `record-outcome.sh`, the PR outcome comment in the pull-request
  handler, and their tests.
- Specifications: deltas to `factory-feature-execution` and `factory-feature-reporting`.
- Data:
  - `feature-outcome.json` gains `validator.checks` and `task_compliance`, and its
    `validator.status` gains `incomplete` and `review-failed` for pull-request outcomes.
  - The attempt artifact directory gains a task-compliance evidence file with the base, head, tasks
    hash, result, and validator output.
- Users:
  - Reviewers of Feature PRs see a red item, and a qualified status in the outcome and issue
    comment, whenever the shipped change was not checked against its tasks. When it was, they see a
    white item with evidence.
  - Feature runs take longer by one or two targeted reviews and any repairs.
- Operations: needs a deploy of Agent Factory. It does not depend on Agent Runner, Agent Validator,
  or target-repository changes. When those fixes land, the implement step's verdict may become
  provable and the extra runs can shrink.
