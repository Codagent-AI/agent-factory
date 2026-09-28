## Context

A feature claim freezes `revisions.target`, `runner`, and `skills` at admission. On every later attempt, `PullRequestHandler.prepare` (`src/agent_factory/work_kinds/pull_request/handler.py`) clones the target repository from the local bare mirror at `revisions.target`. It then works out the resume point from the claim's pushed branch with `PullRequestWorkspace.feature_checkpoint` (`workspace.py`) and `feature_resume_point`. The packaged `factory-feature` workflow then runs `prepare-branch.sh`:

- For a **resume** (`resume_from` set), it checks out `origin/<branch>` and merges nothing.
- For a **prior-branch continuation** (`prior_branch` set), it checks out `origin/<prior>` and merges the frozen `target_head`. On a conflict, it runs `git merge --abort` and falls back to a fresh branch with a `resume.json` fallback. After a clean merge, it renames `openspec/changes/<prior_change>` (and any `archive/*-<prior_change>`) to this claim's change name.
- Otherwise it cuts the branch from `target_head`.

`feature_checkpoint` runs `git log --format=%(trailers:key=Factory-Checkpoint,valueonly) FETCH_HEAD ^base_sha [^exclude_sha]` and returns the first `planned`/`implemented`/`archived` value. Git sorts that walk by commit date. Once `main` has been merged into the branch, the walk also includes `main` commits made after admission. Those commits include other features' checkpoint commits, because feature PRs merge with merge commits.

A review round (`prepare_review`) fetches the mirror, then clones it with the target at the observed PR head and checks out the PR branch. It then runs `factory-review`, which triages the comments, runs `factory-implement` (validate, push, CI via `core/finalize-pr`) only when a decision is `change`, replies, and writes `review-outcome.json`. The host wrapper (`launch.host_script`) passes `change_name`, `resume_from`, and `prior_branch` only to the feature workflow, because the Runner rejects parameters a workflow does not declare.

Outcome contracts are enforced by `outcome.py`. A feature `needs-input` needs a non-empty `stopped_step`, `questions`, `direction_summary`, and `branch`. A review outcome may not carry `stopped_step`, `review_attention_counts`, or `resume`.

## Goals / Non-Goals

**Goals:**
- Merge the target branch's current head into the claim's branch on every feature resume, recovery, continuation, and feature review round.
- Resolve conflicts with an agent, or stop with `needs-input`, and never discard the branch's work.
- Keep resume points and the resume order exactly as they are today.
- Re-validate when a merge brings in commits.
- Record the admission target and the merged head.

**Non-Goals:**
- Fix claims and fix-PR review rounds.
- Moving the frozen Runner, Skills, or Validator revisions.
- Rebasing or rewriting history.
- Refreshing idle PRs on a schedule.
- Re-running acceptance in review rounds.
- Changing the PR description or attention tiers in review rounds.

## Approach

### 1. Controller: resolve and pass the base head

In `PullRequestHandler.prepare` for the feature kind (`ReconcilePolicy.RESUME_FROM_OWN_BRANCH`), before computing the checkpoint:

- Call `self._workspace.fetch_mirror(repository, token)`, then `base_head = self._workspace.resolve_mirror(repository, target["branch"])`. `target["branch"]` is the frozen `frozen_spec.target.branch`. A fetch or resolve failure raises `ReadinessError`, as it does at admission. The claim is held, and no attempt or retry budget is spent.
- Add `base_head` to `resume_fields`, so it is stored in `claim.preparation` and in the `Preparation` payload. It is resolved on every attempt, never reused from an earlier one.
- `launch.build_host_plan` and `host_script` gain a `base_head: str = ""` argument. For the feature contract (not review), the wrapper adds `--param base_head=<sha>`.
- `write_host_provenance` gains `target_at_admission` (= `revisions.target`) and `base_head` keys. The admission event text (`feature-admission:<run>`) appends `; merges <branch>@<sha7>` when the attempt resumes or continues.

`prepare_review`, for a claim whose kind is `feature`, calls `resolve_mirror(repository, target["branch"])` after its existing `fetch_mirror`. It stores the result as `base_head` in the review payload, which becomes `review.json`. Fix review rounds get no `base_head`, and that absence is what keeps them unchanged.

### 2. A continuation branch that holds nothing of its own is still a continuation

A new claim's merge conflict can stop the attempt before the claim has done any work of its own. In that case the claim's branch is pushed at exactly the prior branch's head (see 4). On the next attempt, `prepare` takes the own-branch path. If `resume.head_sha == continuation_head` (the claim added nothing beyond the prior head), the handler also sets `prior_branch = self.branch_name(previous)`, using the same previous-claim lookup as the continuation path.

Because the prior branch and the own branch point at the same commit, `prepare-branch.sh` checks out the same tree. The artifact reconciliation that a continuation at implementation requires (`reconcile-skip.sh` keys on `prior_branch`) still runs, and the change rename still happens. Later pushes fast-forward the claim's branch. The resume point comes from `feature_resume_point` exactly as today: the recorded `needs-input` stop wins.

### 3. `prepare-branch.sh`: merge, never fall back on conflict

Inputs gain `base_head` (optional). The merge source is `base_head` when set, otherwise the frozen `target_head`, which keeps older callers working.

```
prior set:   fetch origin/<prior> ─ fail → fallback "prior branch unavailable"
             checkout -B <branch> origin/<prior>
             merge_base(source)
resume set:  fetch origin/<branch> ─ fail → fallback "resume branch unavailable"
             checkout -B <branch> origin/<branch>
             merge_base(source)        (only when base_head is set)
neither:     checkout -B <branch> <target_head>   (no merge)
```

`merge_base(source)` does the following:

1. Record `pre_merge_head=$(git rev-parse HEAD)`.
2. If `git merge-base --is-ancestor <source> HEAD`, set status `current` and make no commit.
3. Otherwise run `git merge --no-ff --no-edit -m "[factory-feature] chore: merge <source7> from the target branch into <branch>" <source>`. `--no-ff` also applies when HEAD is an ancestor of the source, so the target branch's commits never become the claim's first-parent history.
   - On success, set status `merged`.
   - On a conflict, set status `conflict`. Leave `MERGE_HEAD` and the conflicted index in place, and write `merge-conflict.json` with `base_head`, `pre_merge_head`, `resume_from` (the incoming, not yet lowered, resume point), `prior_branch`, and `conflicted` (`git diff --name-only --diff-filter=U`).
4. Write `base-merge.json` (`target_at_admission`, `base_head`, `pre_merge_head`, `status`, `merge_commit` when merged) to the artifact directory. It is attempt evidence. After a conflict, `check-merge.sh` completes it with the resolved merge commit.

The script no longer runs `git merge --abort`. The rename of the prior change moves out of the script into `continue-change.sh` (see 5).

**Re-verification.** When the status is `merged` or `conflict` and the effective resume point is `finalize`, the script prints `verify` as `effective_resume`. `restore-skipped-verify-status` therefore does not mark validation as passed, and `verify` runs. Resume points at or before `verify` already run verification after the merge.

### 4. New `factory-feature` steps between `prepare-branch` and `create-change`

| Step | Kind | Runs when | Does |
|---|---|---|---|
| `resolve-merge` | `lead-agent`, autonomous | `merge-conflict.json` exists and there is no outcome | Follows `factory-define-rules.md`. Reads `merge-conflict.json` and `{{issue_file}}`, whose eligible comments may answer an earlier merge question. Resolves every conflict, keeping the intent of both sides, runs `agent-validator run`, and completes the merge with `git commit --no-edit`. If it cannot resolve the conflicts confidently, it writes `{{artifact_dir}}/merge-stop.json` (`questions`, `direction_summary`) and leaves the tree as it is. |
| `check-merge` | `check-merge.sh` | `merge-conflict.json` exists, `merge-stop.json` does not, and there is no outcome | Verifies the resolution mechanically (below) and records the resolved merge commit in `base-merge.json`. Any failed check fails the attempt as a technical failure, handled by recovery. |
| `record-merge-stop` | `record-merge-stop.sh` | `merge-stop.json` exists and there is no outcome | Described below. |
| `continue-change` | `continue-change.sh` | `prior_branch` is set and there is no outcome | The idempotent rename, moved from `prepare-branch.sh`: `openspec/changes/<prior_change>` → `<change_name>` and `archive/*-<prior_change>` → `archive/*-<change_name>`. Each move runs only if the source exists and the destination does not. It commits `[factory-feature] chore: continue <prior> as <change>` when anything moved. |

`check-merge.sh`, shared by the feature and review workflows, reads `pre_merge_head`, `base_head`, and `conflicted` from `merge-conflict.json`. It fails unless every one of these checks holds:

1. **The merge is finished and clean.** There is no `MERGE_HEAD`, `git diff --name-only --diff-filter=U` is empty, and the working tree is clean.
2. **No conflict markers remain.** `git grep -nE '^(<<<<<<<|=======|>>>>>>>)( |$)' -- <conflicted files>` finds nothing.
3. **The branch's work survives.** Both `pre_merge_head` and `base_head` are ancestors of HEAD. `pre_merge_head` is on HEAD's first-parent chain (`git rev-list --first-parent HEAD` lists it). The first commit after it on that chain, the resolution, is a merge commit whose parents are exactly `pre_merge_head` then `base_head`.
4. **Only the conflicted paths were hand-resolved.** `git merge-tree --write-tree pre_merge_head base_head` (git 2.38 or later; this Mac has 2.50) gives the automatic merge tree, including its conflict-marked files. `git diff --name-only <that tree> <resolution commit>` must list only paths in `conflicted`. A resolution that resets to `base_head`, checks out one side wholesale, or drops the branch's non-conflicting files therefore fails.

Commits after the resolution (for example, a fix for the validator) are allowed. They stay visible in the branch history, and re-verification covers them. On success, the script rewrites `base-merge.json` with `status: resolved`, `merge_commit: <resolution SHA>`, and `follow_up_commits` (the SHAs after the resolution).

`record-merge-stop.sh`, a new script separate from `record-stop.sh`, which would try to commit the unmerged index:

1. Run `git merge --abort` in this attempt's disposable clone. HEAD returns to `pre_merge_head`, which is the pushed branch or the prior branch.
2. If `git ls-remote --exit-code origin refs/heads/<branch>` finds no branch (a continuation whose own branch was never pushed), run `git push origin HEAD:refs/heads/<branch>`. HEAD is exactly the prior head, because `continue-change` has not run.
3. Write `feature-outcome.json` as a valid feature `needs-input`:
   - `stopped_step`: `merge-conflict.json.resume_from`, the step the attempt was heading to.
   - `questions` and `reasons`: the agent's questions, with the first one prefixed by `Merging <base7> conflicts in: <files>`.
   - `direction_summary`, `branch`.

   The outcome contract gets no new fields. The file list is in the question text and in `merge-conflict.json`.

All later steps already skip on `test -s feature-outcome.json`, as they do after a definition stop. `verify-outcome` then validates the stop.

### 5. Checkpoint reading

`feature_checkpoint` adds `--first-parent` to its `git log`. Every merge of the target branch is a `--no-ff` merge whose first parent is the claim's branch. The claim's own checkpoint commits therefore stay on the first-parent chain, and merged-in `main` history (including other features' checkpoint trailers) is reached only through second parents, which the walk skips. `^base_sha` and `^exclude_sha` keep their meaning.

The continuation case still works, because the claim's branch was created by checking out the prior branch, not by merging it. `feature_resume_point` and `factory-resume-skip.sh` are unchanged.

### 6. `factory-review` for feature rounds

Before `triage`:

| Step | Runs when | Does |
|---|---|---|
| `merge-base` (`review-merge-base.sh`) | always; a no-op without `base_head` | Reads `base_head` from `review.json`. When it is absent (a fix round), status is `none`. Otherwise it applies the same `merge_base` logic as 3, through a shared `merge-base.sh` that both scripts source. Captures `merge_status`: `none`, `current`, `merged`, or `conflict`. Writes `base-merge.json`, plus `merge-conflict.json` on a conflict. |
| `resolve-merge` | `merge_status = conflict` | The same agent contract as the feature step, reading `review.json` for context and answers. |
| `check-merge` | `conflict` and no `merge-stop.json` | The same `check-merge.sh`, which records the resolved merge commit in `base-merge.json`. |
| `record-review-merge-stop` (`record-review-merge-stop.sh`) | `merge-stop.json` exists | Runs `git merge --abort`. Writes `review-outcome.json`: `{"contract":"factory-review/1","outcome":"needs-input","reasons":[<file-list question>, ...],"answered":[],"changed":[]}`. Pushes nothing and posts nothing. |

Changes to the existing steps:

- `triage`, `record-triage`, `repair-triage`, `recheck-triage`, `respond`, and `record-outcome` gain `skip_if: test -s review-outcome.json`, so a merge stop ends the round.
- `save-description`, `implement`, and `restore-description` run when `changes_needed = true`, or when `merge_status` is `merged` or `conflict` and `changes_needed` is `false`. They do not run when triage returns `needs-input`: that round pushes nothing, including a resolved merge (D15).
- `factory-implement` gains an optional `implement_plan` parameter, default `"true"`. `implement-plan` skips when it is `"false"`. The review workflow passes `"false"` for a merge-only round. The round then goes straight to the validator gate (with its repair cycle), `core/finalize-pr` (push plus one CI fix cycle), and `write-result`. Fix callers never pass the parameter, so their behavior is unchanged.
- `record-review-outcome.sh` gains a `merge_status` input. When `changes_needed` is `false` and `merge_status` is `merged` or `conflict`, it reads `implement-result.json` exactly as the `true` branch does. The outcome is `pull-request` only when the validator and CI passed, and otherwise `failed`.
- The `respond` prompt reads `{{artifact_dir}}/base-merge.json` (whose `merge_commit` is set for both clean and resolved merges) and `implement-result.json`. After `git fetch origin <branch>`, it confirms with `git merge-base --is-ancestor <merge_commit> origin/<branch>` that the merge commit was pushed. Only then does the completion comment name that SHA, linked to the commit on GitHub, as added after acceptance and not covered by the acceptance evidence, with a link to the evidence section of the PR description. When the merge was not pushed, the comment says so instead. The description restore and the "acceptance was not re-run" statement are unchanged.

A blocked review claim returns through the existing pull-request lifecycle: the claim is blocked by a review round's `needs-input`, and the next eligible writer comment admits a new round. That round resolves a fresh `base_head` and merges again.

### 7. Archive-block wording

In `record-archive-block.sh`, `direction_summary` becomes: "Implementation is complete and pushed. Archiving is blocked by the cause above. Fix it on the target branch, or commit the fix to this branch, then comment on the issue: the next attempt merges the target branch and resumes at archive." `test_record_archive_block_preserves_explanation_and_branch` replaces its assertion that the summary contains "a fix merged to main does not reach it" with checks for "Fix it on the target branch" and "commit the fix to this branch".

### 8. Data flow

```
prepare (controller)             factory-feature (host)
  fetch_mirror ──► resolve_mirror(target.branch) = base_head
  checkpoint (--first-parent) ──► resume_from, prior_branch
  host plan --param base_head ──► prepare-branch.sh ── current/merged ──► continue-change ─► steps
                                        │ conflict
                                        ▼
                                  resolve-merge ─► check-merge ─► continue-change ─► steps
                                        │ merge-stop.json
                                        ▼
                                  record-merge-stop ─► needs-input (stopped_step = resume point)
```

## Decisions

- **Configured target branch, not GitHub's default branch (D2).** The target branch is resolved from the refreshed mirror with existing helpers. It is the PR's base, and it needs no extra API call.
- **`--no-ff` merges with first-parent checkpoint reading (D4, D7).** Together they guarantee that merged-in history never counts as the claim's progress, with no extra bookkeeping. Excluding every merged base would require storing them all. Filtering by change name breaks on the continuation rename.
- **Conflict stops keep the original resume point (D5).** No new resume key is added, so the resume order is unchanged.
- **Merge stops get their own recorder (D10).** An unmerged index cannot pass through `record-stop.sh`. `merge --abort` affects only the disposable clone, never pushed work.
- **An unadvanced continuation branch is treated as a continuation (D17).** Without this, the attempt after a continuation merge stop would skip artifact reconciliation and the change rename.
- **The rename runs after the merge completes (D11).** Clean and resolved merges share one idempotent step.
- **`base_head` is a feature workflow parameter, and a `review.json` field for rounds (D18).** The feature workflow already takes its resume inputs as parameters. The review workflow is shared with fixes, and the wrapper passes it no kind-specific parameters. Carrying `base_head` in `review.json` means a fix round is unchanged simply because the field is absent, and it avoids a new review parameter.
- **Merge-only review rounds reuse `factory-implement` through `implement_plan=false` (D19).** This avoids a second copy of the validator, push, and CI loop, and gives the round the same failure semantics as a change round.
- **No new outcome fields (D8, D20).** Conflict details are in the question text and in the `merge-conflict.json` and `base-merge.json` evidence, so `outcome.py`'s contract checks are unchanged.
- **A mechanical guard after agent resolution (D21, D26).** `check-merge.sh` turns an agent that left markers or an unfinished merge, or discarded either side's history or the branch's non-conflicting work, into a technical failure with a recovery retry, not a pushed broken or truncated tree.

## Risks / Trade-offs

- **Wrong conflict resolution.** Mitigations: the agent runs the validator, `check-merge` runs, verification re-runs after a merge that added commits (feature), the validator and CI run (review), and the merge commit is visible in the PR, where the review completion comment calls it out. Acceptance is not re-run for review rounds, by design.
- **An extra merge commit on every resume where `main` moved.** This makes PR history noisier, but it is standard GitHub merge practice, and it keeps the final PR merge small.
- **The mirror fetch on every prepare** adds a network call at launch. It is already required at admission, and a failure holds the claim rather than failing it.
- **A resumed attempt could stop on a conflict repeatedly** if `main` keeps changing the same lines. Each stop asks a precise question. This is accepted.
- **In-flight claims across the deploy.** Attempts already running keep their release and old workflow. The next attempt uses the new behavior. Old preparations lack `base_head`, and the handler resolves it again for each attempt anyway.

## Migration Plan

This needs no data migration. `base_head`, `target_at_admission`, and the new evidence files are additive. `factory-feature/1` and `factory-review/1` keep their versions, because the new parameters are optional with empty defaults. Deploy with `scripts/deploy.sh`. To roll back, redeploy the previous release. After a rollback, a claim whose branch already carries a merge of `main` is read by the old checkpoint walk, which lacks `--first-parent`, and may resume at the wrong step. Before rolling back, check claims resumed since the deploy, and re-admit any claim that is affected.

## Verification

Integration tests in `tests/integration/test_feature_workflow_scripts.py` use real temporary git repositories and a bare `origin`:

- **Archive unblock:** a branch blocked at `archive` by a bad file, a fix committed to `main`, then `prepare-branch.sh` with `resume_from=archive` and `base_head=main`. The file is fixed on the branch and there is one merge commit. `feature_checkpoint` still returns `implemented`, so the attempt resumes at archive.
- **Conflicting continuation:** `prepare-branch.sh` leaves `MERGE_HEAD` and writes `merge-conflict.json` with the file list and no `resume.json` fallback.
  - The resolution path resolves the conflict, and then `check-merge` and `continue-change` rename both unarchived and archived prior changes.
  - The stop path runs `record-merge-stop.sh`, which aborts, pushes the prior head unchanged as the claim's branch, and writes a valid `needs-input` whose `stopped_step` is `implement` or `verify`.
  - This replaces `test_prepare_branch_conflict_falls_back_to_fresh`.
- **`check-merge.sh` rejections:** it rejects a leftover `MERGE_HEAD`, unmerged paths, conflict markers, a reset to `base_head`, a resolution whose parents are not `pre_merge_head` and `base_head`, a HEAD that no longer has `pre_merge_head` on its first-parent chain, and a resolution that changed or deleted non-conflicted paths relative to `git merge-tree`. On success it records the resolved SHA.
- **Clean, current, and fast-forwardable merges:** a merge commit, no commit, and a `--no-ff` merge commit, respectively.
- **Re-verification:** the effective resume point `finalize` becomes `verify` after a merge.

Other tests:

- **Unit tests for `feature_checkpoint`:** after `main`, carrying other features' `implemented`/`archived` trailers, is merged into a branch whose own last checkpoint is `planned`, the result is `planned`. They also cover a continuation branch.
- **Unit tests for `feature_resume_point`:** the needs-input, draft, and continuing order, with the merge present.
- **Handler tests** (`test_feature_launch.py` / `test_feature_gestures.py`):
  - `base_head` is resolved from a moved mirror, passed as a parameter, and recorded in the preparation and in host provenance.
  - Frozen revisions are unchanged.
  - A `ReadinessError` on a resolve failure holds the claim.
  - An own branch at `continuation_head` restores `prior_branch`.
- **Review workflow tests:**
  - A fix round has no `base_head`, and its steps and parameters are unchanged.
  - A feature answer-only round with a merged base passes `implement_plan=false`, and `record-review-outcome.sh` maps the validator and CI results.
  - The conflict stop writes a valid review `needs-input` and pushes nothing.
  - A merge followed by triage `needs-input` pushes nothing.
- **Catalog test:** the new scripts are staged and listed.
- **Archive-block test:** the updated wording assertions.

## Open Questions

None.
