# Task: Blocked-bug loop, fix drag gestures, and post-merge working-clone sync

## Goal

Complete the fix lifecycle after an attempt ends: re-admit a blocked bug when a repository writer comments or a human drags the card to Ready, recognize a drag of a settled fix card to Ready as a fresh-attempt request, correct contradictory drags of blocked cards, exempt settled fix claims from closure cancellation, and after the factory PR merges, safely update the operator's working clone and close the issue so the card reaches Done, with clones and images removed on the next poll.

## Background

All paths are in `agent-factory`. Planning sources are `openspec/changes/pickup-and-fix-bugs/proposal.md`, `design.md` (sections "Cycle", "Blocked loop", "Merge sync", "Decisions: Blocked stays in Running", "Dry-run merges and fetch-into-branch", "Writer comments only unblock", and "Risks: Sync race"), the specs under `specs/`, and `test-plan.md`.

Preconditions in the repository: `WorkKindHandler` in `src/agent_factory/work_kinds/base.py` with `EvalHandler` and `FixHandler` registered in `src/agent_factory/work_kinds/__init__.py`; `FixHandler` (under `src/agent_factory/work_kinds/fix/`) admits bugs, freezes `revisions.{target,runner,skills}`, reconciles side effects before reserving, launches the sandboxed workflow, stores the parsed `fix-outcome.json` (with `pr.url`, `pr.number`, `branch`) on the run, sets lifecycle `blocked` with presentation Running + label `needs-input` on `needs-input`, and settles `pull-request` and `failed` claims to Review; `FixHandler.gesture` currently returns `None`; `ClaimStore` is at schema v4 with the `blocked` lifecycle, `reserve_run(..., reason=...)`, `supersede_and_create`, `set_claim_lifecycle`, and per-claim `reporting` progress; `runtime.cycle` runs consume → reconcile per card → report → cleanup on Done → admit per kind, and applies `ClaimPresentation.labels` through `set_attention_label` with delivery receipts; `GitHubClient.list_comment_records` returns author login and `created_at`; `LocalConfig.repositories.working_clones` maps `owner/repo` to the operator's clone path. `EvalHandler.gesture` recognizes only the cleared-Verdict fresh-request rule and must keep doing so.

### Blocked loop

Each poll, for claims in lifecycle `blocked`: call `list_comment_records`, keep comments with `created_at` after the decline comment's timestamp (the decline event's acknowledged comment) whose author has writer permission (`get_permission` in `{write, maintain, admin}`, cached per login for the cycle); the factory's own identity (`github.bot_login`) never counts. If any eligible comment exists, or the card has been moved to Ready: present label off, `reserve_run(reason="unblock")` for unit `fix`, and start a new attempt whose `issue.json` includes the eligible comments present at that time (the handler's existing input builder takes the comments). Non-eligible comments are ignored entirely and do not change state. A blocked claim holds no slot; the unblock attempt goes through the normal per-kind admission (slot, window, holds, reconciliation) and if the slot is busy the claim stays blocked with the label already removed only once the attempt is reserved.

### Gestures and corrections

`FixHandler.gesture(claim, card, comments)` returns `fresh` when a settled fix claim's card is in Ready, and `unblock` when a blocked claim's card is in Ready. The runtime handles `fresh` through the existing `supersede_and_create` path with re-resolved commits (`accept` again), retaining prior claim, attempt, and PR history, and the new attempt's input includes the eligible comments and any prior factory PR. The generic correction rule already reads: if the card's column differs from the presented status, restore it and comment once, except where `gesture()` recognizes the drag. For a blocked fix claim that means Review or Done is corrected back to Running with the label intact and one correction comment; Ready is an unblock; Running is left alone. Ordinary queued-idle eval correction is unchanged.

### Closure exemption

`Controller.cancel` and the runtime's closure check cancel only claims with unfinished execution. A settled fix claim with a recorded PR is never cancelled by closure and no cancellation comment is posted; after the factory closes the issue itself, later polls record nothing new.

### Merge sync

`src/agent_factory/work_kinds/fix/sync.py` (or similar). For settled fix claims whose stored outcome has a PR and whose `reporting.sync` is not complete, regardless of the card's column: `gh pr view <number> --repo <repo> --json state,mergedAt` (add `get_pull_request` to `GitHubClient`). When merged, in `repositories.working_clones[<repo>]`, in this exact order:

```
git status --porcelain --untracked-files=no      → non-empty ⇒ block("uncommitted changes")  (before any fetch)
current=$(git branch --show-current)             → empty ⇒ block("detached HEAD")
if current == main: git fetch origin && git merge --ff-only origin/main   → failure ⇒ block("local main diverged")
else: git fetch origin main:main                 → refuses a non-fast-forward or a main checked
                                                   out in any worktree ⇒ block with git's reason
      git merge-tree --write-tree HEAD refs/heads/main   → exit 1 ⇒ block("conflicts")
      git merge --no-edit main                            → failure ⇒ git merge --abort; block
```

Untracked files count as clean. Success: close the issue if still open (`gh api repos/<repo>/issues/<n> --method PATCH -f state=closed`; add `close_issue` to `GitHubClient`) with a marker comment; closure automation moves the card to Done. Block: label `needs-input` on unless the card is already Done, a reason comment with a stable marker per reason, retry every poll; a later success removes the label. Record `reporting.sync` on the claim (attempted, blocked reason, completed) so a restart never repeats a completed merge. A missing clone blocks with that reason. The guarantee is that the working tree, index, and checked-out branch change only by the merge itself; fetched refs and objects may change. The dirty check and the merge are not atomic; this is accepted for a single-operator clone.

`operations.status` should be able to print pending syncs with their last failure reason from `reporting.sync`; expose the data even if the per-kind status rendering is delivered elsewhere.

### Cleanup

The runtime's Done-triggered cleanup calls `FixHandler.cleanup`, which removes the claim's recorded clones and run image tags and persists progress and failures for retry; mirrors, shared checkouts, evidence, and other claims' clones are never touched. Clones and images remain while the claim is running, waiting, blocked, or in Review.

Constraints: no auto-merge, no reaction to review comments or CI on the PR, no branch or PR cleanup, no LLM involvement, no eval behavior change. Keep `pyright` strict and `ruff` clean.

## Spec

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-bug-intake/spec.md`.

### Requirement: Recognize fix retry gestures

A fix claim that ended with a pull request, a failed outcome, or exhausted recovery is settled. Moving a settled fix card from Review back to Ready SHALL request a new claim using current branch heads while retaining prior claim, attempt, and PR history; the new attempt's input SHALL include the issue's eligible comments and any prior factory PR. A blocked fix claim SHALL be re-admitted when an eligible comment newer than the decline appears on the issue, or when a human moves its card from Running to Ready; in either case the factory SHALL remove the `needs-input` label and start a new attempt whose input includes the eligible comments present at that time. An eligible comment is one authored by a user with write, maintain, or admin access to the repository; comments by the factory's own identity and by other users SHALL be ignored as input and SHALL NOT trigger re-admission. The eval fresh-request gesture is unchanged and separate.

#### Scenario: Drag a settled fix back to Ready

- **WHEN** a human moves a fix card whose claim is settled from Review to Ready
- **THEN** the factory creates a new claim with re-resolved commits, keeps the earlier claim history, and does not treat the drag as a contradictory status edit

#### Scenario: Answer a blocked bug

- **WHEN** a writer comments on a blocked bug after the decline comment
- **THEN** the next poll removes the `needs-input` label and admits a new attempt when the fix slot and holds permit
- **AND** the new attempt's input includes the writer's comment

#### Scenario: Drag a blocked bug to Ready

- **WHEN** a human moves a blocked bug card from Running to Ready without commenting
- **THEN** the next poll removes the `needs-input` label and admits a new attempt with the eligible comments already on the issue

#### Scenario: Post a factory comment on a blocked bug

- **WHEN** the factory delivers one of its own comments to a blocked bug
- **THEN** the bug remains blocked and no attempt starts

#### Scenario: Receive a comment from a non-writer

- **WHEN** a user without write access comments on a blocked bug
- **THEN** the bug remains blocked and the comment is not supplied to any attempt

#### Scenario: Edit a running bug

- **WHEN** the issue body or comments change while a fix attempt is executing
- **THEN** the running attempt is unaffected and no overlapping attempt starts

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-claim-lifecycle/spec.md`.

### Requirement: Keep lifecycle behavior independent of work kind (gesture portion)

#### Scenario: Read a kind-specific gesture

- **WHEN** a human drags a settled fix card from Review to Ready
- **THEN** the controller asks the fix handler, which treats it as a fresh-attempt request
- **AND** an eval card dragged the same way keeps its existing correction behavior

### Requirement: Correct status edits that contradict factory execution

For factory-owned requests, the factory SHALL reconcile Project Status against saved lifecycle state, verified execution, and available results on each successful GitHub poll. Moving an idle request to Running SHALL NOT establish that execution exists or bypass normal admission; the factory SHALL restore the appropriate queued or handoff status according to its lifecycle. A blocked fix claim SHALL be an exception: its card remains in Running with the `needs-input` label without execution and SHALL NOT be moved to a queued status; a blocked card moved to Review or Done SHALL be restored to Running, while a blocked card moved to Ready SHALL be treated as the fix handler's unblock gesture. Moving a request with verified active execution to Ready, Review, or Done SHALL restore Running and continue that same execution without restarting it or admitting overlapping work. Such status changes SHALL NOT cancel execution. Issue closure SHALL retain its defined cancellation behavior and take precedence over restoring Running. A drag that the request's work-kind handler recognizes as a fresh-attempt gesture SHALL be honored rather than corrected.

The factory SHALL leave status edits on cards without factory ownership alone. Corrective updates SHALL include a brief issue comment explaining the actual state and correction, using durable reporting to avoid repeating the same correction comment on each poll. The default correction interval SHALL be the normal five-minute poll; unavailable GitHub access SHALL delay the correction rather than change execution state based on an unverified board observation.

#### Scenario: Leave a blocked fix in Running

- **WHEN** a fix claim is blocked awaiting input and its card sits in Running with the `needs-input` label
- **THEN** the poll does not move the card or post a correction

#### Scenario: Move a blocked fix to Review

- **WHEN** a human moves a blocked fix card from Running to Review or Done while its issue remains open
- **THEN** the next successful poll restores Running with the label intact and explains the correction once

#### Scenario: Move an idle request to Running

- **WHEN** a human moves a factory-owned queued request to Running while it has no active execution
- **THEN** the next successful poll restores its appropriate queued status
- **AND** the drag itself creates no execution attempt and bypasses no admission control
- **AND** the factory briefly explains the correction on the issue

### Requirement: Exempt settled work from closure cancellation

Issue closure SHALL cancel only claims with unfinished execution. A settled fix claim with a recorded pull request SHALL remain eligible for its post-merge sync whether the issue was closed by a human or by the factory after a successful sync, and closure SHALL NOT be reported as a cancellation for such a claim.

#### Scenario: Close a fixed issue by hand before the sync

- **WHEN** a human closes an issue whose fix claim is settled with a merged PR before the factory has synced
- **THEN** the claim is not cancelled and the sync still runs once

#### Scenario: Observe the factory's own closure

- **WHEN** the factory closed the issue after a successful sync and observes the closed issue on a later poll
- **THEN** it records nothing new and posts no cancellation comment

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-fix-reporting/spec.md`.

### Requirement: Comment on fix activity (sync portion)

The factory SHALL comment on the issue [...] when a post-merge sync succeeds or is blocked. Comments SHALL carry stable markers and SHALL NOT repeat for unchanged state.

### Requirement: Sync the working clone after merge

For a settled fix claim with a recorded factory PR that has been merged and whose sync has not completed, regardless of the card's current column, the factory SHALL update the operator's configured working clone of that repository on the next successful poll: verify the working tree and index have no changes to tracked files, fetch `main` from the remote into the local `main` branch (refusing when local `main` has diverged or is checked out in any worktree of that clone), verify by dry run that merging `main` into the currently checked-out branch produces no conflicts, and then perform that merge. On success it SHALL close the issue when it is still open so closure automation moves the card to Done. On tracked changes, a diverged local `main`, `main` checked out elsewhere, a predicted conflict, a detached HEAD, or an unreachable clone, it SHALL leave the card where it is, apply the `needs-input` label when the card is not yet Done, comment with the reason, and retry on later polls, clearing the label when the sync succeeds. Apart from fetched refs and objects, it SHALL NOT change the clone's working tree, index, or checked-out branch except by the merge itself. A merged PR whose issue a human already closed SHALL still receive its one sync attempt sequence.

#### Scenario: Merge a fix while the clone is on dev

- **WHEN** a factory PR merges while the working clone has branch `dev` checked out and a clean tree
- **THEN** the factory updates local `main`, merges it into `dev`, closes the issue, and the card moves to Done

#### Scenario: Merge a fix while the clone is dirty

- **WHEN** the working clone has uncommitted changes
- **THEN** the card stays in Review with the `needs-input` label and a comment naming the uncommitted changes
- **AND** the clone's working tree, index, and branches are unchanged

#### Scenario: Predict a conflict

- **WHEN** the dry-run merge reports conflicts
- **THEN** the factory performs no merge, leaves the working tree unchanged, and blocks the card with the conflict reason

#### Scenario: Find local main diverged or checked out elsewhere

- **WHEN** local `main` has commits not on the remote, or `main` is checked out in another worktree of the clone
- **THEN** the factory refuses to update `main`, blocks the card with that reason, and changes no branch

#### Scenario: Merge after a human closed the issue

- **WHEN** a human closed the issue before the factory observed the merged PR and the card is already Done
- **THEN** the factory still performs the sync once and comments on the outcome without relabeling the Done card

#### Scenario: Resolve and retry

- **WHEN** the operator commits or stashes the changes and the next poll's checks pass
- **THEN** the factory completes the merge, removes the `needs-input` label, closes the issue, and the card moves to Done

### Requirement: Deliver fix reports durably without duplicates

Fix reporting SHALL use the same per-claim reporting progress, stable markers, lost-response discovery, and restart survival as eval reporting. Pending delivery SHALL NOT rerun an attempt or repeat a sync.

#### Scenario: Lose a PR comment response

- **WHEN** GitHub accepts the PR-link comment but the response is lost
- **THEN** reconciliation finds it by marker and completes the board update without a second comment

Source: `openspec/changes/pickup-and-fix-bugs/specs/factory-operations/spec.md`.

### Requirement: Clean up worktrees after review

On the next successful poll after a reviewed factory-owned item moves from Review to Done, the factory SHALL remove that item's factory-owned Runner, Skills, and evals worktrees, or for a fix its per-attempt clones and run-specific images. It SHALL preserve results, logs, SQLite history, candidate branches, PRs, and mirrors. Worktrees and clones SHALL remain available while work is running, waiting, blocked, or in Review. Reconciliation of verified running work dragged to Done SHALL restore Running before cleanup is considered; that edit SHALL NOT remove worktrees.

The factory SHALL persist cleanup progress and failures, retry incomplete cleanup on later polls, and continue processing other jobs. Repeated cleanup and controller restarts SHALL tolerate already-removed owned worktrees. Cleanup SHALL operate only on recorded factory-owned worktrees, clones, and images and SHALL NOT remove shared source checkouts, the operator's working clones, or another item's worktrees.

#### Scenario: Finish review and release worktrees

- **WHEN** a reviewed item moves from Review to Done and the factory next successfully polls GitHub
- **THEN** its factory-owned worktrees or clones and run-specific images are removed
- **AND** results, logs, SQLite history, candidate branches, and PRs remain available

#### Scenario: Preserve worktrees still in use

- **WHEN** an item is running, waiting, blocked, or in Review, including an active item incorrectly dragged to Done
- **THEN** its worktrees or clones remain available for execution, recovery, and human judging
- **AND** a contradictory Done edit follows the existing Running correction policy

#### Scenario: Retry incomplete cleanup

- **WHEN** removal of a reviewed Done item's worktrees fails or the controller restarts partway through cleanup
- **THEN** the factory records the remaining cleanup and retries on later polls without blocking other jobs
- **AND** already-removed worktrees do not cause a new failure or affect retained evidence

## Test Plan

- `INT-004` (Merge sync against real Git working clones): the sync module against real temporary Git repositories: a bare origin, a factory-merged `main`, and a working clone prepared as: `dev` checked out with a clean tree; `dev` with a tracked modification; `dev` with a staged change only; `dev` with an untracked file only; `dev` whose history conflicts with `main`; `dev` while local `main` has an unpushed commit; `dev` while `main` is checked out in a second linked worktree of the same clone; `main` checked out; detached HEAD; missing directory. Assert clean `dev` → local `main` equals `origin/main`, `dev` contains the merge commit, success; tracked or staged modification → blocked "uncommitted changes" before any fetch with no new objects or refs and tree, index, and branches unchanged; untracked-only → clean; diverged local `main` → blocked, `main` still at the local commit; `main` in another worktree → blocked, both worktrees unchanged; conflict → blocked "conflicts", no merge started, `MERGE_HEAD` absent, tree unchanged; `main` checked out → fast-forwarded; detached HEAD or missing clone → blocked with that reason; a second run after the operator resolves the block succeeds. Execution: `tests/integration/test_merge_sync.py`; `uv run pytest`; Git required, no network.
- `INT-006` (Blocked loop, comment eligibility, and kind-specific gestures): `FixHandler.gesture`, the runtime's blocked-claim scan, and side-effect reconciliation over the controlled GitHub client. Setup: a blocked fix claim with a decline comment at T0; comments after T0 from a writer, a non-writer, and the bot; a blocked claim dragged to Ready with no new comment; blocked claims dragged to Review and to Done; an ordinary waiting eval claim dragged to Running; a settled fix claim dragged to Ready; a settled eval claim dragged to Ready with its verdict intact; stub responses for an existing factory branch and an open factory PR. Assert writer comment → label removed, one run reserved with reason `unblock`, `issue.json` contains only writer comments; bot or non-writer comment alone → nothing changes; blocked card in Ready → unblock with existing comments; blocked card in Review or Done → restored to Running with one correction comment and the label intact; blocked card in Running → no correction; waiting eval card in Running → restored to Ready as today; settled fix card in Ready → new claim via supersede with re-resolved revisions and prior history retained; eval card in Ready with old verdict → existing behavior; existing open factory PR → claim settled as PR without a launch; lookup failure → no launch, retried next cycle. Execution: `tests/integration/test_fix_gestures.py`; `uv run pytest`.
- `E2E-002` (Fix journey through the CLI with controlled sandbox and GitHub): installed CLI (`tick`, `status`) with an isolated root and SQLite; real temporary Git remotes for a target repository, Runner, and Skills; a controlled `sandbox-run.sh` in the Runner checkout that records its arguments, mounts, and environment names and writes a scripted `fix-outcome.json`; a `docker` stub implementing `info`, `stats`, `ps`, `inspect`, and `rmi`; the local GitHub stub serving cards, comments, permissions, PR lookups, and issue close; a working clone of the target on `dev`. Journey: route a writer Bug to Ready; tick to admit; verify mirror fetch and clones at the recorded commits; the stub writes `pull-request`; tick to observe Review and the PR link; the stub marks the PR merged; tick to observe the sync into `dev`, the issue close, and Done; tick to observe clone and image removal. Second pass: stub crashes after "opening" a PR without an outcome; reconciliation finds the PR and settles without a relaunch. Third pass: stub writes `failed`; Review with `failed`. Fourth pass: advance the target branch after admission and force a technical failure; the retry clones the original commits. Fifth pass: a human closes the issue before the merged PR is observed; the sync still runs once and no cancellation is recorded. Sixth pass: with a real `agent-evals` checkout available, run the real and-scene `run.sh --dry-run` from an eval plan whose environment sets `IMAGE`, and assert the printed build command carries the per-run tag. Assert argv contains `--image agent-runner-factory:<run>`, `--no-default-secrets`, the read-write repo mount, the read-only Skills mount, and an env file whose only variable is `GH_TOKEN`; the App token value appears nowhere; `issue.json` matches the issue and eligible comments; Refs renders `target@ runner@ skills@`; board and label transitions match the reporting spec; `dev` contains the merge commit and is otherwise untouched; clones and the image tag are removed only after Done and mirrors remain; claim, run, and event history are intact across passes. Execution: `tests/e2e/test_fix_cycle.py`; `uv run pytest`; Git required, no network or Docker.

## Done When

- Blocked claims are re-admitted only on an eligible writer comment newer than the decline or a drag to Ready; the label is removed, the attempt is reserved with reason `unblock`, and its `issue.json` carries only eligible comments; bot and non-writer comments change nothing.
- `FixHandler.gesture` returns `fresh` for a settled card in Ready and `unblock` for a blocked card in Ready; the runtime supersedes with re-resolved commits and retained history; blocked cards in Review or Done are restored to Running with one comment and the label intact; eval corrections are unchanged.
- Closure cancels only claims with unfinished execution; a settled fix claim with a PR still receives one sync sequence and no cancellation comment.
- The merge sync implements the exact command order above, blocks with the specified reasons without touching the tree, index, or branches, closes the issue on success with a marker comment, records `reporting.sync` so a completed merge never repeats, and clears `needs-input` on later success.
- Cleanup removes the claim's clones and image tags only after Done, persists and retries failures, and leaves mirrors and evidence.
- INT-004, INT-006, and E2E-002 pass; `uv run pytest`, `uv run ruff check .`, and `uv run pyright` pass; existing eval tests still pass.
