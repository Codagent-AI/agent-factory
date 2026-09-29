## Why

A feature claim freezes its target commit at admission, and every later attempt on that claim
works against that commit. When a claim stops and a human fixes the cause on `main`, the fix never
reaches the claim:

- **The same stop repeats.** #15 stopped at `archive` because of a problem in a living spec on
  `main`. #38 fixed it on `main`, but unblocking #15 would stop again. `prepare-branch.sh` resumes a
  claim's own branch from `origin/<branch>` and never merges `main` (the `elif [ -n "$resume" ]`
  path). PR #51 went as far as telling the operator so in the archive-block `direction_summary`
  ("a fix merged to main does not reach it"). That wording is an admission of the gap, not a way
  to work around it.
- **Conflicts discard work.** A prior-branch continuation merges the recorded target commit. When
  that merge conflicts, the script runs `git merge --abort` and falls back to a fresh branch ("prior
  branch could not merge the recorded target commit"). That throws away a committed plan and its
  implementation, often hours of agent time, over a conflict an agent could usually resolve.
- **Drift compounds.** The longer a claim's branch goes without `main`, the bigger the conflicts
  when its pull request finally merges. A human then resolves them by hand, outside the factory's
  validation.

Paul settled the direction on 2026-09-28: every resume merges the current default branch into the
claim's branch, an agent resolves any conflicts, and conflicts no longer cause a fresh start. It
matters now because blocked and continued feature claims are becoming common (#15, #45, #46), and
each one currently needs a human to hand-carry fixes onto the branch.

**Verdict: go.** The change is small in surface: one branch-preparation script, one new agent step,
a resume-point adjustment, and checkpoint reading. It removes a class of repeated stops and
discarded work. The alternatives are worse:

- *Tell the operator to commit fixes to the branch*: this is today's workaround. It is manual and
  error-prone, and it leaves the branch drifting.
- *Re-admit the issue as a new claim*: it still merges only the new target, it still aborts on
  conflict, and it loses the claim's stop context.
- *Rebase instead of merge*: it rewrites pushed history, which breaks checkpoint trailers, the open
  draft PR, and `continuation_head` exclusions.

The main cost is one more agent step that can itself stop. That is acceptable because it stops with
`needs-input` and a file list rather than losing work.

## What Changes

- At every feature resume, the factory fetches the target mirror and resolves the head of the
  claim's target branch (the pull request's base, `main` for this repository). It records that head
  as the attempt's merged base, next to the target frozen at admission. Target, Runner, and Skills
  revisions stay frozen for reproducibility. Only the claim branch's base moves forward.
- Every feature resume path merges that head into the claim's branch before the resumed step:
  same-claim resumes after a stop, draft and technical-recovery resumes, prior-branch
  continuations, and review rounds on a feature pull request.
  - A clean merge is committed with an ordinary merge commit that carries no `Factory-Checkpoint`
    trailer. A base that is already an ancestor of the branch produces no commit.
  - A conflicting merge is left in place, and a new merge-resolution agent step runs before the
    resumed step. It resolves the conflicts while keeping the intent of both sides, runs the
    repository's validator on the result, and commits. When it cannot resolve the conflicts
    confidently, the attempt stops with `needs-input`, lists the conflicting files and the decision
    it needs, and keeps the branch's work. It never aborts to a fresh branch. This stop has its own
    path: the unresolved index is never committed or pushed, and the pushed branch stays as it was
    before the merge.
- On a prior-branch continuation, carrying the prior claim's OpenSpec change over to this claim's
  change name runs after the merge is complete, whether the merge was clean or resolved by the
  agent.
- The fresh-start fallback remains only for a branch that no longer exists. "Unresolvable merge"
  is removed as a fallback reason.
- When the merge brought in new commits, the attempt must not trust earlier validation. A resume
  point that would skip verification (`finalize`) moves back to `verify`. Resumes at or before
  `archive` already run verification. A review round that pushes a merge passes the validator and
  a bounded CI wait before it reports success, even when triage requests no code change. Its
  completion comment names the merge commit as added after acceptance and not covered by it.
- Reading checkpoints follows only the branch's own first-parent history. Checkpoint trailers from
  other features merged into `main` therefore never count as this claim's progress. The resume
  order (needs-input, then draft, then continuing, then checkpoint, from #46) does not change.
- The archive-block `direction_summary` from PR #51 is reworded: a fix merged to `main` now reaches
  the claim when it resumes. The two assertions in
  `test_record_archive_block_preserves_explanation_and_branch` are updated to match.
- The `factory-feature-execution` and `factory-review-execution` specs no longer say that a feature
  continuation merges only "the target's recorded commit", or that an unresolvable merge starts a
  fresh definition.

## Capabilities

### New Capabilities

None. The behavior belongs to existing feature and review execution.

### Modified Capabilities

- `factory-feature-execution`: "Resume and continue feature work". Resumes and continuations merge
  the current target-branch head rather than the admission commit. Conflicts go to a
  merge-resolution step or stop with `needs-input` instead of starting fresh. The fresh-start
  fallback is limited to a missing branch. A resume that brought in new commits re-verifies. The
  admission target and the base merged at each attempt are recorded in the attempt's provenance.
- `factory-review-execution`: a review round on a feature pull request merges the current
  target-branch head into the PR branch before triage and resolves conflicts the same way. An
  unresolved conflict returns the round's `needs-input` and pushes nothing. A pushed merge passes
  the validator and CI before the round succeeds, and the completion comment names it as a
  post-acceptance commit.
- `factory-feature-reporting`: "Annotate the feature pull request by review attention". This makes
  explicit that a review round's pushed base merge is not added to the restored description's
  orange items. The completion comment names the commit and links the acceptance evidence instead.

## Technical Approach

- **Base resolution (controller).** `PullRequestHandler.prepare` and `prepare_review` for the
  feature kind call `fetch_mirror` and `resolve_mirror(repository, target.branch)`. Both already
  exist and are used at admission. The resolved head is passed to the workflow as a new
  `base_head` parameter, beside the frozen `target_head`. It is also written to the claim's
  preparation and the attempt's provenance as `base_merged`, with the frozen target as
  `target_at_admission`. These fields are additive. No stored format changes shape.
- **`prepare-branch.sh`.** After checking out the claim's branch (own branch or prior branch), it
  merges `base_head` with `git merge --no-ff --no-edit`. If the merge is clean, it commits. If the
  merge conflicts, it leaves the index conflicted and writes `merge-conflict.json` (the conflicting
  paths and both heads) to the artifact directory. It never runs `merge --abort` to fall back. The
  fresh-branch fallback runs only when the fetch of the branch fails. The prior-change rename moves
  out of the script's clean-merge branch.
- **Continuation rename step.** A separate, idempotent `continue-change` script step carries the
  prior claim's unarchived or archived change over to this claim's change name. It runs after the
  merge is complete (clean, or resolved by the agent) and before any step that reads
  `openspec/changes/<change_name>`. It does nothing when there is no prior branch or the rename has
  already happened.
- **Merge-resolution step (`factory-feature`).** A new `lead-agent` autonomous step runs right
  after `prepare-branch` and only when `merge-conflict.json` exists. It follows
  `factory-define-rules.md`: it resolves the conflicts, runs the validator, and commits. When it
  cannot resolve the conflicts, it writes `merge-stop.json` with the conflicting files and the
  needed choice, and changes nothing else.
- **Merge-conflict stop (`record-merge-stop.sh`).** This is a dedicated script, not `record-stop.sh`,
  which would try to commit the unmerged index. It runs `git merge --abort` in the attempt's
  disposable clone only, which leaves the branch at its pre-merge head. On a prior-branch
  continuation whose own branch was never pushed, it pushes that pre-merge head, which is the prior
  branch unchanged, to the claim's branch. The next attempt then resumes from the claim's own
  branch instead of falling back to a fresh start. It records the conflicting paths, both heads,
  and the question in the attempt evidence. It writes a `needs-input` feature outcome whose
  `stopped_step` is the resume point the attempt was heading to, and every later step skips on the
  existing outcome. The next resume clones the pushed branch, repeats the merge against the
  then-current base with the human's answer in the eligible comments, and continues where it would
  have. `feature_resume_point` and `factory-resume-skip.sh` keep their current step order.
- **Checkpoint reading.** `PullRequestWorkspace.feature_checkpoint` adds `--first-parent` to its
  `git log`. Without it, the `^base_sha` exclusion no longer covers the branch's history once newer
  `main` commits are merged in. Those commits include other features' `Factory-Checkpoint:
  archived` trailers, which would be misread as this claim's progress.
- **Re-verification.** `prepare-branch.sh` reports whether the merge advanced the branch. The
  workflow lowers an effective resume point of `finalize` to `verify` in that case, and the
  `restore-skipped-verify-status` guard then no longer marks validation as passed.
- **Review rounds.** `factory-review` gets the same merge and resolution steps before triage,
  applied only when `review.json` names the feature kind. Fix review rounds are unchanged.
  - An unresolved conflict aborts the merge in the round's clone. The round writes a
    `review-outcome.json` of `needs-input` that names the conflicting files and the choice needed,
    skips triage, and pushes nothing, as the existing reviewer-conflict `needs-input` does. The
    claim is then blocked by a review round's `needs-input`, which the pull-request lifecycle
    already polls. The writer's next eligible comment admits a new round that repeats the merge
    with that answer in `review.json`. No new lifecycle state is needed.
  - When the merge added commits, the round passes through `factory-implement`'s validate, push,
    and bounded CI finalization, even when triage requested no change. `implement-result.json`
    then gates the outcome as it does for change rounds, so failing CI cannot report
    `pull-request`.
  - The round keeps today's reporting contract: the description the round started with is
    restored, and the acceptance evidence still names its accepted commit. The completion comment
    also names the base-merge commit as added after acceptance and not covered by it.
- **Tests.** The workflow-script integration tests cover:
  - a claim blocked at `archive`, then a fix to the blocking file on `main`, then unblock, after
    which the archive succeeds;
  - a conflicting prior branch, with an unarchived and with an archived prior change, that ends in
    a resolved merge commit with the change carried to the new name, or in `needs-input` with the
    file list and the prior branch pushed unchanged as the claim's branch, never a fresh branch;
  - a feature review round that merges `main` with no requested change, then validates, waits for
    CI, and names the merge in its completion comment, and a conflicting round that returns
    `needs-input` and pushes nothing;
  - `feature_checkpoint` and `feature_resume_point` results that do not change after a merge of
    `main` carrying foreign checkpoint trailers.

## Out of Scope

- **Fix claims.** Fix attempts and fix-PR review rounds keep their current behavior. The issue is
  titled for feature branches, and the fix workflow does not resume from a pushed branch. Extending
  this to fixes can follow separately.
- Changing which Runner, Skills, or Validator revisions a claim uses. Those stay frozen at
  admission.
- Rebasing, squashing, or rewriting pushed branch history.
- Merging `main` into settled claims that are not being resumed, or refreshing open PRs on a
  schedule.
- Re-running acceptance on a review round. The review workflow's rule that acceptance is not re-run
  stays, and the merge is covered by the validator and CI.
- Cleanup of old branches or clones (#15 storage work).

## Impact

- **Code:** `work_kinds/pull_request/handler.py` (resolve and pass `base_head`, record
  provenance), `workspace.py` (`feature_checkpoint` first-parent), and `launch.py` (the new workflow
  parameter and provenance fields). Workflow files: `prepare-branch.sh`, a new
  continuation-rename script, a new `record-merge-stop.sh`, `factory-feature-v1.0.yaml` (the
  resolution, rename, and merge-stop steps and the verify guard), `factory-review-v1.0.yaml` (the
  merge, resolution, and conflict stop before triage for features, and the merge-only
  validate/CI path), a new `record-review-merge-stop.sh` (the conflict outcome),
  `record-review-outcome.sh` (merge-only results), `factory-implement-v1.0.yaml` (an optional
  `implement_plan` parameter), and
  `record-archive-block.sh` (reworded summary).
- **Contracts:** `factory-feature/1` and `factory-review/1` each gain one optional parameter with an
  empty default. Existing callers and older staged workflows still launch, so no version bump is
  needed.
- **Specs:** `factory-feature-execution`, `factory-review-execution`, and `factory-feature-reporting`.
  The reporting spec keeps its review-round rule (the description is restored and acceptance is not
  re-run) and makes the completion-comment disclosure of a base merge explicit.
- **Tests:** `tests/integration/test_feature_workflow_scripts.py`, `test_feature_launch.py`, and
  `test_feature_gestures.py`, plus unit tests for the resume point and the checkpoint.
- **Operators:** a blocked feature can be unblocked by fixing the cause on `main` and commenting,
  with no need to commit the fix to the claim's branch. Conflicting continuations now either resolve
  or stop with a file list instead of silently restarting.
- **Risk:** an agent's conflict resolution could be wrong. Mitigations: the resolution step runs
  the validator, the verify step re-runs after any merge that added commits, and the merge commit
  appears in the PR for human review.
