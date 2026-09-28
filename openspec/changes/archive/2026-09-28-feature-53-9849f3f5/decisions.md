# Decisions: feature-53-9849f3f5

## D1 (proposal): Verdict go
- **Decision:** Go. Merging the current base into the claim's branch on every feature resume removes repeated stops and discarded work.
- **Alternatives:** Keep the manual "commit the fix to this branch" workaround; re-admit as a new claim; rebase instead of merge.
- **Decision-bearing:** No. The issue records Paul's decision (2026-09-28).

## D2 (proposal): "Default branch" means the claim's configured target branch
- **Decision:** Resolve the head of the claim's frozen `target.branch` (the PR base, `main` here) from the refreshed mirror, with the existing `fetch_mirror` and `resolve_mirror`.
- **Alternatives:** Query GitHub for the repository's default branch.
- **Decision-bearing:** Yes. The two are the same for every configured target today, but the target branch is the one the PR merges into, so it is the one whose drift matters.

## D3 (proposal): Scope limited to feature claims, including feature review rounds
- **Decision:** Apply the merge to feature resumes, recoveries, prior-branch continuations, and review rounds on feature PRs. Fix attempts and fix-PR review rounds are unchanged.
- **Alternatives:** Also merge on fix-PR review rounds, since the review workflow is shared.
- **Decision-bearing:** Yes. The issue title scopes this to feature branches, and its spec and code references are all feature-specific. The body's "review rounds" is read as feature review rounds.

## D4 (proposal): Checkpoints are read along first-parent history
- **Decision:** `feature_checkpoint` uses `git log --first-parent`, so checkpoint trailers from other features' merged commits on `main` never count as this claim's progress.
- **Alternatives:** Exclude the newly merged base in addition to the admission target; filter trailers by change name.
- **Decision-bearing:** Yes. Without this, merging `main` breaks the acceptance criterion that resume points do not change. Excluding each merged base would require recording all of them, and filtering by change name fails for prior-branch continuations that rename the change.

## D5 (proposal): A merge-conflict stop keeps the original resume point
- **Decision:** When the resolution step cannot resolve the conflicts, the `needs-input` outcome records `stopped_step` as the resume point the attempt was heading to. The next resume repeats the merge and continues there.
- **Alternatives:** Add a new `merge` step name to the resume order.
- **Decision-bearing:** Yes. It keeps `feature_resume_point` and `factory-resume-skip.sh` unchanged, as the issue's acceptance criterion requires.

## D6 (proposal): Re-verification only when the merge added commits
- **Decision:** When the merge advanced the branch, a resume point of `finalize` is lowered to `verify`. Resumes at or before `archive` already verify. Feature review rounds run the validator on the merged tree before pushing.
- **Alternatives:** Always rerun verification, including acceptance, on every resume.
- **Decision-bearing:** Yes. Rerunning full acceptance when nothing new was merged costs time without adding confidence.

## D7 (proposal): Merge commits, not rebases, and no checkpoint trailer on them
- **Decision:** Use `git merge --no-ff --no-edit`, skip it when the base is already an ancestor, and never add a `Factory-Checkpoint` trailer to merge or resolution commits.
- **Alternatives:** Rebase the branch onto the new base.
- **Decision-bearing:** No. Rebasing rewrites pushed history that the draft PR, checkpoints, and `continuation_head` depend on.

## D8 (proposal): Provenance fields are additive
- **Decision:** Record `target_at_admission` and `base_merged` in the claim's preparation and the attempt's provenance. Add an optional `base_head` workflow parameter with an empty default, and keep contract versions `factory-feature/1` and `factory-review/1`.
- **Alternatives:** Overwrite `revisions.target` with the new base; bump the contract versions.
- **Decision-bearing:** No. Frozen revisions stay reproducible, and no persisted format changes shape.

## D9 (proposal): Reword the archive-block summary from PR #51
- **Decision:** Replace "a fix merged to main does not reach it" with guidance that a fix merged to `main` reaches the claim when it resumes. Update the two assertions in `test_record_archive_block_preserves_explanation_and_branch`.
- **Alternatives:** None. The issue requires this.
- **Decision-bearing:** No.

## D10 (proposal-review, PR-1, structural): Dedicated merge-conflict stop path. Applied
- **Decision:** An unresolved feature conflict goes through a new `record-merge-stop.sh`, not `record-stop.sh`, which would try to commit an unmerged index. The script aborts the merge in the disposable clone only and never pushes the unresolved state. For a prior-branch continuation whose own branch was never pushed, it pushes the prior branch unchanged as the claim's branch, so the next attempt does not fall back to a fresh start. It records the files and the question in the evidence and writes `needs-input` with `stopped_step` set to the intended resume point. A feature review round aborts locally, writes a `needs-input` review outcome, and pushes nothing. The existing review-`needs-input` polling admits the next round after the writer answers.
- **Alternatives:** Route through `record-stop.sh` (fails on the unmerged index); commit the conflict markers (pushes a broken tree); add a new lifecycle state for review merge stops (unnecessary, since review `needs-input` already returns via polling).
- **Decision-bearing:** Yes. It sets the durable branch state and the outcome for conflict stops.

## D11 (proposal-review, PR-2, significant): The prior-change rename becomes its own idempotent step. Applied
- **Decision:** Move the carry-over of the prior claim's unarchived or archived change to this claim's name out of `prepare-branch.sh`'s clean-merge branch into a separate idempotent step. It runs after the merge completes, whether clean or resolved by the agent, and before any step that reads the change. Tests cover conflicting continuations with both change states.
- **Alternatives:** Have the resolution agent perform the rename (not deterministic); rename before merging (changes paths that the merge must then reconcile, which is noisier).
- **Decision-bearing:** No. It keeps existing continuation behavior correct on the new path.

## D12 (proposal-review, PR-3, significant): Merge-only review rounds pass validator and CI. Applied in part
- **Decision (applied):** A feature review round whose merge added commits passes through `factory-implement`'s validate, push, and bounded CI finalization even when triage requested no change, and `implement-result.json` gates the outcome, so failing CI cannot report `pull-request`. The completion comment names the base-merge commit as added after acceptance and not covered by it.
- **Rejected in part:** Recalculating the PR's attention data and description from `accepted_head` to the new head. `factory-feature-reporting`'s scenario "Complete a review round on a feature" requires the round to restore the description it started with and to leave the acceptance evidence naming the accepted commit, and review-change commits are handled the same way today. Changing that would widen this change into the reporting contract. The completion comment carries the post-acceptance disclosure instead, as the finding's alternative allows.
- **Decision-bearing:** Yes. The post-acceptance merge commit is disclosed in the completion comment, not in the PR description's orange tier.

## D13 (specs): A first fresh attempt does not merge
- **Decision:** Only attempts that resume or continue from an existing branch merge the target branch. A claim's first attempt starts from the target head resolved at admission, with no merge.
- **Alternatives:** Also resolve and merge the current head on first attempts.
- **Decision-bearing:** No. The issue scopes the change to resumes, and admission already resolves the head just before launch.

## D14 (specs): The archive-block message offers both routes
- **Decision:** The `REPAIR_BLOCKED` direction summary says the cause can be fixed on the target branch or committed to the claim's branch, and that a comment resumes at archive with the target branch merged in. The spec states this in the requirement and scenario, not as exact wording.
- **Alternatives:** Mention only the target-branch route.
- **Decision-bearing:** No. Committing to the claim's branch still works after this change.

## D15 (specs): A conflict resolved before triage is not pushed when triage stops
- **Decision:** When a feature review round resolves a base conflict but triage then returns `needs-input` (for example, reviewer requests that conflict), the round pushes nothing, including the resolved merge, which keeps the existing rule that a `needs-input` round pushes nothing. The next round merges again.
- **Alternatives:** Push the resolved merge alone before returning `needs-input`.
- **Decision-bearing:** No. It costs at most one repeated resolution and keeps `needs-input` rounds free of side effects.

## D16 (specs): Resume order and checkpoint independence are stated as observable scenarios
- **Decision:** The feature execution spec adds scenarios showing that checkpoints merged in from the target branch never count as the claim's progress, and that the resume order from #46 (needs-input, then draft, then continuing) does not change after a merge commit. It does not name the first-parent mechanism, which stays in design.
- **Alternatives:** Specify `--first-parent` in the spec.
- **Decision-bearing:** No.

## D17 (design): A continuation branch holding nothing of its own is still a continuation
- **Decision:** When a claim's own branch head equals its recorded `continuation_head`, the handler restores `prior_branch` to the previous claim's branch. The attempt after a continuation merge stop therefore still reconciles the artifacts and carries the change over to the new name. The shared head keeps later pushes fast-forward.
- **Alternatives:** Rename before pushing on a merge stop (the pushed branch would no longer be the prior branch unchanged, and the next attempt would still skip reconciliation because `prior_branch` is empty); add a new workflow parameter for reconciliation.
- **Decision-bearing:** No. It preserves the continuation behavior that the specs already require.

## D18 (design): How `base_head` reaches each workflow
- **Decision:** The feature workflow gets an optional `base_head` parameter from the host wrapper. Feature review rounds carry `base_head` in `review.json`, and fix rounds omit it, which is what keeps them unchanged.
- **Alternatives:** A new `factory-review` parameter, which the wrapper would pass only for feature rounds; putting `base_head` in `issue.json` for features.
- **Decision-bearing:** No.

## D19 (design): Merge-only review rounds reuse `factory-implement`
- **Decision:** Add an optional `implement_plan` parameter (default `"true"`) to `factory-implement`. A feature review round with a merged base and no `change` decision passes `"false"`, so it runs only the validator gate, push, CI finalization, and result. `record-review-outcome.sh` maps that result as it does for a change round.
- **Alternatives:** A separate validate/push/CI sequence in `factory-review`; running the implementor with an empty plan.
- **Decision-bearing:** No. Fix callers are unaffected.

## D20 (design): No new outcome fields for merge stops
- **Decision:** Merge-conflict stops use the existing feature and review `needs-input` fields. The conflicting files go in the question text, and the full details go in the `merge-conflict.json` and `base-merge.json` evidence.
- **Alternatives:** Add a `merge` field to the outcome contracts (review outcomes reject extra fields today).
- **Decision-bearing:** No.

## D21 (design): A mechanical check after agent resolution
- **Decision:** `check-merge` fails the attempt as a technical failure when the resolution left `MERGE_HEAD`, unmerged paths, conflict markers, or a dirty tree, or did not include `base_head`.
- **Alternatives:** Trust the agent's commit.
- **Decision-bearing:** No.

## D22 (design): Resolve `base_head` on every prepare, and hold the claim on failure
- **Decision:** `prepare` fetches the mirror and resolves the target branch on every feature attempt. A failure raises `ReadinessError`, which holds the claim without spending retry budget, as admission does.
- **Alternatives:** Fall back to the frozen target when the fetch fails (silently reintroduces the stale-base problem).
- **Decision-bearing:** No.

## D23 (test-plan): Real git in integration tests, simulated agent resolution
- **Decision:** Integration tests run the packaged scripts against real temporary repositories with bare `origin` and mirror remotes. The `resolve-merge` agent is simulated by a scripted git resolution or a hand-written `merge-stop.json`, and no automated test or acceptance run starts real model sessions.
- **Alternatives:** Mock git; run the real `factory-feature` workflow with models.
- **Decision-bearing:** Yes. The live quality of conflict resolution is an accepted, untested limitation, mitigated by the validator, `check-merge`, and re-verification.

## D24 (test-plan): One controller E2E through the stub-Runner harness
- **Decision:** Extend `tests/e2e/test_feature_cycle.py` with a stop, a `main` advance, and a resume, asserting `base_head` in the Runner args, the provenance, and the unchanged frozen revisions. Merge behavior itself is proven at the integration layer.
- **Alternatives:** No E2E test; a full workflow E2E with a real Runner.
- **Decision-bearing:** No.

## D25 (test-plan): Acceptance envelope is local-only
- **Decision:** Acceptance may use local temporary repositories, the test suites, `agent-validator`, and a read-only `doctor`. It must not touch GitHub, the live factory state, releases, deploys, or model sessions.
- **Alternatives:** Allow a sandbox GitHub repository for real pushes.
- **Decision-bearing:** No. Every behavior is observable against local bare remotes.

## D26 (approach-review, AR-1, high): `check-merge` proves both histories and the branch's non-conflicting work survive. Applied
- **Decision:** `check-merge` becomes a shared `check-merge.sh`. In addition to the existing checks for an unfinished merge, unmerged paths, conflict markers, a dirty tree, and a HEAD missing `base_head`, it requires:
  - `pre_merge_head` and `base_head` are both ancestors of HEAD, and `pre_merge_head` is on HEAD's first-parent chain;
  - the resolution is a merge commit whose parents are exactly `pre_merge_head` then `base_head`;
  - `git merge-tree --write-tree` shows that the resolution differs from the automatic merge only in conflicted paths.
  Follow-up commits after the resolution are allowed and recorded. The feature spec gains the rule and a scenario for rejecting a resolution that discards work. INT-005 adds real-git rejection cases for a reset to `base_head`, wrong parents, a lost first-parent chain, and a changed or deleted non-conflicted file.
- **Alternatives:** Ancestry checks only (would miss a resolution that keeps both parents but checks out one side's tree); require HEAD to be the resolution with no follow-ups (would block legitimate fixes for the validator).
- **Decision-bearing:** Yes. It strengthens the guarantee that conflicts never discard the claim's work.

## D27 (approach-review, AR-2, medium): Record the resolved merge SHA for the completion comment. Applied
- **Decision:** After a conflict, `check-merge.sh` rewrites `base-merge.json` with `status: resolved` and the resolution's `merge_commit`, so both clean and resolved merges carry the SHA. The review `respond` step names that SHA only after confirming it is on `origin/<branch>`, and otherwise says the merge was not pushed. The review spec and INT-008 cover both clean and resolved rounds.
- **Alternatives:** Have `respond` discover the merge from `git log` (ambiguous with follow-up commits).
- **Decision-bearing:** No.

## D28 (approach-review, AR-3, medium): The review-round reporting exception is made explicit rather than changed. Applied in part
- **Decision (applied):** Add a `factory-feature-reporting` MODIFIED delta. It states that commits a review round pushes, including a base merge, are not added to the restored description's orange items, and that the completion comment names each such merge commit, linked, as added after acceptance, with a link to the acceptance evidence. A new scenario covers this, and the proposal lists the capability.
- **Rejected in part:** Updating the PR description's attention section after a review merge. The current reporting requirement already requires a review round to restore the description it started with, and review-change commits are treated the same way today. Review rounds also do not have the original attempt's classification inputs (`review-attention.json` and acceptance evidence live in the feature attempt's artifact directory). Rebuilding the description would add a cross-attempt evidence dependency and change existing review reporting beyond this issue.
- **Decision-bearing:** Yes. Reviewers see a base merge from a review round in the completion comment, not in the description's orange tier.

## D29 (write-tasks): One implementation task covering the whole change
- **Decision:** `tasks.md` holds a single task that covers the controller, checkpoint reading, the new and changed workflow scripts, the feature, review, and implement workflow YAML, the archive-block wording, the catalog lists (`FEATURE_STAGED_FILES` and `REVIEW_WORKFLOW_SCRIPTS`), the docs, and every test-plan obligation. Its done-when criteria include unchanged fix-kind behavior and no fresh start on a conflict.
- **Alternatives:** Split into several tasks. The factory workflow requires exactly one planned task.
- **Decision-bearing:** No.
