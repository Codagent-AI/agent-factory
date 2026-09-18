## Why

A factory fix ends when its pull request is opened and the card moves to Review. From then on the only gesture the factory understands is a drag back to Ready, which starts a brand-new claim and a brand-new PR. Reviewer feedback on the PR itself, whether a request for changes or a question, goes unanswered until a human does the work by hand. The factory should treat a writer's review comment as work: pick the issue up again and run an agent that addresses the comment on the existing PR.

## What Changes

- Add a review trigger to the poll loop: a writer's PR-side comment (inline review thread, review summary, or PR conversation comment) newer than the claim's review checkpoint on a settled fix claim with an open factory PR re-admits the claim through the fix slot as a new attempt with reason `review`.
- Ship a second packaged workflow, `factory-review/1`, launched in the same sandbox on a clone of the PR branch at its head. Its triage decides per comment whether code changes are needed or an answer suffices, deciding itself when ambiguous. Changes go through the existing implement, validator, test-flows, and finalize-pr steps. The agent replies in each thread and resolves the threads it handled.
- Extract the common implementation steps of the fix workflow into a shared sub-workflow used by both packaged workflows, leaving the `factory-fix/1` contract unchanged.
- Map review outcomes to the board: Running during the round, back to Review with `pending-human-review` on success, `failed` with the PR left open on a red validator or CI, and `needs-input` (card returns to Review with the label applied) when a human decision is genuinely required.
- Extend readiness, doctor, status, and documentation to cover the second contract and the review loop.

## Capabilities

### New Capabilities
- `factory-review-intake`: detect eligible PR-side comments on settled fix claims, checkpoint what has been seen, and re-admit the claim ahead of new bugs.
- `factory-review-execution`: the packaged review workflow, its input file, its per-comment triage, thread replies and resolution, and its structured outcome.

### Modified Capabilities
- `factory-bug-intake`: the settled-claim gesture rule gains the PR-comment trigger; issue comments on a settled claim stay ignored; drag to Ready remains the fresh-claim gesture.
- `factory-fix-execution`: the factory ships and stages three workflow files, checks both contract lines, and clones the PR branch for a review attempt.
- `factory-fix-reporting`: review-round outcomes and their board mapping, including `needs-input` from a review round, which keeps the card in Review.
- `factory-operations`: doctor checks the review contract; status shows review rounds; documentation covers the review loop.
- `factory-claim-lifecycle`: the blocked-card correction rule distinguishes a claim blocked by bug triage (stays in Running) from one blocked by a review round (stays in Review).

## Out of Scope

- Comments from non-writers, from the factory's own identity, and review bots that are not writers.
- Issue comments while a fix card is in Review (unchanged: ignored until a drag to Ready).
- Review rounds for eval claims, for PRs the factory did not open, or after the PR is merged or closed.
- Auto-merge, approving reviews, or dismissing reviews.
- Changing the Runner's built-in `finalize-pr`, `wait-ci`, or `fix-pr` behaviour.

## Impact

- `work_kinds/fix/`: new `review.py` scan (mirrors `blocked.py`), `launch.py` stages three files and checks two contracts, `workspace.py` clones the PR branch for review attempts, handler gains the `review` gesture, run reason, outcome reader, and presentation.
- `github.py`: read PR reviews, review threads with resolution state, and PR conversation comments through GraphQL.
- `work_kinds/fix/workflow/`: `factory-fix-v1.0.yaml` shrinks, `factory-implement-v1.0.yaml` and `factory-review-v1.0.yaml` are added with their scripts.
- `store.py`: claim outcome gains `review_checkpoint`; run reason gains `review`. No schema migration.
- `operations.py`, `docs/operations.md`, `docs/installation.md`: doctor, status, and documentation.
- The fix credential (`GH_TOKEN`) must be able to reply to and resolve review threads; the existing Pull requests write scope covers this.
