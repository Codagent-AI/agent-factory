## Context

The resident's watch step (`src/agent_factory/watch/__init__.py`) runs every cycle: supervise sessions, detect events, dispatch, deliver comments, prune. A `PR-READY` dispatch runs `factory-watch-v2.0.yaml` (contract `factory-watch/2`) in a throwaway checkout. The session follows `.claude/skills/factory-triage/SKILL.md` "Headless PR-READY check" and writes `watch-result.json`, which `watch/result.py` validates. `watch/deliver.py` queues and delivers comments idempotently, but only on the claim's issue. Dispatch state lives in the `watch_dispatch` table, which `ClaimStore._ensure_watch_schema` creates outside the versioned schema so a rolled-back release can still open the database. The post-merge sync (`factory-pull-request-lifecycle`) already updates the working clone and closes the issue after a factory pull request merges.

## Decisions

### The session rates, the resident merges

The agent session only returns a rating. A new module, `watch/merge.py`, applies the deterministic gates and calls GitHub. This keeps the invariant that no dispatched session merges, and lets the factory pin the merge to the exact head the agent rated. Do not give the session a merge capability or prompt it to run `gh pr merge`.

### Result schema and contract `factory-watch/3`

The `pr-check` result gains a `risk` object: `{"level": "low"|"medium"|"high", "head_sha": "<40-hex>", "reasons": ["..."]}`. It is required when the dispatch's brief said auto-merge is on, and must be absent or null otherwise. A missing or invalid `risk` when it is required makes the result invalid, so the dispatch is recorded `interrupted` and nothing merges (fail closed). Ship `factory-watch-v3.0.yaml` with contract `factory-watch/3`, and drop v2. Its prompt replaces "Do not review the pull request's code" with: rate risk per the skill when the brief says auto-merge is on; never comment on, approve, or merge the pull request. Update `session.py` (`WORKFLOW_FILE`, `CONTRACT`, and an auto-merge flag in the brief). Keep its existing instruction that the session must not merge.

### Merge state on the dispatch row

Add a nullable `merge_json TEXT` column to `watch_dispatch` with an idempotent `ALTER TABLE ... ADD COLUMN` in `_ensure_watch_schema`. That leaves `user_version` alone, so the previous release still opens the database, and it ignores the column. Shape: `{"auto_merge": bool, "state": "waiting"|"merged"|"not-merged", "level", "head_sha", "reason", "checked_at", "merged_at", "merge_sha"}`. At launch, record `auto_merge` from the cycle's configuration so result validation knows whether `risk` is required. When a valid `pr-check` result with `risk` is recorded `completed`, set `state` to `waiting`. A `medium` or `high` rating goes straight to `not-merged`.

### The merge step in the cycle

Insert `_safe("merge", ...)` in `watch.step` after `supervise` and before `deliver`, and run it whether or not watching is enabled, so waiting merges end when watching or auto-merge is turned off. For each `waiting` row, in order:

0. Read the pull request. If it is already merged and its merge commit has the rated `head_sha` as a parent, record `merged` (with `merge_sha`) and send no merge request. This recovers a restart between GitHub accepting the merge and the row being updated.
1. Auto-merge off or watching disabled: `not-merged`, "auto-merge off".
2. Paused, any check or status still pending, or no check or status reported yet on the head: stay `waiting` until `checked_at + 60 min`, then `not-merged` naming what it waited for (including "no checks reported").
3. The stored `pr_url`'s owner, repository, or number differs from the row's `repository` and `pr_number` (`detect.py`'s `_PR` currently ignores the owner and repository); repository not a fix target, or base not the target's branch; pull request not open, a draft, or conflicting (treat `mergeable` unknown as pending); head differs from `head_sha`; a check or status failed; an unresolved review thread; a writer's latest review is `CHANGES_REQUESTED`: `not-merged` with that gate.
4. Otherwise `PUT /repos/{repo}/pulls/{n}/merge` with `merge_method=merge` and `sha=head_sha`. Success → `merged`. Any rejection → `not-merged` with GitHub's message, no retry.

A transient GitHub read error leaves the row `waiting`, logs it, and retries next cycle (it is still bounded by the 60 minutes). When a row leaves `waiting` (or goes straight to `not-merged`), queue the risk-verdict delivery in the same transaction.

### GitHub client additions

In `github.py`, extend `get_pull_request`/`PullRequestState` as needed with draft, mergeable, base ref, and head SHA. Add a combined check read for a commit, covering check runs and commit statuses and returning pending, failed (with names), or success. Add `merge_pull_request(repository, number, sha)`. Reuse `list_review_activity` for thread resolution, but add `state` to its `reviews` GraphQL selection and carry it on each returned review (today it fetches only `id body submittedAt author`), so the merge step can take each writer's latest submitted review by `submittedAt` and check for `CHANGES_REQUESTED`. Use `get_permission` to decide which reviewers are writers. Existing callers of `list_review_activity` must keep working.

### Comment delivery to a pull request

`deliver.queue` gains a target parameter. The risk verdict uses purpose `risk` with `number = pr_number`. Pull request comments are issue comments in the REST API, so `list_comment_records` and `create_comment` work unchanged, and marker adoption is per target. The comment body is rendered in `result.py` next to `triage()`.

### Risk bar lives in the skill

The criteria in the spec go verbatim into the "Headless PR-READY check" section of `factory-triage/SKILL.md`, as a rating step and the extended result schema. The resident does not re-derive them; its gates are the deterministic backstop.

## Risks / Trade-offs

- **An agent misjudges risk.** The gates stop the obvious cases (red CI, moved head, open threads), and turning off `[watch] auto_merge` stops all merging on the next cycle. The verdict comment on every rated pull request makes ratings auditable.
- **This ships enabled.** The first deploy starts merging low-risk pull requests in every fix target, including Agent Factory itself. Merging Agent Factory does not deploy it; deploying stays manual.
- **Branch protection that requires approvals** makes every merge fail in that repository. This is reported, not bypassed.
- **Rollback.** A previous release ignores `merge_json` and runs contract `factory-watch/2`. Rows left `waiting` resume when the newer release is redeployed, or time out with "60 minutes elapsed".
