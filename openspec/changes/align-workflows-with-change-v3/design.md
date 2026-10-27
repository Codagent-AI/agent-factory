## Context

Agent Runner's change v3 workflow (`openspec:change`) runs `define-change`, then
`implement-change-v2.0`, then acceptance, archive, and `finalize-pr`. `implement-change`
has the lead and the implementor each write a checklist of how every specified behavior is
verified, has the crosscheck merge the two, implements against the merged checklist with
evidence recorded per row, has the crosscheck audit the result by inspection, and has the
implementor resolve the findings. It then runs `verify-change`, which runs Agent Validator
once (three repair rounds at most), opens the draft pull request, and runs bounded
acceptance rounds. There are no tasks and no per-task Validator runs, and task-compliance
is not enabled. Validator runs once, near the end.

The factory has not followed. Today, per kind:

| Kind | Validator passes | Other review |
| --- | --- | --- |
| Feature | `implement-task` (task-compliance on by default), `verify-change`, then the factory's task-compliance gate with up to three repairs | none; no crosscheck audit |
| Fix | `run-validator` and a second `agent-validator run` gate with repair and recheck, twice (initial and final) | tester flow test, lead review, implementor fixes |
| Task | the same two double gates | tester (user-visible only), lead review, implementor fixes |
| Review round | `run-validator` and a second gate | none |

Research into Validator value says its findings are mostly real (about 76 percent), that the
security-and-errors review is the most precise, and that cost comes from fix loops and from
churn in the fix steps: loosened test timeouts, changed dependencies, and follow-up fixes to
earlier fixes. The and-scene evals of implement-change v2.x show the crosscheck audit is
spec-focused, and a variant where only the implementor writes the checklist scored about the
same at about 46 percent less cost (`origin/exp/implementor-checklist-only`, not on `main`).
This change aligns the factory with `main`'s v3 and notes where the variant slots in.

## Goals / Non-Goals

**Goals**

- Every pull request kind gets the crosscheck audit and exactly one Validator run, with its
  repair capped, before the pull request.
- Reuse the Runner's builtin workflows for everything the Runner owns, so the factory
  inherits their improvements, and keep in the factory only what is specific to it.
- Existing in-flight claims keep working across the deploy.

**Non-goals**

- No change to Agent Runner, Agent Validator, or Agent Evals.
- No change to the `factory-fix/1`, `factory-feature/1`, `factory-task/1`, or
  `factory-review/1` contracts, to outcome files, or to claim lifecycle.
- No change to triage, intake, or the board.

## Decisions

### Which Runner the workflows run on

Fix, feature, task, and review attempts on the host run the `agent-runner` on `PATH`. It is
built from the Agent Runner checkout, which `scripts/deploy.sh` fast-forwards to `origin/main`
and rebuilds on every deploy. `builtin:` references resolve inside that binary. The recorded
Runner commit is used only to build Docker sandboxes (`docker` mode of the fix kind) and for
`check_runner_contract`, which now also requires the builtins the workflows call. So the
workflows depend on `core/implement-change-v2.0`, `core/verify-change-v1.0`, and
`core/run-validator-v1.0`, all on Runner `main` today and in the binary installed now; none
of this needs an unreleased Runner change. `doctor` already runs `agent-runner -validate` on
every packaged workflow, which loads those builtins, so an older Runner stops new attempts at
readiness with a clear message. Deploy the factory and the Runner together (the default).

Two Runner changes would improve the result and are **not** in this change; see "Runner
requests".

### Target shape

The workflow files become `-v2.0` and the old `-v1.0` files are removed (see "Resume,
recovery, and rollback"). Contracts and outcomes are unchanged.

**Feature** (`factory-feature-v2.0`, `factory-define-v2.0`)

| Step | Source |
| --- | --- |
| contract check, OpenSpec preflight, branch preparation, merge of the target branch, conflict resolution, continuation rename, reconcile | factory (unchanged, except conflict resolution no longer runs the Validator) |
| define: proposal, proposal review, specs, design, test plan, approach review, OpenSpec validation, plan commit, `planned` checkpoint | factory (autonomous define with definition stops); `write-tasks` and `tasks.md` removed |
| append the factory implementation constraints to `design.md` | factory (`add-constraints.sh`) |
| implement: checklists, merge, implementation, evidence, crosscheck audit, findings, session report, `verify-change` (one Validator run, draft PR, acceptance) | **Runner** `builtin:core/implement-change-v2.0` |
| `implemented` checkpoint, marked verified | factory (`checkpoint.sh`) |
| archive and `archived` checkpoint | Runner `builtin:openspec/archive-change-v1.0`, factory checkpoint |
| classify review attention, annotate the description, record the outcome | factory |
| finalize | Runner `builtin:core/finalize-pr-v1.0` |
| standalone `verify-change` on the archived change | Runner builtin, run only on recovery paths |

**Fix** (`factory-fix-v2.0`)

| Step | Source |
| --- | --- |
| contract check, clean tree, triage, triage repair | factory (unchanged) |
| branch, change name, `define-fix` (a lead step writing a small definition) | factory |
| append the factory implementation constraints to the definition's `design.md` | factory |
| checklists, implementation, audit, findings, `verify-change` | **Runner** `builtin:core/implement-change-v2.0` |
| blocked implementation becomes `needs-input`; a failure inside verification becomes `failed`; any other failure stays a technical failure | factory (`record-blocked.sh`, `verify-failure.py`) |
| finalize, annotate (`Refs`, claim marker), lines of code, outcome | Runner `finalize-pr`, factory |

**Task** (`factory-task-v2.0`) is the fix shape plus the Task boundary: the same triage and
the Task boundary in triage; a `define-task` step whose definition includes a requirement
that the change stay within the boundary, checked by the deterministic scope floor script; the
boundary guard (`factory-task-guard`) after `implement-change` and again after finalization
when CI repair added commits; the `chore:` subject and title rules.

**Review round** (`factory-review-v2.0`, `factory-implement-v2.0`): triage, merge of the base
branch for features, replies, and outcome are unchanged. The shared implementation
sub-workflow becomes: implement the plan, have the crosscheck audit the round's diff against
the plan and the reviewer's comments (inspection only), have the implementor resolve the
findings, run Validator once through `builtin:core/run-validator-v1.0`, require a clean tree,
guard Task scope, push, and wait for CI. A round with no commits of its own (a merge alone)
skips the audit.

### How fixes and tasks get what implement-change needs

`implement-change-v2.0` expects a defined change: proposal, specifications, design, and a
test plan. Options:

1. **A lightweight generated definition** (recommended). After triage, the lead writes four
   small files from the issue and its own plan: `proposal.md`, `specs/<name>/spec.md` with the
   corrected behavior as SHALL requirements and WHEN/THEN scenarios (including a regression-test
   requirement), `design.md`, and `test-plan.md`. They go under `{{artifact_dir}}/definition`,
   outside the repository, with change kind `spec-driven`. Runner then validates the structure,
   skips committing it, and keeps its checklist and audit records beside it, so the pull
   request carries only the fix.
2. A fix-specific path: factory-owned checklist, audit, and evidence steps. It would give
   control over cost and acceptance, but it copies the Runner's prompts, which drift, and it
   cannot reuse the Runner's evidence scripts (`check-evidence.sh`, `check-checklist-merge.sh`),
   which resolve relative to Runner builtins. It would also not be what the evals measured.
3. Reuse only the checklist, audit, and verify parts. Not possible: those steps are not
   separately callable builtins; `implement-change` embeds them and `verify-change`.

Option 1 costs one lead step and gets the Runner's whole pipeline and every later
improvement for free, including the lighter variant when the Runner ships it (change the one
pinned `implement-change` reference in each workflow). Its costs are a checklist pass for
very small fixes and acceptance rounds that the Runner does not let a caller skip (a Runner
request below). The definition's `test-plan.md` says no agent acceptance is required beyond the
automated tests unless the issue reports a user-run flow, which sizes the tester's
exploration to nothing for ordinary fixes and for Tasks that are not user-visible.

### Where the audit runs, and the roles

The crosscheck audit is the Runner's, inside `implement-change` for feature, fix, and task.
For review rounds the factory runs a lighter audit of the round's diff against the lead's plan,
because a round has no checklist. Role mapping, by the factory's configured profiles:

| Runner session | Role profile | Used for |
| --- | --- | --- |
| `lead-agent` | `lead` | triage, definition, lead checklist, rulings on questions, definition and record repairs, acceptance fixes, PR |
| `implementor-agent` | `implementor` | implementor checklist, implementation, evidence, findings, simplification, session report, Validator fix steps (they inherit the most recent session) |
| `crosscheck-agent` | `crosscheck` | checklist merge, audit |
| `acceptance-tester` | `tester` | acceptance rounds |

Fix and task claims need a `crosscheck` profile, so `[fix.defaults]` and `[task.defaults]` gain
one. Their default is `codex:gpt-6.1-sol:high`, as for features and evals: the crosscheck merges and
audits against the lead's checklist, and the lead is Claude, so it uses the other model family. A claim frozen before this
change has no crosscheck, so the handler fills in the kind's configured default at launch
(`launch_roles`); every other role stays as frozen.

### One Validator run per kind

`verify-change` holds the only Validator run before the pull request: one `run-validator`, a
fix step after each red result, three rounds at most, then a verification-only run. A red
result stops the workflow before the pull request opens. The factory no longer runs
`agent-validator run` itself except through `core/run-validator` in review rounds. Removed:

- the initial and final validation gates, repairs, and rechecks in fix, task, and
  `factory-implement`;
- the Validator run in merge-conflict resolution (the attempt's one run covers the merged tree);
- the task-compliance gate (`task-compliance-gate.py`), its repair loop, its record, the
  outcome's `task_compliance` field, and the annotation's task-compliance items. It checked a
  `tasks.md` that v3 does not write, and task-compliance is opt-in in repository Validator
  configurations;
- the tester flow test and lead review plus findings in fix and task (acceptance and the audit
  replace them).

To contain the churn the research found, `add-constraints.sh` appends a "Factory implementation
constraints" section to the change's `design.md` (and the Task boundary and `chore:` rule for a
Task). Every Runner step reads the design, and the Validator's fix steps inherit the
implementor's session, which has read it. The section says: decide and record rather than ask;
no test-driven development; no manual testing by the implementor; Validator repairs never
loosen or raise a test timeout, skip or weaken a test, or change a dependency to pass a check;
and a check that fails the same way at the merge base, or whose only remedy is one of those,
is left failing and reported for a human. This replaces the three copies of the out-of-scope
CHECK failure prompt in the old workflows. It is guidance, not enforcement; see Risks.

### Archive after verification

Change v3 archives after acceptance, and the archive step advances the Validator baseline, so
the archive commit is never validated. The factory used to archive first so that acceptance
tested the tree the pull request carries. `implement-change` verifies inside itself, so the
factory now archives after it, as v3 does. The cost is that the archive commit and the phase
checkpoints follow the accepted head on every feature pull request. They move OpenSpec files
only, so `pr_description.is_bookkeeping` recognizes them: they are listed with the later commits
but do not raise the orange "Commits after acceptance" item, and `verify-classification.py`
does not ask the classifier to name them.

### Failure handling

`implement-change` stops without a decision when the lead rules that no compliant
implementation is possible without the user (an `implementation-blocked.md` record).
`record-blocked.sh` turns that into `needs-input` (for a feature, stopped step `implement`, the
branch pushed so the next attempt resumes from it). Other failures:

- inside `verify-change` (Validator red after repair, acceptance push rejected, no draft PR):
  `failed` with the failing step and its reason, as `verify-failure.py` already recorded for
  features, now also found under `implement/sub:implement-change/verify-change`, and now for fix
  and task as well;
- before verification (a checklist, implementation, or audit step that exhausted its repairs):
  `verify-failure.py --require-evidence` exits 3, ending the workflow with no outcome, which
  the factory retries once as a technical failure and settles as `infra-error` if it repeats.

### Resume, recovery, and rollback

Versioned files. The Runner selects the highest version of a logical name, and each attempt
stages its catalog fresh, so the packaged `-v1.0` workflow files are replaced by `-v2.0`
rather than edited in place. Running attempts keep the files and Runner binary they started
with. The factory never resumes a Runner run, so no in-flight Runner state refers to a v1 file.
The contract markers stay `factory-*/1`.

Feature claims resume from checkpoints in branch history, which the new flow must read
correctly alongside checkpoints the old flow wrote:

- `planned`, `implemented`, and `archived` keep their names and the resume mapping
  (`planned` to implement, `implemented` to archive, `archived` to verify).
- A checkpoint made after the change was verified carries a second trailer,
  `Factory-Verified: true`. Branch preparation turns a resume at verification into a resume at
  finalization when first-parent history since the admission target contains the trailer. A
  merge of the target branch that added commits sends a resume at finalization back to
  verification, as before, and makes a resume at archive verify after the archive, because
  those commits invalidate the earlier run. `verify-pending.sh` decides whether the attempt
  must verify on its own: a resume at verification, or at archive without the trailer or after
  such a merge.
- Old claims (no trailer) resumed at archive or verification archive the change, run
  `core/verify-change` on it as v1 did, and then push a verified checkpoint. A leftover
  `tasks.md` is ignored. A claim stopped at the removed `write-tasks` step still resumes in
  the right place because that name stays in the resume order as the marker for closing the
  definition.
- A draft pull request left by a crash inside `implement-change` resumes at verification: the
  attempt archives the implemented change and verifies it on its own. This repeats some
  work but never skips verification.

Fix and task attempts restart from a fresh clone, as before. Review rounds on claims frozen
before this change run with the configured crosscheck.

Rollback. Redeploy the previous release: workflows ship in the release, and the Runner
builtins it calls (`run-validator`, `verify-change`, `finalize-pr`) still exist on Runner
`main`. Branches carrying `Factory-Verified` trailers read as plain `implemented` or
`archived` checkpoints to v1, which re-verifies them, safely. Frozen crosscheck roles are
ignored. Deploy rollback is not blocked by any frozen input.

### Testing

- Existing suites cover what stays: contract and catalog staging, launch plans, readiness,
  outcome parsing and reporting, the record and checkpoint scripts, the Task guard.
- Updated in place: workflow structure tests for fix, feature, task, and review/implement (step
  order, sub-workflow references, sessions, outcome guards); the catalog tests that run the
  installed `agent-runner -validate` over every packaged workflow, which also proves the
  builtins and their required parameters exist; fixtures for roles and Runner clones.
- Added to existing suites: behavior tests for `record-blocked.sh`, `add-constraints.sh`,
  `verify-pending.sh`, the verified checkpoint trailer and branch preparation, the generalized
  `verify-failure.py` and its `--require-evidence` exit, bookkeeping commits in the description
  and classification, the Task crossing outcome with a draft PR, the validation gate in review
  rounds, `launch_roles`, and the required-builtins check. No new suites, no CI jobs, and no
  test asserts what a prompt says.
- Removed: the task-compliance gate suite and the cases that tested the removed steps.
- **Limit.** The acceptance tests stub the Runner; they prove launch, staging, and outcome
  handling, not that the workflows do the right thing with real agents. A live run on this
  change's build is required before merge (see the pull request).

### Runner requests (not in this change)

1. `implement-change` should pass `acceptance_rounds` and `acceptance_fix_scope` through to
   `verify-change`, and allow zero rounds. Until then, features lose the narrowed acceptance-fix
   scope (no workflow files, no CI) that `factory-feature-v1.0` passed, and fix and task get the
   Runner's three rounds. The standalone recovery path still passes the narrowed scope.
2. An option to stop `implement-change` before it pushes and opens the draft pull request would
   let a Task check its boundary before the first push, as before, and rewrite commit subjects
   to `chore:`. Until then the boundary is checked after the draft exists.
3. The Validator fix step in `run-validator` could carry the "never loosen timeouts or change
   dependencies" rule itself.

The lighter implementor-checklist-only variant needs none of this: when it lands in a Runner
version, change the pinned `implement-change` reference in the three workflows and drop nothing
else.

## Risks / Trade-offs

- **The first push precedes the Task guard.** `verify-change` pushes and opens the draft pull
  request, so a Task that crosses its boundary leaves an open draft and reports `failed` with
  the pull request, rather than `needs-input` with nothing pushed. The definition makes the
  boundary a checklist row verified by the scope floor script, so the implementor and the audit
  catch a definite crossing first. Commit subjects are not rewritten after the fact; non-chore
  subjects are recorded in the description, as for CI repairs.
- **Acceptance is not skippable.** Fix and task run the Runner's acceptance rounds, sized by
  the definition's testing envelope. Watch the cost on the first runs.
- **Constraints are guidance.** An agent can still loosen a timeout. The audit and the Validator
  review are the checks.
- **Definition quality.** A thin generated definition gives a thin checklist. The Runner's
  definition check and lead repair catch structural gaps only.
- **Archive after verification** gives a small unvalidated commit (documents only).
- **Stubbed acceptance tests** cannot catch a prompt that does not do what the step needs.

## Open Decisions

Paul's rulings:

1. Archiving after verification, with bookkeeping commits kept out of the orange items: accepted.
2. This change waits until the Runner passes `acceptance_rounds` and `acceptance_fix_scope`
   through `implement-change` to `verify-change`, so no acceptance regression ships.
3. The weaker Task guard ordering above: accepted as is, with no Runner stop-before-PR option.
4. Crosscheck defaults: Codex for every kind, because the crosscheck checks the lead (Claude),
   not the implementor.
