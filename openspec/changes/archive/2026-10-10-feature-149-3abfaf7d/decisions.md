# Decisions

## D1 (propose): Verdict go with caveats

- Decision: Go. The defect is real (PR #146 published a false "not pushed, CI not run" signal), the fix is a step reorder, and Paul settled the direction.
- Caveat: classification now runs after the pull request is ready, so a classification failure must not leave an unannotated ready PR that intake would settle as handed off.
- Alternatives: no-go (rejected, since the reviewer-facing signal is wrong); a prompt rule (rejected by the issue).
- Decision-bearing: no (follows the issue's settled Option A).

## D2 (propose): Run classify and verify-classification after finalize, before annotate-pr

- Decision: Order the steps verification → task-compliance gate → finalize → classify → verify-classification → annotate-pr → record-outcome.
- Alternatives: have annotate-pr state the head and ci_status itself (Option B, not chosen by Paul); a prompt rule (rejected in the issue).
- Decision-bearing: no (settled by the issue).

## D3 (propose): Classify even when finalization ends with CI red

- Decision: Keep classify's current guards (definition stop, validator status). Do not add a CI-status guard. The PR is annotated and the outcome stays `failed` with CI reasons.
- Alternatives: skip classification on red CI (the PR would lose its attention section exactly when it matters most).
- Decision-bearing: no (keeps current behavior, where annotation runs regardless of CI).

## D4 (propose): Keep the post-finalization later-commit step in annotate-pr as a safety net

- Decision: Keep `mark-later-commits` / later_commits merging unchanged. Review rounds and any commits the classifier did not see still depend on it.
- Alternatives: remove it now that the classifier sees finalization commits (it would break review rounds).
- Decision-bearing: no.

## D5 (propose, superseded by D7): Leave the handling of a classification failure after finalization to design, with a leading option

- Decision: The proposal names the risk and recommends routing a classify or verify-classification failure after finalization through the existing `annotation_status = failed` path, so a durable outcome reports it. Design makes the final call.
- Alternatives: keep it a technical failure (the retry would see a non-draft PR and settle as handed off, leaving it unannotated); mark the PR ready only after annotation (that would require changing Runner's generic finalize-pr, which is outside this repository).
- Decision-bearing: yes (it changes the failure semantics of the attempt).

## D6 (propose): Scope limited to the feature workflow

- Decision: Do not change the fix and task workflows. They have no classify step before finalize.
- Alternatives: audit and change all PR workflows (no defect evidence, and outside the issue).
- Decision-bearing: no.

## D7 (proposal review, PR-001): Applied. Settle the failure contract for classification after finalization

- Finding: Marking annotation failed is not enough. `record-outcome.sh` exits on a malformed or non-object `review_attention_counts` before it writes the outcome, so a partial file followed by a failed repair still ends technically with no outcome. Reconciliation then settles the non-draft PR as handed off.
- Decision: Applied; this supersedes the open choice in D5. `classify` and `verify-classification` run with `continue_on_failure` and capture a classification status. On failure, the rejected file is moved aside and kept as evidence, `annotate-pr` is skipped, and `annotation_status` is set to `failed`. `record-outcome` gets no counts file (a missing file is already tolerated on a failed outcome) and gets explicit `reasons` naming the classification failure, plus the CI failure when CI also failed. Tests cover a missing file, truncated JSON, an invalid structure, a failed repair, and a simultaneous CI failure, and check that the outcome is recorded and verified.
- Alternatives: keep a technical failure (the retry settles the PR as handed off and loses the error); have record-outcome tolerate malformed counts on failure (that would hide malformed data generally rather than handle this path); mark the PR ready only after annotation (that would require changing Runner's generic finalize-pr, which is outside this repository).
- Decision-bearing: yes (failure semantics of a late attempt step).

## D8 (proposal review, PR-001): A ready PR without annotation is an accepted failed hand-off, with no PR fallback notice

- Decision: The failed outcome's issue comment, which `factory-feature-reporting` already posts, is the visible signal. The workflow posts no extra notice on the pull request.
- Alternatives: post a fallback notice in the PR description (that needs a new annotation path that runs exactly when annotation inputs are broken, which adds surface for a rare failure).
- Decision-bearing: yes (a reviewer opening the PR directly sees no attention section).

## D9 (spec): Modify the two existing requirements and add one for the classification-failure contract

- Decision: Rewrite "Classify review attention without blocking" (classification now runs after finalization, sees the post-finalization state, and covers finalization's commits) and "Finalize the feature pull request" (it follows verification and the task-compliance gate, and returns `pull-request` only when classification and annotation also succeed). Add a new requirement, "Record a durable outcome when classification fails after finalization", for the D7/D8 contract.
- Alternatives: fold the failure contract into the classification requirement (rejected: that requirement is already long, and a separate requirement keeps the failure scenarios testable and traceable).
- Decision-bearing: no (follows the issue, D7, and D8).

## D10 (spec): State the "after finalization" behavior observably, without a prompt rule

- Decision: The spec requires that classification observe the branch, PR, and CI as finalization left them, and adds a scenario stating that the description does not describe finalized commits as unpushed or lacking a CI run. The guarantee comes from step order, not from a rule about wording.
- Alternatives: a requirement that forbids push or CI remarks in classification (rejected by the issue).
- Decision-bearing: no.

## D11 (spec): No delta for factory-feature-reporting

- Decision: The existing `failed` handling (reasons posted, PR linked, card moved to Review with `Verdict=failed`) already covers the classification-failure report. The later-commit annotation and the "A commit after classification" scenario remain accurate.
- Alternatives: add a reporting scenario for classification failure (it would duplicate the execution requirement's issue-comment scenario).
- Decision-bearing: no.

## D12 (spec): The task-compliance gate requirement is unchanged

- Decision: "Gate the feature on a bound task-compliance verdict" still runs before classification (and now also before finalization). Its rule that finalization commits are not reviewed again is unchanged, so it gets no delta.
- Alternatives: none needed.
- Decision-bearing: no.

## D13 (design): Capture classification status with the workflow's existing seed / step / mark pattern

- Decision: Add `seed-classification-status`, then `classify` (`continue_on_failure`), then `mark-classify-failed`, then `verify-classification` (`continue_on_failure`, repair kept, skipped unless the status is passed), then `mark-classification-invalid`. A new `record-classification-failure.py` step runs when the status is not `passed`.
- Alternatives: one gate script that runs the verifier itself (rejected: it would lose the Runner-managed repair).
- Decision-bearing: no.

## D14 (design): A failed classifying session counts as a classification failure even if it left a valid file

- Decision: Treat it conservatively. The file is kept as rejected evidence and the PR is not annotated from it.
- Alternatives: trust any file that passes the verifier (a crashed session may leave a partial but valid classification).
- Decision-bearing: no (matches the spec scenario for a session failure).

## D15 (design, delete fallback superseded by D20): Move the rejected file aside rather than change record-outcome.sh

- Decision: Rename it to `review-attention.rejected.json`. `record-outcome.sh` then sees no counts file, which it already tolerates on a failed outcome, and it keeps failing loudly on malformed counts from any other path. If the rename fails, delete the file as a last resort and say so in the reason, so the outcome stays durable.
- Alternatives: make record-outcome tolerate malformed counts on failure (that would hide defects).
- Decision-bearing: no.

## D16 (design): Pass explicit reasons and add the CI reason in the script

- Decision: `record-outcome` receives `reasons: {{classification_reasons}}`, seeded `[]`. An empty list keeps every existing default. The script appends `CI did not pass within its fix cycle` when `ci_status` is failed, because explicit reasons replace the defaults.
- Alternatives: change record-outcome to merge its reasons (a broader change to a shared script).
- Decision-bearing: no.

## D17 (design): Seed annotation_status from classification_status

- Decision: `seed-annotation-status` prints `failed` when classification failed. Otherwise the skipped `annotate-pr` would leave the seeded `passed` in place, because `mark-annotation-failed` uses `previous_success`.
- Alternatives: a separate mark step after the skipped annotate (it depends on fragile previous-step semantics).
- Decision-bearing: no.

## D18 (test-plan): Three integration obligations and no new E2E

- Decision:
  - INT-001: staged catalog validation, plus step-order and flag assertions.
  - INT-002: the real Runner executes the workflow's tail steps, with the side-effecting steps replaced by commands, to prove the `previous_success` and `continue_on_failure` status paths.
  - INT-003: the failure script, record-outcome, and outcome verification composed over every bad-classification shape, plus a regression check that an empty reasons list changes nothing.
  - No new E2E: the feature-cycle E2E uses a substitute Runner, and a real workflow run needs live models, GitHub, and CI.
- Alternatives: rely on static YAML assertions only (rejected: they cannot prove the Runner's skip semantics, the design's main risk); a live feature-workflow E2E (rejected: it needs external services, costs money, and would be flaky).
- Decision-bearing: no.

## D19 (test-plan): The acceptance envelope is local only

- Decision: Acceptance may stage catalogs, run the installed Runner on workflow fragments with substitute steps, and run scripts in temporary directories. It must not touch the live factory, GitHub PRs and issues, Fly, Docker, or Paul's checkout. No human-only tests.
- Alternatives: authorize a real throwaway feature PR (rejected: it has external effects and needs a live model run, and the integration tests cover the boundary).
- Decision-bearing: no.

## D20 (approach review, AR-001): Applied. Decouple counts from evidence cleanup

- Finding: The outcome still depended on moving or deleting the rejected file. If both failed, record-outcome would parse the malformed file and exit 2 with no outcome.
- Decision: Applied. Add `seed-classification-counts` (the path) and `clear-classification-counts` (empty on failure). `record-outcome` takes `review_attention_counts: {{classification_counts}}`, and an empty input is already skipped. The move to `review-attention.rejected.json` is best effort. Nothing is deleted, and a failed move is named in the reason. `record-classification-failure` is `continue_on_failure` and writes its JSON in one final write. This supersedes the delete fallback in D15. INT-003 gains a fault-injected rename failure.
- Alternatives: keep the delete fallback (rejected: it destroys evidence and still fails if the deletion fails); make record-outcome tolerate bad counts (rejected in D15).
- Decision-bearing: no.

## D21 (approach review, AR-002): Applied. Static checks always run, and Runner checks are a required local run

- Finding: The Runner-gated tests skip in CI (no Runner is provisioned), and the suitability probe validates the workflow under review, so a regression becomes a skip.
- Decision: Applied in part.
  - Step-order, flag, guard, wiring, and staging assertions move to an unconditional static test (INT-001).
  - Runner suitability is probed with a known-good fixture workflow.
  - With `FEATURE_REQUIRE_RUNNER=1`, a missing or incompatible Runner fails instead of skipping.
  - Acceptance must run INT-001 and INT-002 that way and record the output as a completion obligation.
  - CI provisioning of Agent Runner is not added.
- Rejected part: provisioning a pinned Agent Runner in this repository's CI. It adds a cross-repository build dependency and access requirements for CI. That is a separate infrastructure decision beyond this feature's scope, and the finding's own fallback (a required, recorded local run) is adopted instead.
- Decision-bearing: yes (CI still does not prove the Runner semantics, so proof relies on the recorded acceptance run).

## D22 (approach review, AR-003): Applied. INT-002 exercises the real repair and the CI-red-with-valid-classification path

- Finding: INT-002 removed the repair and had no case with CI red and a valid classification, so its stated coverage was not actually exercised.
- Decision: Applied. INT-002 keeps the real `classify` session and the inline repair, driven by a stub `claude` CLI through a project agent profile (the Runner's own stub pattern). It adds cases (d), where the repair fixes the classification and reaches annotation, and (e), where finalize fails with a valid classification: classification and annotation run, counts are kept, and the outcome is `failed` with the CI reason. Case (c) now covers a repair that leaves the file invalid. The coverage map cites each case.
- Alternatives: keep command stand-ins for the session steps (rejected: they cannot prove the repair boundary).
- Decision-bearing: no.

## D23 (tasks): One implementation task covering the workflow, the script, staging, tests, and the acceptance run

- Decision: `tasks.md` has a single task with scope items for the workflow reorder, the new script, staging, INT-001 to INT-003, the required `FEATURE_REQUIRE_RUNNER=1` acceptance run, and an orange PR item about the CI gap (D21). It names D20 to D22 as overriding the earlier design and test-plan text.
- Alternatives: split it into several tasks (the step instruction requires exactly one).
- Decision-bearing: no.
