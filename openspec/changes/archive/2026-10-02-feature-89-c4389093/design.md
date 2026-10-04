## Context

The feature workflow `factory-feature-v1.0.yaml` lives in
`src/agent_factory/work_kinds/pull_request/workflow/`. That directory is the source of truth. The
launcher stages it into the clone's untracked, git-excluded `.agent-runner/workflows/`. The
workflow implements the change through Agent Runner's `builtin:core/implement-task`, whose
`run-validator.sh` runs `agent-validator run --report --enable-review task-compliance --context-file
<tasks.md>`. Agent Runner is outside this repository, so the factory cannot change that step.

The following facts about Agent Validator (checked against the checkout at `origin/main` `a2619ea`)
shape the design:

- **Trust ledger.** The ledger lives in `<git-common-dir>/agent-validator/trusted-snapshots.jsonl`.
  `run` and `review` both call `reconcileStartup`, which returns `trusted` before any gate runs when
  the head tree is in the ledger. `--gate`, `--enable-review`, `--base-branch`, and `--commit` do not
  bypass it (agent-validator#176). A worktree shares the common dir. A separate clone does not.
- **Exit codes and statuses.** `review` exits 0 for `passed`, `no_applicable_gates`, `no_changes`,
  and `trusted`, so its exit code cannot show that a review ran. `review` has no `--report`.
- **Review records.** Each dispatched review writes
  `validator_logs/review_<entry>_<name>_<adapter>@<n>.<iteration>.json`, with `status` set to one of
  `pass`, `fail`, `error`, `skipped_prior_pass`, or `preserved_one_shot`, plus `violations` and an
  `attempt_id` for a real dispatch. The record names no base, head, tree, or context file.
  `task-compliance` is a one-shot review by default, so a re-run in the same log session preserves
  the first verdict (`preserved_one_shot`) instead of reviewing again.
- **Change detection.** `--base-branch <ref>` diffs `<ref>...HEAD` (three dots), which is the
  merge-base diff a pull request shows. A full SHA is a valid ref.
- **Declarations.** `agent-validator list` prints `Review Gates:` including disabled reviews, for
  example `- task-compliance (Tools: claude)`. PyYAML is not available to workflow scripts.

On the factory side:

- `annotate-pr.py` already adds a deterministic red item (`Validator red after acceptance fixes`)
  and the orange `Commits after acceptance` item, and writes the final tiers back to
  `review-attention.json`. `record-outcome.sh` turns those tiers into `review_attention_counts`.
- `outcome.py` does not validate `validator` or unknown keys for `factory-feature/1`, so new keys
  are additive for the parser.
- `handler._pr_message` writes the issue comment that links a produced PR.

## Goals / Non-Goals

**Goals:**

- Every declaring Feature run gets a task-compliance verdict that the factory produced and can
  prove, bound to the base, the head, the tree, and the tasks. When none can be produced, the run
  says so deterministically.
- The design needs no change in Agent Runner, Agent Validator, or the target repositories.
- The PR is never blocked, and the outcome value never changes.

**Non-Goals:**

- Fix, task, and review-round workflows, and evals.
- Re-reviewing after finalization's CI repairs.
- Removing the implement step's own task-compliance request. It stays as early repair.

## Approach

### Components

```
factory-feature-v1.0.yaml
  target-head ─ prepare-branch ─ … ─ implement ─ complete-task
     ─ archive ─ push-archive ─ verify ─▶ [task-compliance gate: verified] ─ classify ─ verify-classification
     ─ finalize ─ annotate-pr (adds task-compliance items) ─ pr-details ─ record-outcome (qualifies validator)

task-compliance-gate.py            (new, workflow dir)
  review   one isolated review iteration → task-compliance.json; exit 0 = settled, 1 = violations
annotate-pr.py                     (changed) deterministic task-compliance items + uncovered commits
record-outcome.sh                  (changed) validator.checks / validator.status / task_compliance
handler._pr_message                (changed) one sentence when the result is not passed
```

### `task-compliance-gate.py review`

The script takes JSON input from `script_inputs`: `phase` (`verified`),
`artifact_dir`, `tasks_file`, and `target_head`. Each invocation does the following:

0. **Effective target ref.** The target ref used for the base is the target head the claim's branch
   actually contains:
   - When `{{artifact_dir}}/base-merge.json` exists, which `prepare-branch.sh` (through
     `merge-base.sh`) writes on every resume or continuation, the ref is its `base_head`. That
     covers merged, already-current, and conflict-resolved resumes.
   - Otherwise, on a fresh start, it is `target_head`, which the branch was created from.

   `target_head` alone is wrong on a resume, because it is the frozen admission commit, older than
   the head `prepare-branch` merged. Diffing from it would put target-side changes in the reviewed
   scope. The chosen ref is recorded as `target_ref`.

1. **Declaration and entry points.** It runs `agent-validator list` in the claim's clone:
   - It looks for a `- task-compliance` line under `Review Gates:`. If the line is absent, it records
     `not-declared` and exits 0.
   - It reads the `Entry Points:` section (`- <path>` followed by `Reviews: …`) and keeps the paths
     whose `Reviews` include `task-compliance` as `declaring_entry_points`.
   - If `list` fails or the sections cannot be parsed, it records `not-run` with reason `validator
     configuration unreadable: …` and exits 0.
2. **Reuse check.** It loads `{{artifact_dir}}/task-compliance.json` if one exists. It keeps the
   existing verdict (`passed` or `failed`) and exits 0 when all three of these hold:
   - `git diff --name-only <reviewed_head> HEAD` lists no path outside `openspec/`;
   - the normalized tasks hash is unchanged;
   - the target ref and base are unchanged.

   A `not-run` record is never reused. Records from earlier attempts are never seen, because the
   artifact directory belongs to one attempt.
3. **Binding.** It computes:
   - `head = git rev-parse HEAD` and `tree = git rev-parse HEAD^{tree}`;
   - `base = git merge-base <target_ref> HEAD`;
   - `tasks_sha256`, the SHA-256 of the tasks file with every task checkbox normalized to `- [ ]`,
     so checkpoint task ticks do not change the hash.
4. **Isolated run.**
   - It runs `git clone --quiet --shared --no-checkout <clone> <tmp>/review` and then
     `git -C <tmp>/review checkout --detach <head>`. The clone has its own git dir, and so an empty
     trust ledger, no `validator_logs`, and no one-shot history. Every run is a fresh full dispatch.
   - It copies the tasks file to `<tmp>/tasks.md`. Inside the review clone it runs `agent-validator
     review --gate task-compliance --enable-review task-compliance --context-file <tmp>/tasks.md
     --base-branch <target_ref>`. The captured stdout and stderr are kept.
   - The environment is the workflow's. No metrics flags are passed: the factory's review is
     evidence for the PR, not a Runner metrics source.
5. **Verdict from evidence.** The review clone is fresh, so every record in it belongs to this run.
   When every gate passes, the validator auto-cleans (`cleanLogs`): it moves the current log files
   into `validator_logs/previous/` (rotating older `previous*` directories) or, with
   `max_previous_logs: 0`, deletes them. The script therefore gathers evidence from three places:
   - **Records:** `review_*task-compliance*.json` in `<tmp>/review/validator_logs/` and in every
     `<tmp>/review/validator_logs/previous*/` directory.
   - **Job lines:** the captured output lines (the validator prints them on stderr with
     `console.error`; both streams are read) of the form `[PASS]|[FAIL]|[ERROR]
     review:<entry>:task-compliance (<adapter>@<n>) (<secs>) - <message>`. These survive any
     cleaning. A line whose message says the state was preserved or a prior pass was skipped is not
     a dispatch.
   - **Jobs:** the entry points named in those job lines (or in the record file names,
     `review_<entry>_task-compliance_…`).

   It then decides:

   | Evidence | Result |
   | --- | --- |
   | Any record with `status: fail`, or any `[FAIL]` job line | `failed`, with every violation from the records (file, line, issue, fix, priority). A failing run is never auto-cleaned, so its records are present |
   | Every dispatched job passed (records `status: pass` or `[PASS]` job lines that are dispatches), at least one dispatch, and the coverage check holds | `passed` |
   | Every dispatched job passed, but the coverage check finds uncovered paths | `not-run` with reason `task-compliance did not see: <paths>` (the reviewed part passed; the record keeps the per-job results) |
   | Any `error` record or `[ERROR]` line and no fail | retry the isolated run once, then `not-run` with reason `review error: …` |
   | No dispatch: no records and no job lines, or only `skipped_prior_pass` or `preserved_one_shot` | `not-run`, with the reason from the validator's console output (`no_applicable_gates`, `no_changes`, `trusted`, or the last line), or `no task-compliance review record` |

   The exit code is never evidence. That way a skip can never pass.

   **Coverage check.** The validator scopes each review job's diff to its entry point
   (`git diff … -- <entryPointPath>`), so a declaration under a subdirectory does not show changes
   elsewhere to the reviewer. The script lists `git diff --name-only <base> <head>` and removes:
   - paths under `openspec/`, which are the change's own artifacts;
   - paths under an entry point that declares task-compliance and has a dispatched job (`.` covers
     everything).

   Any path left is uncovered. Paths that a declaring entry point's own `exclude` list removes are
   treated as covered, because `list` does not print excludes and the target chose not to review
   them. A target that declares task-compliance at `.` therefore never has uncovered paths.
6. **Record.** It writes `{{artifact_dir}}/task-compliance.json` (schema below) atomically. It
   copies the review clone's `validator_logs` and console output to
   `{{artifact_dir}}/task-compliance/<phase>-<n>/` as evidence, removes `<tmp>`, and exits 1 only
   for `failed`.

`task-compliance.json` holds the latest result. Each run is appended to `runs`.

```json
{
  "result": "passed | failed | not-run | not-declared",
  "reason": "string, for not-run and not-declared",
  "phase": "verified",
  "target_head": "<sha>", "target_ref": "<sha>", "base": "<sha>",
  "declaring_entry_points": ["."], "uncovered_paths": [],
  "reviewed_head": "<sha>", "reviewed_tree": "<sha>",
  "tasks_file": "<path>", "tasks_sha256": "<hex>",
  "violations": [{"file": "", "line": 0, "issue": "", "fix": "", "priority": ""}],
  "runs": [{"phase": "", "head": "", "result": "", "reason": "", "evidence": "<dir>"}]
}
```

### Workflow steps

The single gate before classification uses the same structure as Runner's `run-validator`: a
loop of at most three, then one verification-only run.

```yaml
- id: task-compliance-verified
  loop: {max: 3}
  continue_on_failure: true
  steps:
    - id: task-compliance-review
      script: task-compliance-gate.py   # phase: verified, tasks_file: {{archived_dir}}/tasks.md
      continue_on_failure: true
      break_if: success
    - id: task-compliance-repair
      session: implementor-agent
      mode: autonomous
      skip_if: previous_success
      prompt: <repair prompt>
- id: task-compliance-verified-final
  script: task-compliance-gate.py
  skip_if: previous_success
  continue_on_failure: true
```

The gate sits after `restore-skipped-verify-status` and before `classify`. It skips when an outcome
exists or `validator_status` is not `passed`. A failed verify produces no PR, so it needs no gate.
The gate does not change `validator_status`, so a `failed` or `not-run` result does not skip
classification or finalization. The workflow uses an `implementor-agent` session (`agent:
implementor`), as the fix and task workflows already have.

The repair prompt points the implementor at the `violations` in `task-compliance.json` and tells it
to:

- fix what the tasks require;
- run the repository's checks (`agent-validator check`);
- commit as `[{{step_id}}] …`;
- leave any violation it disagrees with unfixed, explaining why in its summary.

The gate records no skips. A disagreement leaves the violation in place, and the PR shows it red,
which is the honest outcome for a human to judge. The repair session never runs `agent-validator
review` or `update-review`, because the factory's run is the only verdict that counts.

The `classify` prompt gains one sentence: the workflow adds task-compliance items from
`task-compliance.json`, so the agent should not add any.

### Annotation (`annotate-pr.py`)

After `flag_red_acceptance_validator`, a new `flag_task_compliance(flags, artifact_dir)` step runs:

- It removes any item in any tier whose title is one of the fixed titles, then adds exactly one:

  | Result | Tier | Title | Detail | Link |
  | --- | --- | --- | --- | --- |
  | `not-run` | red | `Task-compliance did not run` | the reason | `#acceptance-evidence` |
  | `failed` | red | `Task-compliance violations remain` | one clause per violation, `file:line issue` | `#acceptance-evidence` |
  | `not-declared` | yellow | `Task-compliance not declared` | the target declares no task-compliance review | none |
  | `passed` | white | `Task-compliance passed` | `reviewed <reviewed_head[:12]>` | none |

- If `task-compliance.json` is missing, which means the gate never ran because of a workflow
  defect, it adds red `Task-compliance did not run` with reason `no task-compliance record`.
- Once `later` is computed, it takes the commits in `<reviewed_head>..HEAD` and appends `Not covered
  by task-compliance: <sha12>, …` to the `Commits after acceptance` item, creating that item if
  none exists. The reviewed head is always at or after the accepted head, so these commits are a
  subset of `later`.

The tiers are written back to `review-attention.json` as today. The counts in the outcome, the
description, and the comment therefore all include the task-compliance item. The acceptance-evidence
section gains a short `Task-compliance` paragraph that names the base, the reviewed head, the
result, and the evidence directory.

### Outcome (`record-outcome.sh`)

A new input, `task_compliance`, carries the record's path. Only the feature workflow passes it,
and it applies only when the contract is `factory-feature/1`:

- `validator.checks` is set to the old checks-only status (`passed` or `failed`).
- When checks passed, the gate must have run, because every earlier stop writes its outcome before
  `record-outcome`. If the record is then missing or unreadable, `record-outcome` synthesizes
  `{result: not-run, reason: "no task-compliance record"}`. It never falls back to an unqualified
  pass.
- When checks passed, `validator.status` is mapped from the record (or the synthesized one):
  `passed` and `not-declared` give `passed`, `not-run` gives `incomplete`, and `failed` gives
  `review-failed`.
- `task_compliance` is set to `{result, reason, base, reviewed_head, tasks_sha256}`, which is always
  present when checks passed.
- When checks failed, `validator.status` stays `failed`, and `task_compliance` is included only if a
  record exists.

The branch that chooses the outcome still uses the checks-only status, so the outcome value is
unchanged. The fix and task contracts are untouched. `verify-feature-outcome.py` and `outcome.py`
need no change. A test pins that `outcome.py` accepts the new keys.

### Issue comment (`handler._pr_message`)

When `result["task_compliance"]["result"]` is `not-run` or `failed`, the comment appends `Task-
compliance did not run: <reason>.` or `Task-compliance violations remain.` after the flag counts.
Fix and task outcomes have no `task_compliance`, so their comments are unchanged.

## Decisions

- **Isolated shared clone, chosen over a direct re-run or validating from the task start.** It is
  the only option that gives the guarantee today. A worktree shares the ledger, and `skip` cannot
  move the baseline backward. `--shared` makes the clone cheap: it uses the local object store and
  checks out one tree. The isolated clone also avoids one-shot preservation and any earlier log
  state, so each run is a fresh full review. When agent-validator#176 lands, the clone remains
  correct and only costs a little extra. Removing it then is optional.
- **The implement step's review is never reused.** Its record names no base, head, tree, or context,
  so the binding requirement can never be proven from it. The factory runs its own review before
  classification. The implement step's review remains useful as early repair. The spec scenario
  states that its review does not count as the attempt verdict.
- **The base uses the effective merged target head.** On resume the base comes from
  `base-merge.json`'s `base_head`, the ref `prepare-branch` actually merged, rather than the frozen
  `target_head`. Otherwise target-side commits merged on resume would land in the reviewed diff.
- **Evidence survives auto-clean.** Passing runs rotate or delete their logs, so records are read
  from `previous*/` too, and the captured job lines are dispatch proof that also survives
  `max_previous_logs: 0`. A failing run keeps its records, so violations are always available.
- **Coverage is by declaring entry points.** A pass counts only when every changed path outside
  `openspec/` lies under an entry point whose task-compliance job was dispatched. Mixed scope
  becomes `not-run` naming the uncovered paths. Entry-point `exclude` lists are honored as the
  target's choice and are not flagged.
- **The gate sits before classification.** This covers the implementation step's skipped reviews
  and changes made by verification and acceptance, with one factory review per attempt. The
  archived tasks are the context, and checkbox-normalized hashing remains stable across checkpoints.
- **No skip channel for repairs.** It keeps the verdict the factory's own. A violation that is
  wrong or out of scope shows red with the implementor's reasoning in the session evidence, rather
  than being silently waived.
- **An error retries once, then becomes `not-run`.** A transient adapter error should not turn
  a run red when a second try would succeed. More retries would only burn the execution budget.

## Risks / Trade-offs

- **Cost.** Each declaring run adds one task-compliance review before classification and up to three
  repair rounds. Each review is one model dispatch of a few minutes. This fits inside the feature
  execution limits. Most runs pass on the first or second iteration.
- **Coupling to the validator.** The design depends on:
  - the `agent-validator list` text format (`Review Gates:` and `Entry Points:`);
  - the review record file names and `status` values;
  - the console job-line format;
  - the auto-clean rotation into `previous*/`. All parsing sits in one script, and every
  unrecognized state becomes `not-run`, so drift makes reports more conservative and never falsely
  green. Unit tests pin the parsed formats with fixtures copied from real runs.
- **Entry-point excludes are not checked.** `list` does not print excludes, so a path that a
  declaring entry point excludes counts as covered. This follows the target's own choice not to
  review those paths, and changes under `openspec/` are exempt anyway. A target that declares only
  under a subdirectory gets `not-run` for changes outside it.
- **Environment differences in the clone.** The review needs only the tree, `.validator/config.yml`,
  and the reviewer CLIs on `PATH`. Untracked build outputs are absent, which is fine for a review.
  Global validator config (`cli` inheritance) is read from the host, as in the main clone.
- **Temp space.** A shared clone checks out one tree. It is removed after each run, and on failure
  by a `finally` block.
- **Stale verdict in the PR.** CI repairs after the gate are not re-reviewed. They are named in the
  orange item as not covered. This is a deliberate trade-off (see proposal-review PR-1).

## Migration Plan

- The change is factory-only and takes effect with an Agent Factory deploy. Running claims keep
  their release.
- A claim admitted under the old release and resumed under the new one runs the gate before
  classification as usual.
- Rollback to a release without the gate is safe. The extra outcome keys are ignored, and no
  persisted claim state changes.

## Open Questions

None.
