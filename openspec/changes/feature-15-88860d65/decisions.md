# Decisions: feature-15-88860d65

## Step: propose

### D1. Verdict: go, with a caveat

- **Decision:** Go. The caveat is that `registry.fly.io` support for manifest DELETE is
  unverified and must be probed during design.
- **Alternatives considered:**
  - No-go: rejected, because disk exhaustion has already blocked admission.
  - Split the change into a Mac part and a Fly part: rejected, because the issue scopes them
    together and the Fly part can ship even if deletion turns out to be unsupported.
- **Decision-bearing:** yes

### D2. Eligibility outside Done uses an idle clock from the terminal lifecycle

- **Decision:** A claim that is settled, cancelled, or superseded becomes eligible for cleanup
  after `[limits] terminal_retention_days` has passed since the factory first durably observed it
  in that lifecycle, whatever its card status. The clock resets if the claim becomes active
  again. The existing Done rule is kept.
- **Alternatives considered:**
  - Use `claim.updated_at`: rejected, because every cleanup write changes it.
  - Clean up immediately when the claim turns terminal: rejected, because it would remove the
    evidence a human needs to review a settled claim in Review.
  - Clean up only under disk pressure: rejected, because it is harder to predict and the issue
    asks for retention.
- **Decision-bearing:** yes

### D3. Default idle period is 30 days

- **Decision:** 30 days, twice the 14-day Done evidence period, and configurable.
- **Assumption:** This leaves enough time for human review of evals parked in Review while still
  bounding growth.
- **Alternatives considered:** 14 days (the same as Done) and 60 days.
- **Decision-bearing:** yes. The value can be tuned later through configuration.

### D4. Idle terminal claims release their workspaces as well as their evidence

- **Decision:** Idle terminal claims have both their workspaces (worktrees, clones, local images,
  credential copies) released and their evidence pruned. This includes superseded claims, whose
  workspaces are never released today.
- **Alternatives considered:** Prune evidence only. Rejected, because clones and worktrees account
  for about 2 GB of the reported usage.
- **Decision-bearing:** yes

### D5. Active, waiting, and blocked claims are never touched

- **Decision:** Active, waiting, and blocked claims are the "active or resumable" claims the issue
  says to preserve, so they are never touched.
- **Evidence:** Settled fix and feature claims in Review get fresh clones for each review round
  (`prepare_review`), so releasing their old clones is safe.
- **Decision-bearing:** no. This follows directly from the issue.

### D6. A claim's Fly tag is deleted once its image can no longer be needed, without waiting for the idle period

- **Decision:** The tag is deleted once the claim is terminal, has no non-terminal run, and has no
  Machine record or stop hold. The factory deletes only the claim's recorded digest, and only
  after confirming the tag still resolves to it. Registry failures are retried on later polls. A
  missing manifest counts as done. A registry that refuses deletion is recorded as unsupported
  instead of retried forever. Tag cleanup runs in the per-claim cleanup path, never in Machine
  dispose or reconcile.
- **Alternatives considered:**
  - Wait for the idle period: rejected, because a terminal claim never needs its image again.
  - Sweep the registry by listing tags: rejected, because it could delete tags the factory did not
    record.
- **Decision-bearing:** yes

### D7. No direction-level stop

- **Decision:** No direction-level stop is needed.
- **Reasoning:**
  - Replacing the Done-only pruning rule is exactly what the issue comment requests.
  - The configuration and cleanup state changes are additive.
  - Everything stays inside this repository.
- **Decision-bearing:** no

## Step: proposal review (proposal-review-findings.json)

Every finding was checked against the code and the live Mac. Each is logged below.

### PR-1 (structural): candidate worktrees dominate artifact disk

- **Status:** applied.
- **Evidence:** `du` over the artifact directory found 26 `.runtime/candidate-worktree`
  directories totalling 6.06 GiB of the 7.7 GiB. Each is a standalone clone with its own `.git`
  directory.
- **Decision:** Add `.runtime/candidate-worktree` to eval evidence pruning on both the Done path
  and the idle path.
- **Safeguard:** Pruning waits until the eval results capture has succeeded. The curated files,
  including `implementation.diff`, are kept in the results repository.
- **Alternatives considered:**
  - Remove only `node_modules` and `skills`: rejected, because the rest of the directory is small
    and a partial clone is not useful.
  - Keep the audited list unchanged: rejected, because it would leave the problem unsolved.
- **Decision-bearing:** yes

### PR-2 (significant): split the idle period by lifecycle

- **Status:** applied.
- **Decision:** This supersedes D3:

  | Key | Applies to | Default |
  |---|---|---|
  | `[limits] abandoned_retention_days` | cancelled and superseded claims | 3 days |
  | `[limits] settled_retention_days` | settled claims outside Done | 14 days |

- **Estimate:** Artifacts grew about 0.43 GiB/day on average over 2026-09-09 to 2026-09-27, with
  bursts of about 1.4 GiB/day. Retained evidence settles at about 6 GiB on average and about
  20 GiB during a sustained burst. The Mac has 43 GiB free.
- **Alternatives considered:**
  - Release cancelled and superseded claims immediately (0 days): rejected, because a short grace
    period lets an operator inspect a just-cancelled run.
  - A single 30-day period: rejected, as the finding argues.
- **Decision-bearing:** yes

### PR-3 (significant): terminal claims whose card left the board are never visited

- **Status:** applied.
- **Evidence:** `runtime.py` calls `retention.reconcile` and `handler.cleanup` only from inside
  `for card in cards`.
- **Decision:** Add a bounded pass, driven by the claim store, over terminal claims absent from
  the poll's board snapshot. It runs only after a successful poll.
- **Alternatives considered:** Keep off-board claims out of scope. Rejected, because the board is
  not the source of truth for who owns local disk.
- **Decision-bearing:** yes

### PR-4 (significant): the eval human-review handoff promises availability until Done

- **Status:** applied.
- **Evidence:** The handoff text lives in `suites/and_scene/__init__.py`. The requirements are in
  `factory-eval-reporting` ("Provide executable human-review instructions") and
  `factory-eval-execution` ("Preserve suite-owned evidence...").
- **Decision:**
  - Add both capabilities to Modified Capabilities.
  - The handoff states that the window ends at Done or at the settled retention period after the
    request settles, whichever comes first.
  - When the window lapses, the factory records an issue event.
- **Alternatives considered:** Print an exact expiry date in the handoff. Rejected, because the
  handoff is posted when a repetition completes, which can be before the request settles, so the
  date is not yet known.
- **Decision-bearing:** yes

### PR-5 (significant): Fly DELETE feasibility is unknown

- **Status:** applied, by probing now.
- **Probe, 2026-09-27:**
  - A DELETE to `registry.fly.io/v2/agent-factory-sandbox/manifests/sha256:000…` (a digest that
    does not exist) returned 404 `MANIFEST_UNKNOWN`, not 405 `UNSUPPORTED`.
  - A GET of the tags list returned 200 and listed six `claim-` tags.
- **Decision:** Keep the Fly part. A refusal is treated as a persistent failure that is recorded
  once, instead of a dedicated "unsupported" state machine.
- **Not done:** A real deletion was not tried, because it is destructive and irreversible.
- **Alternatives considered:** Drop the Fly part. Rejected, because the evidence shows the route
  is supported.
- **Decision-bearing:** yes. It updates D6.

### PR-6 (minor): cancelled evals never release their worktrees

- **Status:** applied.
- **Evidence:** `WorktreeCleanup.reconcile` returns early unless the claim is settled.
- **Decision:** The Why section and What Changes now name cancelled evals as well as superseded
  claims.
- **Decision-bearing:** no

### PR-7 (minor): status output

- **Status:** applied.
- **Decision:** `status` shows an aggregate: the count and bytes awaiting cleanup, the count
  pruned, and each failure listed individually.
- **Alternatives considered:** Per-claim lines behind a flag. Deferred to design.
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.
- **Reasoning:** Every finding was resolved inside this repository, and every persisted change is
  additive. Shortening the human-review window is exactly the behaviour the issue comment asks
  for, and the lapse is now visible on the card.
- **Decision-bearing:** no

## Step: spec

### S1. The settled idle path applies only while the card is outside Done

- **Decision:** A settled claim whose card is currently Done is judged only by the Done path.
- **Why:** The existing guarantee that moving a card to Done grants the full retention period,
  counted from that Done observation, still holds for whatever evidence remains.
- **Alternatives considered:** Apply the idle path whatever the card status. Rejected, because a
  claim settled long ago and just moved to Done would be pruned at once.
- **Decision-bearing:** yes

### S2. Pruning happens as soon as either path allows it

- **Decision:** Cancelled and superseded claims are pruned at 3 days after the terminal
  observation, even when their card is Done.
- **Alternatives considered:** Keep the 14-day Done path authoritative for cards in Done. Rejected,
  because nobody reviews these claims.
- **Decision-bearing:** no

### S3. Cancelled evals follow the idle rule

- **Decision:** A cancelled eval claim releases its worktrees after the abandoned retention period,
  as the proposal states. Cancelled fix and feature claims keep their immediate release at
  cancellation.
- **Alternatives considered:** Release cancelled evals immediately, like fixes. Rejected for this
  change, because the short grace period lets an operator inspect the harness worktree.
- **Decision-bearing:** no

### S4. Idle release and image deletion wait while a Fly Machine is recorded

- **Decision:** Neither idle release nor Fly image deletion happens while any Fly Machine record
  or quota-hold stop remains for the claim. Unconfirmed disposal therefore keeps both the local
  workspace and the image.
- **Decision-bearing:** no

### S5. A tag that resolves to a different digest is not deleted or retried

- **Decision:** When a `claim-` tag resolves to a digest other than the recorded one, nothing is
  deleted and the mismatch is recorded as a failure for the operator, with no automatic retry.
  Nothing else writes that tag, so a mismatch is anomalous.
- **Decision-bearing:** no

### S6. How the status space estimate is computed is left to design

- **Decision:** The status summary includes an estimate of the local space held by pending claims.
  How it is computed and cached is deferred to design, marked `deferred-to-design`, so that
  `status` stays fast.
- **Decision-bearing:** no

### S7. The human-review comment states the period in days, not a date

- **Decision:** The handoff comment gives the settled retention period in days, counted from when
  the request settles, rather than an exact date. The settle time is unknown when the comment is
  posted.
- **Decision-bearing:** no

### S8. The lapse event is posted only for claims that received a review command

- **Decision:** The "review window ended" event is recorded once per claim, and only when that
  claim received at least one human-review command.
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.
- **Validation:** `openspec validate feature-15-88860d65 --strict` passes.

## Step: design

### G1. The idle clock lives in `claim.cleanup` as `terminal_observed_at`

- **Decision:** The clock is recorded in `claim.cleanup["terminal_observed_at"]`, set and cleared
  in `retention.py`.
- **Alternatives considered:**
  - `claim.updated_at`: rejected, because every write changes it.
  - A new column: rejected, because it would need a schema migration.
- **Decision-bearing:** no

### G2. `cleanup["complete"]` is required on every lifecycle

- **Decision:** Pruning requires `cleanup["complete"]` for settled, cancelled, and superseded
  claims alike. Idle release sets the flag for cancelled and superseded claims, which replaces the
  exemption `_eligible` gives them today.
- **Decision-bearing:** no

### G3. Idle release is an `idle=` keyword on `WorkKindHandler.cleanup`

- **Decision:** Idle release is requested by a keyword on the existing handler method. Superseded
  claims now reach `handler.cleanup` before the loop's `continue`.
- **Alternatives considered:** A new protocol method. Rejected, because it adds surface and
  duplicates removal code.
- **Decision-bearing:** no

### G4. Spec change: no lapse event for claims whose card left the board

- **Decision:** The review-window event is not recorded for off-board claims. The eval reporting
  delta gains this rule and a scenario.
- **Why:** Events are delivered only through `_report`, which needs a factory-owned card. An
  undeliverable event would block pruning forever through the pending-events guard.
- **Decision-bearing:** no. It is a technical implication of existing reporting behavior.

### G5. The status space estimate is cached with the claim

- **Decision:** The estimate is measured during ticks, at most 2 claims per tick, and stored in
  `cleanup["size_estimate"]`. `status` only reads it. This resolves the deferred-to-design marker,
  and the operations spec now says so.
- **Decision-bearing:** no

### G6. Off-board sweep after the per-card loop

- **Decision:** The sweep runs after the per-card loop and only when `list_project_items`
  returned, since that call raises on any page failure. Heavy work is limited to 5 claims per
  tick, oldest terminal observation first.
- **Decision-bearing:** no

### G7. Fly deletion lives in a new `fly/images.py`

- **Decision:**
  - The module is called from `EvalHandler.cleanup`, never from `FlyBackend.dispose` or
    `reconcile`.
  - It resolves the claim's tag, then deletes by digest.
  - It retries with exponential backoff from 5 minutes, capped at 6 hours.
  - A 405 `UNSUPPORTED` reply or a digest mismatch is recorded as a persistent failure. The
    operator can clear `cleanup.fly_image` to force a retry.
  - At most 5 claims are handled per tick.
- **Decision-bearing:** no

### G8. A `waiting` capture snapshot does not block pruning of a terminal claim

- **Decision:** A repetition whose curated files are incomplete does not block pruning once the
  claim is terminal, mirroring how `publish_eval_results` already treats cancelled and superseded
  claims.
- **Why:** Otherwise one broken repetition would pin a claim's disk forever.
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.
- **Validation:** `openspec validate --strict` passes after the spec edits.

## Step: test-plan

### T1. Live Fly registry acceptance uses a test-owned manifest

- **Decision:** AT-002 creates its own manifest by PUTting an annotated copy of an existing
  manifest, which gives it a unique digest, then deletes it through the delivered code. It checks
  that `base` and every other tag are untouched.
- **Condition:** It runs only where the Fly token is readable. There is no substitute, because a
  real registry is the only way to settle the risk that manifest DELETE is unsupported.
- **Alternatives considered:**
  - Delete a real stale `claim-` tag: rejected, because it is irreversible and not test-owned.
  - Fake registry only: rejected, because it leaves the main feasibility risk unverified.
- **Decision-bearing:** yes. It authorizes one external write and delete in the sandbox registry.

### T2. Acceptance never ticks against the live board or live store

- **Decision:**
  - AT-001 runs `status` read-only against the live store.
  - AT-003 drives `tick` on an isolated root through the fake-GitHub harness.
  - Automated tests never touch `~/.agent-factory`, the board, or the registry.
- **Why:** A live tick would edit real cards and delete real evidence.
- **Decision-bearing:** no

### T3. Two existing retention tests are rewritten, not deleted

- **Decision:** The two retention tests that assert cancelled and superseded claims prune without
  `cleanup.complete` are updated to the new rule (G2).
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.

## Step: approach review (approach-review-findings.json)

### AR-1 (high): a review round after an idle release leaks clones and token copies

- **Status:** applied.
- **Evidence:** Confirmed in the code. `PullRequestCleanup.reconcile` returns early when
  `complete` is true, and `retention._eligible` returns early when `pruned_at` is set.
- **Decision:** Leaving a terminal lifecycle resets the cleanup cycle. It clears `complete`,
  `released_by`, `last_error`, `review_observed`, `retention`, `results_final`, and
  `size_estimate`, in the same write that clears `terminal_observed_at`.
- **Changes:**
  - Spec: the idle-clock rule now resets completion and pruning, with a new scenario "Release
    clones of a review round that followed an idle release".
  - Design §1: reset list and rationale.
  - Test plan: INT-003 gains the reactivation cycle.
- **Alternatives considered:** Tracking each released path. Rejected, because it adds state for
  the same outcome.
- **Decision-bearing:** no. It restores an existing invariant.

### AR-2 (medium): lapse-event and prune timing across ticks

- **Status:** applied (the preferred option).
- **Evidence:** Confirmed. `reconcile` runs at runtime.py:129, before `_report` at about line 204,
  and `cleanup` runs at line 208.
- **Decision:** Keep the loop order. The design now states the sequence: release on tick 1, event
  posted on tick 2, prune on tick 3. E2E-001 and AT-003 are corrected to match.
- **Alternatives considered:** Reordering the loop. Rejected, because it would put release ahead
  of review-round admission.
- **Decision-bearing:** no

### AR-3 (medium): heavy work in the per-card loop is unbounded

- **Status:** applied.
- **Decision:**
  - One `CleanupBudget` per tick is shared by the per-card loop and the sweep: 5 removals
    (releases and prunes combined), 5 registry claims, and 2 measurements.
  - Observations are always recorded.
  - On-board claims draw on the budget first.
  - Worst case: about 32 claims drain in about 13 ticks, roughly an hour at the 5-minute poll.
- **Changes:**
  - Spec: the per-poll bound now covers all claims, with a new scenario "Spread a cleanup
    backlog across polls".
  - Design: new §4a.
  - Test plan: E2E-002 now mixes on-board and off-board claims and checks admission latency.
- **Decision-bearing:** no

### AR-4 (medium): settled claims in Done that were never seen in Review are pinned forever

- **Status:** applied.
- **Evidence:** The live store has 2 settled fix claims with `done_observed_at` set and no
  `review_observed` or `complete`.
- **Decision:** The settled idle release also applies to a settled claim in Done whose Done
  cleanup never ran, once `settled_retention_days` has elapsed. Its evidence then follows the Done
  path.
- **Changes:**
  - Spec: the settled-claims bullet, plus a new scenario "Release a settled claim in Done that was
    never seen in Review".
  - Design §1: `idle_due`.
  - Test plan: INT-003.
- **Decision-bearing:** no. It closes a gap within the issue's scope.

### AR-5 (low): publication and the capture guard disagree about settled claims

- **Status:** applied.
- **Decision:** One predicate, `capture_finished(claim)`, is used by both `publish_eval_results`
  and the guard. It is true for cancelled or superseded claims, for claims observed Done, and for
  settled claims that were idle-released. Idle-released settled claims therefore get
  `results_final` and stop being scanned every tick.
- **Changes:** Design §1; test plan INT-004 gains a row.
- **Decision-bearing:** no

### AR-6 (low): a partial size estimate is shown as the whole

- **Status:** applied.
- **Decision:** The status line reports the measured sum and names how many claims are
  unmeasured, for example `~4.3 GiB across 7 measured, 5 not yet measured`. It reads `size not
  yet measured` when nothing is measured.
- **Changes:** Spec status bullet and new scenario; design §7; test plan INT-007.
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.
- **Reasoning:** Every finding is a technical correction inside this repository.
- **Validation:** `openspec validate --strict` passes.

## Step: tasks

### K1. One implementation task for the whole change

- **Decision:** A single task, `tasks/01-terminal-claim-cleanup.md`, as instructed. It references
  the design sections and spec deltas instead of copying them, and lists every decision to
  implement.
- **Operator gates:** Acceptance flows AT-001 to AT-003 and deploy monitoring are listed in
  `tasks.md` as operator gates, not implementor work.
- **Decision-bearing:** no

### Stop check

- **Decision:** No direction-level stop is needed.
