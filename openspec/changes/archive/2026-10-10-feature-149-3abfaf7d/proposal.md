## Why

A feature pull request's "Review first" section is the reviewer's main signal for where to look. On
PR #146 (claim a8f41c2c) its orange item about commits after acceptance said the final commits were
"not pushed yet" and that "CI has not run on them". By the time the description was published, the
PR head was already 36ab05e and CI `check` had passed on it. The reviewer was told the head was
unpushed and untested when it was pushed and green.

The cause is the order of steps in `factory-feature-v1.0.yaml`. `classify` writes
`review-attention.json` before `finalize` pushes the branch, marks the pull request ready, waits for
CI, and adds any CI-fix commits. `annotate-pr` then publishes that file unchanged. Anything the
classifier saw about the branch's push or CI state is stale by the time a reviewer reads it. The
prompt never asked for push or CI state. The lead added it because that was the branch state at the
time. So a prompt rule against one sentence would leave the stale snapshot in place, and any other
statement about branch state could go stale the same way.

Paul settled the direction on 2026-10-10 (Option A in the issue): run classification and its
verification after finalization, and update the `factory-feature-execution` specification to match.

## What Changes

- Move the feature workflow's `classify` step and its deterministic `verify-classification` check
  (with its repair) after `finalize`, and keep them before `annotate-pr`. The classifier then sees
  the branch as finalization left it: pushed, the pull request ready, CI settled (passed or failed
  after the bounded loop), and every CI-fix commit already on the branch.
- Commits that finalization adds become ordinary "commits after acceptance". The classification's
  orange item names them, states their diff size, and has a `Tests:` sentence, like any other later
  commit. The annotation's existing step that adds later commits stays as a safety net. It still
  names commits the classification did not cover and marks items that a later commit may have
  fixed, which review rounds rely on.
- Classification still runs, and the pull request is still annotated, when finalization ends with
  CI red. The attempt's outcome stays `failed` with the CI reasons, as it is today.
- Define what happens when classification fails after finalization. This covers a failed classify
  session, an unrepaired `verify-classification`, and a missing, truncated, or malformed
  `review-attention.json`. The attempt keeps the rejected file as evidence and does not annotate
  the pull request from it. It still records a durable `failed` outcome with the pull request
  reference, `ci_status`, and a reason specific to classification. It does not parse or report tier
  counts from the unverified file. When CI also failed, the CI failure is still reported.
- Update `factory-feature-execution`: "Classify review attention without blocking" says
  classification happens after finalization instead of before. "Finalize the feature pull request"
  no longer says finalization follows classification. The orange-tier wording about commits "the
  finalization loop adds after classification" changes to match.
- Update the workflow catalog and script tests that pin the step order.

No public interface or persisted format changes. `review-attention.json` keeps its schema, the
outcome keeps its fields, and the classify prompt gets no new rule about push or CI state.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-feature-execution`: in "Classify review attention without blocking", classification
  moves from before finalization to after it, the orange item for later commits covers the commits
  finalization adds, and the post-finalization later-commit addition is kept as a safety net for
  commits the classification did not see. In "Finalize the feature pull request", finalization no
  longer follows classification, and the workflow annotates after it. A new failure contract
  covers classification failing after finalization: the pull request is not annotated from the
  rejected classification, and the attempt records a durable `failed` outcome with the pull request
  reference, a reason specific to classification, and no tier counts.

`factory-feature-reporting` needs no requirement change. The description still comes from the final
classification and is still updated with later commits. Its scenario titled "A commit after
classification" is still accurate.

## Technical Approach

This is a step reorder inside one Runner workflow file the factory ships, plus spec and test
updates. The steps are now verification → task-compliance gate → finalize → classify →
verify-classification → annotate → record outcome. Today they are verification → task-compliance
gate → classify → verify-classification → finalize → annotate → record outcome. `classify` keeps its
current guards: it is skipped after a definition stop or when the validator did not pass. Its
inputs stay the same; they just reflect a later, settled branch state. The task-compliance gate
still runs before finalization. Commits after its last verdict are still reported as uncovered, as
the spec already requires.

The main structural risk is the new failure window. Finalization marks the pull request ready before
anything has been classified. Today, a failed classify session or an unrepaired
`verify-classification` stops the attempt while the pull request is still a draft, so a retry
resumes it. After the reorder, the same failure would leave a ready pull request with no
annotation. `factory-feature-intake` settles a claim with a non-draft factory pull request as handed
off, so a retry would not repair it. Recovery would lose the classification error unless the
attempt records it itself.

Simply continuing past the classification steps and marking annotation failed is not enough.
`record-outcome.sh` still receives `review-attention.json` as `review_attention_counts`. It exits on
malformed JSON, or on a value that is not an object, before it writes the outcome. A partial file
followed by a failed repair would therefore still end technically, with no outcome. The proposal
settles the failure contract as follows:

- `classify` and `verify-classification` run with `continue_on_failure`, and their result is
  captured as a classification status.
- On failure, the workflow moves the rejected file aside, keeping it in the attempt's evidence
  (for example as `review-attention.rejected.json`). It skips `annotate-pr` and sets
  `annotation_status` to `failed`.
- `record-outcome` then gets no `review_attention_counts` file. It already tolerates a missing
  file on a failed outcome. It does receive an explicit `reasons` list naming the classification
  failure, plus the CI failure when `ci_status` is `failed`. The `failed` outcome carries the pull
  request reference and CI status, and `verify-feature-outcome` accepts it.

Classification failure is an accepted failed hand-off. The ready pull request keeps the
description finalization left, without the attention section. The failed-attempt comment that
`factory-feature-reporting` already posts on the issue makes the failure visible and names its
reason. The workflow posts no separate fallback notice on the pull request.

Tests must cover a missing file, truncated JSON, an invalid structure, a failed repair, and
classification failing while CI also fails. Each must show that the outcome is recorded and
verified. Whether a script change is needed beyond the workflow file is a design detail; the
expected change is limited to passing inputs. An attempt limit that expires during classification
remains a residual technical-failure risk. It is the same risk as an expiry during any late step
today.

Rejected alternatives:

- Have `annotate-pr` state the pushed head and `ci_status` itself, keeping classification before
  finalization. The workflow would own push and CI state, but CI-fix commits would still be
  classified only by the safety net, and the classifier's other branch-state remarks could still
  go stale. Paul chose Option A.
- A prompt rule that forbids push or CI state in classification. The issue rejects this, and it
  fixes one sentence rather than the stale snapshot.

## Out of Scope

- The fix and task workflows. They have no classification step before finalization, and the issue
  covers only the feature workflow.
- Changes to the classify prompt's tier rules, the `review-attention.json` schema, or the outcome
  format.
- Review rounds on feature pull requests. They do not re-run classification, and their description
  handling is unchanged.
- Re-running task-compliance on commits that finalization adds.
- Agent Runner's generic `finalize-pr` workflow.

## Impact

- `src/agent_factory/work_kinds/pull_request/workflow/factory-feature-v1.0.yaml`: the step order;
  the classification status, the evidence it preserves, and the annotation skip on failure; and the
  `reasons` and `review_attention_counts` inputs it passes to `record-outcome`.
- `src/agent_factory/work_kinds/pull_request/workflow/record-outcome.sh` and
  `verify-feature-outcome.py`, only if design finds that the failure path needs script support.
- `openspec/specs/factory-feature-execution/spec.md`, through this change's spec delta.
- `tests/integration/test_feature_workflow_catalog.py` and
  `tests/integration/test_feature_workflow_scripts.py`, where they assert the step order or the
  pre-finalization classification.
- Reviewers of feature pull requests: attention items describe the pushed, CI-settled head.
  Finalization's CI-fix commits get a full classification instead of only the safety-net entry.
- Running claims keep their frozen release. Only attempts launched after deploy use the new order.
