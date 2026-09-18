## Context

A fix claim settles when its attempt returns `pull-request`: lifecycle `settled`, verdict `pending-human-review`, card in Review. From then on `FixHandler.gesture` recognises only a drag to Ready (`fresh`, which supersedes the claim and opens a new PR) and the merge sync watches for the PR to merge. Nothing reads the PR. The blocked loop (`blocked.py`) already implements the pattern this change needs: scan on every poll, keep writer comments newer than a timestamp, reserve a run with a distinct reason, cut fresh clones, launch.

The packaged workflow `factory-fix-v1.0.yaml` is staged into `<evidence>/agent-runner/workflows/` and resolved by the sandboxed Runner by name. The Runner resolves sub-workflow references relative to the parent file, so sibling files staged into the same directory can be composed. Sub-workflows receive only the parameters passed to them; whether a sub-workflow's `capture` values are visible to the parent is unverified, so the design does not rely on it.

The fix credential (`GH_TOKEN`) is the identity that pushes and opens PRs inside the sandbox. Replying to and resolving review threads uses the same credential, so review replies appear as that identity. The controller's App token reads PR reviews and threads through GraphQL, which its existing Pull requests read permission covers.

## Approach

### Trigger: `work_kinds/fix/review.py`

Mirrors `blocked.py`. For each fix claim that is `settled`, or `blocked` with `outcome.blocked_by == "review"`, with a recorded PR and `reporting.sync` not complete, on each poll:

1. Evaluate the cheap SQLite gates (pause, slot free, window, quota holds, identical to `process_blocked_claim`) and remember the result; the scan continues so `waiting_review` can be recorded even when the slot is busy.
2. `client.get_pull_request` → skip unless state is open.
3. `client.list_review_activity(repository, pr_number)` (new, GraphQL): reviews (state, body, author, submittedAt), review threads (id, isResolved, path, line, comments with id, author, body, createdAt), and PR conversation comments (issue comments on the PR number, via the existing REST listing).
4. Eligibility: author is a writer (permission cache per cycle, lookup failure → ineligible), author is not `bot_login`, body non-empty, `created_at > review_checkpoint`, and for thread comments the thread is unresolved. `APPROVED` with empty body is ignored by the non-empty rule.
5. No eligible input → return. Otherwise record `waiting_review` status text on the claim for `status`, and if admission gates pass: reconcile (open PR is expected here and does not settle, unlike first launch), fetch the mirror, resolve the PR head commit in the mirror, `prepare_clones` with the target checked out on the PR branch at that head, `reserve_run(reason="review")`, set `outcome.review_checkpoint` to now, lifecycle `active`, `set_attention_label(False)`, event `review:<run>` naming the comments.

The review checkpoint lives in `claim.outcome["review_checkpoint"]` (ISO timestamp). It is initialised lazily to the latest attempt's completion time when absent, so claims settled before this change do not replay old comments. It is advanced at reservation, before launch, so a technical failure cannot replay the same comments; the recovery retry reuses the same `review.json`.

`review.json` is written by `plan()` from `preparation.payload["review"]`; `plan()` branches on `run.reason == "review"` to choose the workflow name, the input file name, the outcome file name, and the branch parameter. The launch script gains a `--workflow` and `--input` pair instead of hard-coded names.

Ordering: `runtime.cycle` calls the review scan in the per-card loop right after `sync_claim`, before the admission loop, so an eligible review round takes the slot before a new Ready bug in the same cycle.

### Gesture and lifecycle

`Gesture` gains `"review"`. `FixHandler.gesture` returns it for a settled claim with eligible PR comments; the runtime's report step skips the column correction for that cycle, as it does for `fresh`. Run reasons: `initial`, `recovery`, `unblock`, `review`. `next_unit` and `_needs_recovery` are unchanged; a review attempt that fails technically gets the one recovery retry from fresh clones at the same PR head.

`classify`/`settle` read `review-outcome.json` when `run.reason == "review"`. `needs-input` from a review round sets lifecycle `blocked` with `declined_at` and `blocked_by: "review"`; the verdict held at admission is saved as `outcome.pre_review_verdict` when the round is reserved, and `presentation` returns Review with that verdict plus the label for that case, and `process_blocked_claim` is skipped for it. The decline advances the review checkpoint, and the review scan re-admits the claim on a PR comment newer than that checkpoint. The claim-lifecycle correction rule is split by `blocked_by` (see the `factory-claim-lifecycle` delta): bug-triage blocks are restored to Running, review blocks to Review.

### Workflows

Three packaged files plus scripts under `work_kinds/fix/workflow/`:

```
factory-fix-v1.0.yaml        # factory-contract: factory-fix/1  (interface unchanged)
  check-contract, check-clean-tree, triage, record-triage, create-branch
  implement: workflow: factory-implement-v1.0.yaml  params: input_file, plan_json, result_path
             skip_if not fixable, continue_on_failure
  record-outcome (reads /artifacts/implement-result.json), verify-outcome

factory-review-v1.0.yaml     # factory-contract: factory-review/1
  check-contract
  check-branch      git branch --show-current == branch_name, HEAD == head_sha from review.json, clean tree
  triage            lead: per-comment decisions JSON (see below), capture review_decision
  record-triage     script: needs_input → review-outcome.json needs-input; captures
                    needs_input and changes_needed; aggregates item plans into plan_json
  implement         workflow: factory-implement-v1.0.yaml  params: input_file, plan_json,
                    result_path   skip_if needs_input == true || changes_needed != true
  respond           lead: reply per item and resolve handled threads; capture respond_report
                    skip_if needs_input == true
  record-outcome    script: implement-result.json + respond_report → review-outcome.json
                    skip_if needs_input == true
  verify-outcome

factory-implement-v1.0.yaml  (shared, no contract line)
  params: input_file, plan_json, result_path
  fix (implement-with-tdd on plan_json), run-validator, check-validator, test-flows,
  read-regression-marker, address, recheck-validator, verify-clean, finalize-pr
  (ci_fix_cycles=1), mark-ci-failed, record-pr-details, annotate-pr,
  write-result: {validator, ci, pr_details} → result_path
```

`write-result` is the parent-visible channel: the sub-workflow writes `/artifacts/implement-result.json` and each parent's `record-outcome.sh` reads it. This sidesteps the unverified capture propagation. `annotate-pr` already exits early when the PR body carries the claim marker, so it is a no-op for review rounds.

Review triage JSON:

```json
{"needs_input": [],
 "items": [{"id": "<thread or comment id>", "source": "thread|review|comment",
            "decision": "change|answer", "reply": "…", "plan": "…"}]}
```

`changes_needed` is true when any item is `change`. The `respond` step reads the same decision plus `implement-result.json`: for each item it posts the reply (thread reply via GraphQL `addPullRequestReviewThreadReply`, otherwise `gh pr comment`) and resolves the thread with `resolveReviewThread` when the item is `answer`, or `change` and the push succeeded. It runs under the fix credential already configured in the sandbox.

Review outcome contract:

```json
{"contract": "factory-review/1",
 "outcome": "pull-request" | "needs-input" | "failed",
 "reasons": [], "pr": {...}, "validator": {...}, "ci": {...},
 "answered": ["<id>"], "changed": ["<id>"]}
```

### Readiness

`launch.check_runner_contract` checks both contract lines; `packaged_workflow_text` takes the file name. `doctor` prints one line per contract. `status` lists settled claims whose last scan found eligible comments (`outcome.waiting_review`).

## Decisions

- **Shared sub-workflow, not a mode parameter.** The triage prompts and JSON shapes differ; a single workflow would need two guarded triage steps capturing the same variable. Paul chose the shared sub-workflow.
- **PR-side comments only.** The issue stays the board handle; the PR is the review surface. Issue comments on a settled claim remain inert. Paul's choice.
- **Reply and resolve.** The agent resolves threads it handled, so `finalize-pr`'s `wait-ci` does not see answered questions as actionable feedback and loop. A reviewer's reply unresolves the thread and triggers the next round. Paul's choice.
- **Checkpoint advanced at reservation.** A comment launches at most one round; the recovery retry reuses the frozen input.
- **Review rounds before new bugs.** Finishing work in flight beats starting new work, and the reviewer is waiting.
- **Same branch, same PR, recorded Runner and Skills commits.** The round continues the claim; only the target checkout moves to the PR head. No new claim, no supersede.
- **Requested scope widening is allowed.** In a review round the writer is the authority; only spec-level or cross-repository changes and genuine open decisions return `needs-input`.
- **`needs-input` from a review round returns the card to Review.** The PR exists and a human is already engaged there; moving to Running would misreport.
- **File-based result from the sub-workflow.** Avoids depending on capture propagation across workflow boundaries.

## Risks / Trade-offs

- **Reply loops.** The factory's own replies are excluded by author, and resolved threads are ineligible, so the agent cannot re-trigger itself. A reviewer who replies to every answer keeps the loop going; that is a human-paced loop and unbounded by design.
- **GraphQL mutations from the sandbox.** Resolving threads and replying need the fix credential to have Pull requests write; the existing PAT scope covers it. Doctor does not verify mutation rights, only read.
- **Claims settled before the upgrade.** The lazy checkpoint means only comments after the last attempt's completion count; a review left before the upgrade on an older PR is not replayed. Documented.
- **Fix-workflow refactor.** Extracting the sub-workflow touches the live fix path. The existing `test_fix_workflow.py` and the Docker e2e launch test are the regression net; the `factory-fix/1` contract line and outcome shape stay identical.
- **Capture propagation unknown.** If the Runner does propagate sub-workflow captures, the file channel is redundant but harmless.

## Migration

No schema migration. `review_checkpoint`, `blocked_by`, and `waiting_review` are keys inside the existing JSON `outcome` column. Deployment: pause, install, `doctor` (now checks two contracts), resume. Documentation gains a "review loop" section beside the blocked-bug loop.
