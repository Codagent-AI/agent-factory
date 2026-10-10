## Context

The feature workflow `src/agent_factory/work_kinds/pull_request/workflow/factory-feature-v1.0.yaml`
currently runs these steps after verification:

```
task-compliance-verified(-final) → classify → verify-classification → seed-ci-status → finalize
  → mark-ci-failed → seed-annotation-status → annotate-pr → mark-annotation-failed → pr-details
  → record-outcome → verify-outcome
```

- `classify` is a `lead-agent` session that writes `{{artifact_dir}}/review-attention.json`. It is
  skipped when an outcome already exists or when `validator_status != passed`.
- `verify-classification` runs `verify-classification.py` and has a `lead-agent` repair. It is
  not `continue_on_failure`, so an unrepaired rejection fails the attempt with no outcome.
- `finalize` is `builtin:core/finalize-pr-v1.0.yaml`. It pushes, marks the draft pull request
  ready, waits for CI, and may add `[fix-pr]` commits. `mark-ci-failed` captures `ci_status`.
- `annotate-pr.py` reads `review-attention.json` and adds the recorded task-compliance items and
  the safety-net "Commits after acceptance" entries. It writes the final classification back to the
  same file and edits the pull request description.
- `record-outcome.sh` takes `review_attention_counts: {{artifact_dir}}/review-attention.json`. It
  already skips a missing counts file when the outcome is `failed`. It exits 2 when the file is not
  valid JSON or not an object. An explicit `reasons` input overrides its default reasons on every
  branch. The feature workflow passes no `reasons` input today.

Workflow semantics in Agent Runner (`docs/writing-workflows.md`) that this design relies on:
- `continue_on_failure` may be combined with `repair`; `verify-final` in finalize-pr does this.
- `skip_if: previous_success` refers to the preceding step. At top level, a skipped step does not
  reset that state. This is the same reliance the existing `mark-ci-failed` has after a skipped
  `finalize`.
- `sh:` skip conditions interpolate captured variables.

## Goals / Non-Goals

**Goals:**
- Classification observes the branch, the pull request, and CI as finalization left them.
- A classification failure after finalization always produces a verified `failed` outcome. The
  outcome carries the pull request reference, the CI status, and a classification-specific reason,
  plus the CI reason when CI also failed. The pull request is not annotated from rejected data.
- Make no change to `review-attention.json`, `feature-outcome.json`, the classify prompt's tier
  rules, or Agent Runner.

**Non-Goals:**
- The fix and task workflows, and review rounds.
- Re-running task-compliance on commits that finalization adds.
- Changing how a failure of `annotate-pr` itself is reported. It keeps its current
  `pull request annotation failed` reason.

## Approach

### New step order

```
task-compliance-verified(-final)
  → seed-ci-status → finalize → mark-ci-failed
  → seed-classification-reasons → seed-classification-counts → seed-classification-status
  → classify → mark-classify-failed
  → verify-classification → mark-classification-invalid
  → record-classification-failure → clear-classification-counts
  → seed-annotation-status → annotate-pr → mark-annotation-failed
  → pr-details → record-outcome → verify-outcome
```

Step details (new or changed):

| Step | Kind | Behavior |
| --- | --- | --- |
| `seed-classification-reasons` | command | `printf '[]'`, capture `classification_reasons`. Unguarded, so `record-outcome` can always interpolate it. |
| `seed-classification-counts` | command | `printf '%s' "{{artifact_dir}}/review-attention.json"`, capture `classification_counts`. Unguarded. |
| `seed-classification-status` | command | `printf passed`, capture `classification_status`. Unguarded. It sits immediately before `classify`, so `mark-classify-failed` sees a success when `classify` is skipped. |
| `classify` | session (moved) | Prompt unchanged. Its `skip_if` is unchanged (outcome exists, or `validator_status != passed`). Add `continue_on_failure: true`. |
| `mark-classify-failed` | command | `printf session-failed`, capture `classification_status`, `skip_if: previous_success`. |
| `verify-classification` | command (moved) | Same command and repair. Add `continue_on_failure: true`. Its `skip_if` gains `\|\| test "{{classification_status}}" != passed`. |
| `mark-classification-invalid` | command | `printf invalid`, capture `classification_status`, `skip_if: previous_success`. When `verify-classification` was skipped, the preceding executed step was either `mark-classify-failed` (a success, so the status stays `session-failed`) or a seed (a success, so the status stays `passed`). |
| `record-classification-failure` | script | New `record-classification-failure.py`, with inputs `artifact_dir`, `classification_status`, and `ci_status`. Capture `classification_reasons`. `continue_on_failure: true`, so a crash cannot end the attempt. `skip_if: 'sh: test "{{classification_status}}" = passed'`. |
| `clear-classification-counts` | command | `printf ''`, capture `classification_counts`, `skip_if: 'sh: test "{{classification_status}}" = passed'`. After a classification failure, `record-outcome` gets an empty counts input. It already skips an empty input, so the outcome never depends on whether the rejected file was moved. |
| `seed-annotation-status` | command | `if test "{{classification_status}}" = passed; then printf passed; else printf failed; fi`. A skipped `annotate-pr` therefore still leaves `annotation_status=failed`. |
| `annotate-pr` | script | Its `skip_if` gains `\|\| test "{{classification_status}}" != passed`. |
| `record-outcome` | script | Add `reasons: "{{classification_reasons}}"`, and change `review_attention_counts` to `"{{classification_counts}}"`. All other inputs are unchanged. |

### `record-classification-failure.py`

A small, deterministic script in the workflow package. It is staged with the catalog like its
siblings and must be executable.

1. If `{{artifact_dir}}/review-attention.json` exists, rename it to
   `review-attention.rejected.json` in the same directory, replacing any earlier one, so the
   rejected data stays in the attempt's evidence. This is best effort. If the rename fails, the
   script leaves the file where it is, deletes nothing, and says in the reason that the rejected
   file could not be moved, naming the error. The outcome does not depend on this step:
   `clear-classification-counts` gives `record-outcome` an empty counts input on every failure
   path.
2. Print a JSON array of reasons to stdout:
   - `session-failed`: `review-attention classification failed after finalization: the classifying session failed; the pull request was not annotated`.
   - `invalid`: `review-attention classification failed after finalization: review-attention.json stayed invalid after repair (kept as review-attention.rejected.json); the pull request was not annotated`.
   - If no file existed, or the move failed, the reason says so instead of naming the rejected
     copy.
   - When `ci_status` is `failed`, append `CI did not pass within its fix cycle`. This is the
     same text `record-outcome.sh` uses by default, which the explicit reasons would otherwise
     replace.
3. Write the JSON array to stdout in one final write, so a crash leaves no partial output for
   the capture. Exit 0 in every case, printing at least the classification reason, so the run reaches
   `clear-classification-counts` and `record-outcome`. Even if the script crashed, the workflow
   would still reach them: the step is `continue_on_failure`, the next steps are not guarded by
   `previous_success`, `classification_reasons`
   keeps its `[]` seed, and `record-outcome` falls back to `pull request annotation failed`. That
   still gives a durable failed outcome.

### Outcome

When classification fails, `annotation_status=failed`, `ci_status` reflects finalize, and
`pr_details` has the open pull request. `record-outcome.sh` takes its existing
`annotation_status != passed` branch: `outcome=failed`, `pr`, `validator.status=passed` (then
qualified by task-compliance), `ci.status`, and `branch`. It uses the explicit reasons, and its counts
input is empty. `verify-feature-outcome.py` accepts it, since a failed outcome must have
reasons and it does. Factory reporting then posts the reasons on the issue, links the pull request,
and moves the card to Review with `Verdict=failed`. No reporting code changes.

On success, `classification_reasons` stays `[]`. `record-outcome.sh` treats an empty list as no
reasons, so every existing branch keeps its default reasons.

### Data flow on success

There is no change except timing. `classify` now sees finalize's `[fix-pr]` commits and lists them
in `later_commits`. `verify-classification` then requires an orange item that names each one with
`git diff --shortstat` wording and a `Tests:` sentence. `annotate-pr` still adds any commit the
classification missed. For example, a classifier that lists `later_commits` without them passes
the check, and the annotation then adds them.

## Decisions

1. **Status capture, not a combined gate script.** Reuse the workflow's existing seed / step /
   mark-failed pattern, as `validator_status`, `ci_status`, and `annotation_status` do. This keeps
   the repair on `verify-classification` and its loop semantics. A single script that ran the
   verifier itself would lose the Runner-managed repair. *Alternative rejected:* fold the
   verification into `record-classification-failure.py`.
2. **A classifying session that fails is a classification failure, even if it left a valid file.**
   This is conservative and matches the spec's session-failure scenario. The file is kept as
   rejected evidence. *Alternative rejected:* trust any file that passes the verifier. A crashed or
   timed-out session may have written a partial but structurally valid classification.
3. **Pass a captured counts path, cleared on failure, instead of relying on moving the file or
   teaching `record-outcome.sh` to ignore bad counts.** `record-outcome.sh` already skips an empty
   input and needs no change. It keeps failing loudly on malformed counts from any path that still
   passes them. Moving the file aside only preserves evidence, on a best-effort basis. Approach
   review AR-001 showed that making the outcome depend on the move recreates the technical failure
   when the move fails. *Alternatives rejected:* tolerate malformed counts whenever the outcome is
   `failed`, which would hide real defects; delete the file when the move fails, which destroys
   evidence and still fails if the deletion fails.
4. **Pass reasons explicitly and include the CI reason.** This uses the existing `reasons` input.
   The CI reason is added in the script because explicit reasons replace the default ones.
5. **Keep the classify prompt as it is.** The issue asks for no prompt rule. The fix is the step
   order.
6. **Keep the comment above `seed-verify-status`.** It explains why that step is unguarded:
   classify's `skip_if` reads `validator_status`. That is still true.

## Risks / Trade-offs

- **The PR is ready before it is annotated.** Between finalize and `annotate-pr`, a reviewer may
  see a ready PR without its attention section. On a classification failure that state persists,
  and the issue comment and `Verdict=failed` report it, as the spec accepts. Exhausting the attempt
  limit during classification still ends technically with no outcome. That is the same exposure
  any late step has today.
- **`previous_success` after skipped top-level steps.** The mark steps depend on the Runner's
  documented rule that a skipped top-level step does not reset the preceding outcome. The existing
  `mark-ci-failed` relies on the same rule. Tests must exercise the paths where a skipped step sits
  right before a mark step: a definition stop, a validator failure, and a classify failure that
  skips the verifier.
- **CI does not run Agent Runner.** The Runner-semantics tests run only where a Runner is
  configured. Provisioning a pinned Agent Runner build in this repository's CI would add a
  cross-repository build dependency and access requirements. That belongs in a separate decision,
  so this change makes the local run with `FEATURE_REQUIRE_RUNNER=1` a recorded acceptance
  obligation instead.
- **Classification now runs later**, and it may meet a larger set of later commits, which raises
  the chance that `verify-classification` rejects it. The repair handles this, and the failure is
  now a durable failed outcome rather than a technical failure.
- **A resume that skips finalize** (`factory-resume-skip.sh … finalize`) still classifies and
  annotates the existing state, as it does today.

## Verification

`test-plan.md` holds the full obligations. In summary:

- Static assertions on the workflow YAML always run, with no Runner-based skip. They cover the step
  order, `continue_on_failure`, the retained repair, the skip guards, the
  `classification_counts` and `reasons` wiring, and that the new script is staged and executable.
- Real Runner validation, and a real Runner execution of the workflow's closing steps, run with a
  stub `claude` CLI and a project agent profile. The steps run are `seed-ci-status` through
  `verify-outcome`, with the real `classify` session step and the real inline repair. CI does not
  provision Agent Runner, so these tests skip when no Runner is configured. With
  `FEATURE_REQUIRE_RUNNER=1` set, a missing or incompatible Runner fails instead of skipping.
  Acceptance must run them that way and record the result. Runner suitability is probed with a
  known-good fixture workflow, never with the workflow under review.
- Script-composition tests cover the failure script, `record-outcome.sh`, and
  `verify-feature-outcome.py`, including a fault-injected failure of the move.
- Run `openspec validate --specs --strict` and the existing suite.

## Migration Plan

This is a workflow and script change in the factory release. It takes effect for attempts launched
after deploy, and running claims keep their frozen release. There is no data migration. A rollback
restores the earlier order. Attempts already past finalize on the new release finish on it.
