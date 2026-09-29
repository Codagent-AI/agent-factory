- [x] Merge the target branch's current head into a feature claim's branch on every resume, continuation, and feature review round

## Task: Merge the current target branch on every feature resume

Implement the whole change described in `proposal.md`, the delta specs under `specs/`
(`factory-feature-execution`, `factory-review-execution`, `factory-feature-reporting`), and
`design.md`. The decision log is `decisions.md` (D1–D28). The automated obligations are in
`test-plan.md` (INT-001 to INT-009, E2E-001). Always compare against `origin/main`. The
workflow files live in `src/agent_factory/work_kinds/pull_request/workflow/`.

### Scope

1. **Controller** (`work_kinds/pull_request/handler.py`, `launch.py`), design §1–2:
   - In `prepare`, for the feature kind, call `fetch_mirror` and then
     `resolve_mirror(repository, target["branch"])` on every attempt. Store `base_head` in
     `resume_fields`, which updates both the claim preparation and the payload. A failure raises
     `ReadinessError`, which holds the claim with no run recorded and no retry spent.
   - When the own-branch head equals `continuation_head` (the claim has added nothing beyond the
     prior head), restore `prior_branch = self.branch_name(previous)` (D17).
   - Add `base_head` to `build_host_plan`, `_assemble_host_plan`, and `host_script`. Pass
     `--param base_head=<sha>` to the feature contract only, never to `factory-review`.
   - `write_host_provenance` records `target_at_admission` and `base_head`. The
     `feature-admission` event appends `; merges <branch>@<sha7>` for resumes and
     continuations.
   - `prepare_review` adds `base_head` (the resolved head of the target branch) to the review
     payload, and so to `review.json`, for feature claims only.
2. **Checkpoints** (`workspace.py`, design §5): add `--first-parent` to the `git log` in
   `feature_checkpoint`. Leave `feature_resume_point` and `factory-resume-skip.sh` unchanged.
3. **Branch preparation scripts** (design §3–4):
   - Add a shared `merge-base.sh`, which `prepare-branch.sh` and `review-merge-base.sh` source
     for the merge logic:
     - `current` when the base is an ancestor of HEAD, with no commit;
     - otherwise `git merge --no-ff --no-edit -m "[factory-feature] chore: merge <base7> from the target branch into <branch>"`;
     - on a conflict, leave `MERGE_HEAD` in place and write `merge-conflict.json`
       (`base_head`, `pre_merge_head`, `resume_from`, `prior_branch`, `conflicted`);
     - always write `base-merge.json`.
   - `prepare-branch.sh` accepts optional `base_head`. It merges `base_head`, or `target_head`
     when `base_head` is empty, on both the prior-branch and the resume paths, and never on
     the fresh path. Remove the `merge --abort` fallback, and move the prior-change rename out
     of the script. Fall back to a fresh branch only when the fetch fails. Print `verify` in
     place of `finalize` when the merge status is `merged` or `conflict`.
   - Add `continue-change.sh`: the idempotent rename of the unarchived and archived prior
     change to this claim's change name, committed only when something moved.
   - Add `check-merge.sh`, with every check in design §4 and D26:
     - no `MERGE_HEAD`, no unmerged paths, and a clean tree;
     - no conflict markers in the conflicted files;
     - `pre_merge_head` and `base_head` are ancestors of HEAD;
     - `pre_merge_head` is on the first-parent chain;
     - the resolution's parents are exactly `pre_merge_head` then `base_head`;
     - the resolution differs from `git merge-tree --write-tree` only in conflicted paths.

     On success it rewrites `base-merge.json` with `status: resolved`, `merge_commit`, and
     `follow_up_commits`.
   - Add `record-merge-stop.sh`:
     - run `git merge --abort`;
     - push HEAD to the claim's branch only when the remote branch is missing;
     - write a valid feature `needs-input`: `stopped_step` from `merge-conflict.json`,
       questions whose first entry names every conflicting file, a `direction_summary`, and
       `branch`.
   - Add `record-review-merge-stop.sh`: run `git merge --abort` and write a `factory-review/1`
     `needs-input` with the file list in `reasons` and empty `answered` and `changed`. It
     pushes nothing.
4. **`factory-feature-v1.0.yaml`** (design §4):
   - Declare `base_head` with an empty default and pass it to `prepare-branch`.
   - Add the steps `resolve-merge` (a `lead-agent` autonomous step whose prompt matches the
     design), `check-merge`, `record-merge-stop`, and `continue-change`. They go between
     `prepare-branch` and `create-change`, and each skips when `feature-outcome.json` exists.
   - Keep every shell placeholder interpolatable.
5. **`factory-review-v1.0.yaml`, `factory-implement-v1.0.yaml`, and `record-review-outcome.sh`**
   (design §6):
   - Add the steps `merge-base` (capturing `merge_status`), `resolve-merge`, `check-merge`, and
     `record-review-merge-stop` before `triage`.
   - Add `skip_if: test -s review-outcome.json` to `triage`, `record-triage`, `repair-triage`,
     `recheck-triage`, `respond`, and `record-outcome`.
   - Run `save-description`, `implement`, and `restore-description` for a `change` decision, or
     when the merge status is `merged` or `conflict` and `changes_needed` is `false`. Never run
     them when triage returns `needs-input`.
   - Add the optional `implement_plan` parameter to `factory-implement`, default `"true"`. The
     review workflow passes `"false"` for a merge-only round.
   - `record-review-outcome.sh` takes `merge_status` and maps `implement-result.json` for
     merge-only rounds.
   - The `respond` prompt reads `base-merge.json`. It names the pushed merge SHA, linked, with a
     link to the acceptance evidence, only after confirming the SHA is on `origin/<branch>`.
     Otherwise it says the merge was not pushed.
   - Fix rounds, which have no `base_head`, keep their current behavior.
6. **Archive-block wording** (`record-archive-block.sh`, design §7): use the new
   `direction_summary`, which says that a fix on the target branch reaches the claim on resume
   and that committing to the branch still works.
7. **Catalog:** add the new scripts to the staged file lists, and keep them executable:
   - `FEATURE_STAGED_FILES` in `kinds.py`: `merge-base.sh`, `continue-change.sh`,
     `check-merge.sh`, and `record-merge-stop.sh`.
   - `REVIEW_WORKFLOW_SCRIPTS` in `launch.py`: `merge-base.sh`, `review-merge-base.sh`, `check-merge.sh`,
     and `record-review-merge-stop.sh`.
8. **Docs:** in `docs/operations.md`, and in `AGENTS.md` where it describes frozen revisions,
   explain the following: every feature resume merges the current target branch; Runner,
   Skills, and target admission revisions stay frozen; merge-conflict stops and how to answer
   them; and the rollback caveat from the design's migration plan.
9. **Tests:**
   - Add every obligation in `test-plan.md` (INT-001 to INT-009 and E2E-001).
   - Replace `test_prepare_branch_conflict_falls_back_to_fresh`.
   - Update the two assertions in `test_record_archive_block_preserves_explanation_and_branch`.
   - Add unit tests for the new decision logic, including the finalize-to-verify lowering and
     the `record-review-outcome.sh` mapping.

### Done when

- Every scenario in the delta specs under `specs/` is implemented and covered by the tests that
  `test-plan.md` names.
- `uv run ruff format --check . && uv run ruff check .`, `uv run pyright`, `uv build`, and
  `uv run pytest` pass.
- `openspec validate feature-53-9849f3f5 --strict` passes.
- Fix claims and review rounds on fix pull requests launch with the same parameters and steps
  as before, and `factory-implement` without `implement_plan` behaves exactly as before.
- No merge conflict leads to a fresh start. A fresh start happens only when the branch no longer
  exists.
- No test or implementation step runs `scripts/deploy.sh`, `launchctl`, or model sessions,
  pushes to GitHub, or touches `~/.agent-factory`, `~/Library/LaunchAgents`, or
  `/Users/paul/codagent/*`.
