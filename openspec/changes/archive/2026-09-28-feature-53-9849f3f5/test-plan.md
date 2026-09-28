## Coverage Strategy

The specifications remain the source of unit-test requirements. That includes `feature_resume_point` ordering, `record-review-outcome.sh` mapping, and parameter defaults. This plan records only the additional integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only obligations.

Most of the risk in this change is at the boundary with real git. That covers merges and conflicts, first-parent history walks, pushes to a remote, and `merge --abort`. It also covers the packaged workflow scripts, which the Runner executes against a real clone. Integration tests should therefore use real temporary git repositories with a bare `origin` and a bare mirror, and run the packaged scripts directly, as `tests/integration/test_feature_workflow_scripts.py` already does. They should not mock git.

The agent steps (`resolve-merge`) are model-driven and are not run in automated tests. Tests simulate their two possible results:
- **Resolved:** the test resolves the conflict with git and runs `git commit --no-edit`.
- **Stopped:** the test writes `merge-stop.json`.

The workflow YAML is covered by structural tests of step order, `skip_if` guards, and parameters, in the same style as the existing catalog and placeholder tests. The controller journey is covered once end to end through the existing stub-Runner harness.

## Integration Tests

### INT-001: Unblock an archive stop with a fix merged to the target branch
- Covers: `factory-feature-execution` "Unblock archive after a fix lands on the target branch", "Archive repair is blocked", and "Resume a stopped claim after the target branch moved"; the issue's first acceptance test.
- Boundary: `prepare-branch.sh` against a real clone and bare `origin`, then `PullRequestWorkspace.feature_checkpoint` against a real bare mirror.
- Setup:
  1. On `main`, commit a base that contains a living spec that makes archive fail.
  2. Admit: branch `factory/feature-…` from that commit (the frozen target).
  3. Push `planned` and `implemented` checkpoint commits.
  4. Commit a fix to the blocking file on `main`, plus an unrelated feature merge whose commits carry `Factory-Checkpoint: archived`.
- Action: run `prepare-branch.sh` with `resume_from=archive` and `base_head=<main head>`. Then run `openspec archive` (or the equivalent file check the test uses) on the result. Then push, and read `feature_checkpoint(base_sha=<admission target>)`.
- Assertions:
  - `effective_resume` is `archive`.
  - The branch has exactly one new merge commit, and its first parent is the prior branch head.
  - The blocking file matches `main`'s fix.
  - The archive step succeeds.
  - `base-merge.json` records `target_at_admission`, `base_head`, and status `merged`.
  - `feature_checkpoint` still returns `implemented`, never the merged-in `archived`.
  - No `resume.json` fallback is written.
- Execution: `tests/integration/test_feature_workflow_scripts.py`, `uv run pytest tests/integration`.

### INT-002: Merge outcomes for resumes and continuations
- Covers: `factory-feature-execution` "Resume when the target branch has not moved", "Recover from a technical failure after the target branch moved", "Re-verify after a merge brings in new commits", "Continue a failed feature on a new claim", "Continue a feature whose prior claim failed after archival", and "Continue when the prior branch is gone".
- Boundary: `prepare-branch.sh` and `continue-change.sh` with real git.
- Setup: a parameterized set of branch and `main` topologies:
  - `main` already contained in the branch;
  - `main` ahead with no conflict;
  - the branch an ancestor of `main`;
  - a prior branch with an unarchived change;
  - a prior branch with an archived change;
  - a missing prior branch;
  - a missing own branch;
  - a resume at `finalize`.
- Action: run `prepare-branch.sh` (and `continue-change.sh` when `prior_branch` is set) with `base_head`. For compatibility, also run once without `base_head`.
- Assertions:
  - Status is `current` with no new commit when `main` is already contained.
  - Otherwise there is a `--no-ff` merge commit whose first parent is the branch, including when the branch was an ancestor of `main`.
  - A `finalize` resume point prints `verify` after a merge and stays `finalize` when the status is `current`.
  - Continuations end with the change under the new claim's name, both unarchived and `archive/*-<name>`.
  - Running `continue-change.sh` twice changes nothing the second time.
  - Only a missing branch writes a `resume.json` fallback.
  - Without `base_head`, a resume merges nothing, and a continuation merges `target_head`, as before.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-003: Resolve a conflicting continuation without discarding work
- Covers: `factory-feature-execution` "Continue a prior branch that conflicts with the target branch" (resolved branch) and "Resolve a conflicting merge"; the issue's second acceptance test.
- Boundary: `prepare-branch.sh`, then a simulated `resolve-merge`, then the `check-merge` command and `continue-change.sh`, with real git.
- Setup: a prior branch with a committed plan and implementation that edits a file `main` also edits. The prior change is unarchived in one case and archived in the other.
- Action: run `prepare-branch.sh` with `prior_branch`. The test resolves the conflict with git and runs `git commit --no-edit`. Then run `check-merge` and `continue-change.sh`.
- Assertions:
  - `prepare-branch.sh` exits 0, leaves `MERGE_HEAD`, and writes `merge-conflict.json` listing exactly the conflicting paths and the incoming `resume_from`.
  - It writes no `resume.json` fallback and never checks out `target_head` as a fresh branch.
  - After resolution, `check-merge` passes.
  - The prior plan and implementation files are present.
  - The change is carried over to the new name.
  - `main`'s commit is an ancestor of HEAD.
- Execution: `tests/integration/test_feature_workflow_scripts.py`. This replaces `test_prepare_branch_conflict_falls_back_to_fresh`.

### INT-004: Stop on an unresolvable conflict and resume later
- Covers: `factory-feature-execution` "Stop on a conflict the agent cannot resolve" and "Stop on a conflict while continuing a prior branch"; D10 and D17.
- Boundary:
  - `record-merge-stop.sh` with real git and a bare `origin`;
  - the resulting `feature-outcome.json` read through `outcome.read_interpreted_outcome`;
  - `PullRequestHandler.prepare` on the next attempt, against a real mirror.
- Setup:
  - Case A: a same-claim resume at `implement` whose branch is already pushed.
  - Case B: a continuation whose own branch was never pushed.
  - In both cases the merge conflicts and the test writes `merge-stop.json` with a question.
- Action: run `record-merge-stop.sh`. Then, for case B, prepare the next attempt through the handler with a stored `needs-input` result and a writer comment.
- Assertions:
  - The clone has no `MERGE_HEAD` and a clean tree.
  - In case A, the remote branch is unchanged.
  - In case B, the remote branch exists at exactly the prior head.
  - No pushed commit contains conflict markers.
  - The outcome parses as a valid feature `needs-input` whose `stopped_step` is the incoming resume point (`implement`, or `verify` for an archived prior). Its first question names every conflicting file.
  - For case B, the next attempt's preparation has `prior_branch` set to the previous claim's branch and `resume_from` equal to the stopped step, and it does not start fresh.
- Execution: `tests/integration/test_feature_workflow_scripts.py` for the scripts, and `tests/integration/test_feature_gestures.py` for the handler.

### INT-005: The merge-resolution guard rejects unfinished or destructive resolutions
- Covers: D21 and D26; `factory-feature-execution` "Reject a resolution that discards the claim's work"; the "never commit or push a partially merged tree" clause of the added requirement.
- Boundary: `check-merge.sh`, shared by `factory-feature` and `factory-review`, against real git states built from a real conflicting merge.
- Setup: bad states, each derived from one conflicting merge of a branch with non-conflicting work of its own:
  - a leftover `MERGE_HEAD`;
  - unmerged paths;
  - committed conflict markers in a file that was conflicted;
  - a dirty tree;
  - a HEAD that does not contain `base_head`;
  - a hard reset to `base_head`;
  - a merge commit whose parents are not `pre_merge_head` then `base_head` (for example, a squash or a merge in reversed order);
  - a resolution that loses `pre_merge_head` from the first-parent chain;
  - a merge commit that takes `base_head`'s version of a non-conflicted file the branch changed;
  - a merge commit that deletes a non-conflicted branch file.
  Good states: a proper resolution, and a proper resolution followed by one follow-up commit.
- Action: run `check-merge.sh` with the step's inputs.
- Assertions:
  - It exits non-zero for every bad state and leaves `base-merge.json` unchanged.
  - For the good states it exits zero and rewrites `base-merge.json` with `status: resolved`, the resolution SHA as `merge_commit`, and any `follow_up_commits`.
- Execution: `tests/integration/test_feature_workflow_scripts.py`.

### INT-006: Checkpoints and resume points ignore merged-in history
- Covers: `factory-feature-execution` "Resume point ignores checkpoints merged from the target branch" and "Resume order is unchanged by a merge"; the issue's third acceptance test.
- Boundary: `feature_checkpoint` against a real bare mirror fetched from a real `origin`, feeding `feature_resume_point`.
- Setup:
  - A claim branch whose own last checkpoint is `planned`.
  - A `main` carrying feature PR merge commits with `implemented` and `archived` trailers, and a squash-style commit whose message contains `Factory-Checkpoint: archived`, all dated after the claim's commits.
  - The branch merges `main` with `--no-ff`.
  - Variants:
    - a continuation branch created from a prior branch that had `implemented`, with and without `exclude_sha`;
    - a recorded `needs-input` stop at `design`;
    - an open draft;
    - a continuation.
- Action: call `feature_checkpoint` and `feature_resume_point` before and after the merge, and after a second merge.
- Assertions:
  - The checkpoint is `planned` before and after the merges.
  - The continuation variants return the prior's `implemented` only when the exclusion is dropped, as today.
  - The resume point is identical before and after each merge for every variant: `design`, `verify`, the continuation's step, and `implement`.
- Execution: `tests/integration/test_feature_gestures.py`, or a new `tests/integration/test_feature_checkpoints.py`.

### INT-007: The controller resolves, passes, and records the base head
- Covers: `factory-feature-execution` "Keep frozen revisions across a resume" and the provenance clause of the added requirement; D22.
- Boundary: `PullRequestHandler.prepare` and `plan` with a real bare mirror and a real `origin` repository, through `launch.build_host_plan` and the generated host wrapper text and host provenance.
- Setup: a feature claim admitted at commit A. The test advances the origin's `main` to commit B, and records different current Runner and Skills heads than the frozen ones.
- Action: prepare and plan the next attempt. Separately, make the mirror fetch fail.
- Assertions:
  - The preparation stores `base_head = B`.
  - The wrapper passes `--param base_head=B` only to `factory-feature`, and a review wrapper never gets it.
  - `host-provenance.json` has `target_at_admission = A` and `base_head = B`.
  - `frozen_spec.revisions` is unchanged.
  - The clones use the frozen Runner and Skills commits.
  - The admission event names the merged head.
  - A fetch failure raises `ReadinessError`, records no run, and leaves the retry budget unchanged.
- Execution: `tests/integration/test_feature_launch.py`.

### INT-008: Feature review rounds merge, validate, or stop; fix rounds do not merge
- Covers: all `factory-review-execution` scenarios this change adds or modifies.
- Boundary:
  - `prepare_review` writing `review.json` from a real mirror;
  - `review-merge-base.sh`, `record-review-merge-stop.sh`, and `record-review-outcome.sh` with real git and a bare `origin`;
  - the `factory-review` and `factory-implement` YAML wiring.
- Setup:
  - A feature claim with an open PR branch whose base moved.
  - A fix claim with the same topology.
  - Variants: a clean merge, a merge that is already current, a conflict resolved by the test, and a conflict with `merge-stop.json`.
  - A fake `implement-result.json` with passed, validator-failed, and CI-failed results.
- Action: prepare each round, run the scripts in workflow order, and evaluate the `skip_if` guards of `implement`, `save-description`, `restore-description`, `triage`, `respond`, and `record-outcome` against the captured statuses.
- Assertions:
  - The fix round's `review.json` has no `base_head`, the merge script reports `none`, and the step and parameter set is unchanged.
  - A feature answer-only round with a `merged` status runs `implement` with `implement_plan=false`, and its outcome is `pull-request` only for passed/passed, otherwise `failed`.
  - A `current` answer-only round skips `implement` and reports `pull-request` with the validator `skipped`.
  - The conflict stop leaves the remote branch unchanged, writes a valid `factory-review/1` `needs-input` naming the files with empty `answered` and `changed`, and every later step skips.
  - A resolved merge followed by triage `needs-input` pushes nothing.
  - The `respond` prompt instructions and the recorded `base-merge.json` give the completion comment the exact pushed merge SHA for both a clean merge and a resolved conflict (after `check-merge.sh`), and require the not-pushed wording when the push did not happen. This is a structural check of the prompt text and inputs, since the agent itself is not run.
  - `factory-implement` without `implement_plan` still runs `implement-plan`.
- Execution: `tests/integration/test_review_contract.py`.

### INT-009: Workflow wiring, catalog, and archive-block wording
- Covers: design sections 4, 6, and 7; D9 and D14; the staged catalog.
- Boundary: the packaged workflow files as the Runner catalog sees them, and `record-archive-block.sh` against a real audit log.
- Setup: the packaged workflow directory, plus the existing archive-block fixture.
- Action: load the YAML, stage the catalog, and run `record-archive-block.sh`.
- Assertions:
  - In `factory-feature`, `prepare-branch → resolve-merge → check-merge → record-merge-stop → continue-change` precede `create-change`.
  - Each new step skips when `feature-outcome.json` exists.
  - `base_head` is declared with an empty default.
  - Every shell placeholder is interpolatable.
  - The new scripts are staged and executable.
  - The archive-block `direction_summary` contains "Fix it on the target branch" and "commit the fix to this branch", and no longer says a fix merged to main does not reach the claim.
- Execution: `tests/integration/test_feature_workflow_scripts.py` (updated `test_record_archive_block_preserves_explanation_and_branch`) and `tests/integration/test_feature_workflow_catalog.py`.

## End-to-End Tests

### E2E-001: A stopped feature resumes against a moved `main`
- Covers: the controller's resume journey from `factory-feature-execution` "Merge the target branch into the claim's branch on every resume" and "Resume and continue feature work".
- Surface: the `agent-factory tick` CLI through the existing `tests/e2e/test_fix_cycle.Harness`, with its stub GitHub, stub Runner, and real local mirror.
- Setup:
  - Extend `tests/e2e/test_feature_cycle.py`: the stub Runner's first attempt writes a `needs-input` outcome at `design` and pushes the branch to the harness origin.
  - The test then commits to the origin's `main` and adds a writer comment on the stub issue.
- Journey: tick to admit and launch, let the attempt stop, advance `main`, comment, and tick again.
- Assertions:
  - The second attempt's `runner-args.json` contains `resume_from=design` and `base_head=<new main sha>`.
  - Its host provenance records the admission target and the new head.
  - The claim's frozen revisions are unchanged.
  - The admission event mentions the merge.
  - No credential appears in the plan.
- Execution: `tests/e2e/test_feature_cycle.py`, `uv run pytest tests/e2e`.

No E2E test runs the real `factory-feature` workflow through a real Runner and models. The scripts and their wiring are proven at the integration layer, and a model-driven run adds cost and nondeterminism without adding a stable assertion.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - The feature's own worktree and its `uv` virtual environment.
  - Temporary directories with throwaway git repositories: a bare `origin`, a bare mirror, and working clones built from this repository or from small fixtures.
  - The packaged workflow scripts run directly against those clones.
  - The installed `agent-runner` may be used only for commands that do not start model sessions, such as listing or validating workflows.
- **Credentials and secrets:**
  - None are needed. The GitHub App key, fix credential, and model logins used by the live factory must not be used.
  - A read-only `agent-factory doctor` from the worktree against `~/.agent-factory/config.toml` is allowed.
- **Authorized effects:**
  - Creating, merging, and deleting local temporary repositories and pushing to local bare remotes.
  - Running the test suites and `agent-validator` in the worktree.
  - No external cost.
  - Temporary directories are removed at the end.
- **Off limits:**
  - Pushing to, commenting on, or opening PRs in any GitHub repository.
  - Changing the Project board.
  - Running `scripts/deploy.sh`, `launchctl`, or editing the LaunchAgent plist.
  - `~/.agent-factory/releases`, the service clone, the live SQLite store, the live mirrors under `~/.agent-factory/mirrors`, and pausing or resuming the factory.
  - Fast-forwarding or rebuilding `/Users/paul/codagent/agent-runner` or `agent-validator`.
  - Leaving uncommitted pins in `config/codagent.toml`.
  - Starting real model sessions, including running the `resolve-merge` agent step for real.
- **Permitted substitutes:**
  - A scripted resolution (resolve with git, then `git commit --no-edit`) or a hand-written `merge-stop.json` in place of the `resolve-merge` agent.
  - A stub `agent-validator` and fake `implement-result.json` where the real validator or CI would touch GitHub.
  - The e2e harness's stub `gh` and stub Runner for controller journeys.
- **Known risk areas:**
  - **Checkpoint misreads after a merge.** Walks sorted by date across merged history are the main regression risk, especially for continuations with `exclude_sha` and for squash-merged commit messages that carry trailers.
  - **Continuation edge cases.** A branch pushed at exactly the prior head (D17), and double renames in `continue-change.sh`.
  - **`skip_if` guard interactions** in `factory-feature` after a stop, a prior failure cluster (`seed-*-status` and `archive_status` steps): every later step must skip or keep its status defined.
  - **The finalize → verify lowering** and `restore-skipped-verify-status`.
  - **The fix/feature split in shared review and implementation workflows.** A fix round must be byte-for-byte unchanged in behavior.
  - **zsh on this Mac.** There is no `timeout`, and `set -- $var` does not split words. Scripts run under `/bin/sh`, but hand-driven exploration commands must account for this.
  - **Accepted limitations:**
    - The quality of the agent's conflict resolution is not tested live. It is mitigated by the validator, `check-merge`, and re-verification.
    - A rollback after deploy can misread checkpoints on branches that already merged `main` (see the design's migration plan).

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Feature: Resume and continue feature work | INT-002, INT-003, INT-004, INT-006 | E2E-001 | — |
| Feature: Archive the change before verification (archive unblock, reworded summary) | INT-001, INT-009 | — | — |
| Feature: Merge the target branch into the claim's branch on every resume | INT-001, INT-002, INT-003, INT-004, INT-005, INT-007 | E2E-001 | — |
| Review: Implement, verify, and push on the existing branch (merge-only rounds) | INT-008 | — | — |
| Review: Merge the target branch into a feature pull request before triage | INT-008, INT-009 | — | — |
| Reporting: review-round base merge disclosed in the completion comment | INT-008 | — | — |
| Issue acceptance 1: blocked archive, fix on main, unblock | INT-001 | — | — |
| Issue acceptance 2: conflicting prior branch ends resolved or `needs-input`, never fresh | INT-003, INT-004 | — | — |
| Issue acceptance 3: resume points unchanged by the merge commit | INT-006 | — | — |
