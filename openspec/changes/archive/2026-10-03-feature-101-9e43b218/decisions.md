# Decisions: feature-101-9e43b218

## propose: verdict

- **Decision:** go. Remove the watcher's daily session budget and add a cap on factory attempts.
- **Alternatives considered:**
  - No-go, and raise `daily_sessions`. Rejected: it still caps the wrong side and leaves the
    factory with no volume brake.
  - Remove the budget without adding a job cap. Rejected: the issue requires a cap, and the
    watcher, which files factory-assigned Bugs, feeds work back into the factory.
- **Decision-bearing:** no. The issue asks for exactly this.

## propose: shape of the job cap

- **Decision:** one overall cap on attempts reserved in a rolling window, configured in shared
  TOML `[job_cap]` with `attempts` (default 100) and `window_hours` (default 24). Every reserved
  run counts (initial, retry, recovery, unblock, review, eval repetition). Watch sessions, merge
  syncs, and audits do not count. The cap always applies.
- **Alternatives considered:**
  - A count per local day, like the old budget. Rejected: it resets in a burst at midnight. A
    rolling window clears gradually and gives a computable clear time for status.
  - Caps for each kind (the issue's example mentions "overall or per kind"). Deferred as out of
    scope: one overall brake covers the runaway case, and caps per kind add configuration and
    starvation questions.
  - Limit only initial admissions. Rejected: the issue says retries stop too, and review rounds
    and unblocks are launches that can run away.
  - A cost or token budget. Rejected: cost is known only after the fact and is not available for
    every CLI.
- **Decision-bearing:** yes. The issue explicitly leaves the shape to the factory ("The factory
  decides how"). This is recorded as a reviewable default.

## propose: default cap value

- **Decision:** 100 attempts per 24 hours.
- **Alternatives considered:** 60, the observed peak on 2026-09-28 from the live store, with no
  headroom. 200, which is too loose to act as a brake.
- **Decision-bearing:** no. It is configurable, and the Codagent configuration sets it explicitly.

## propose: what reaching the cap does and how it clears

- **Decision:** It holds every reservation path without consuming a retry. Running attempts
  continue. New Ready cards are not claimed. The hold clears when the window rolls, or when the
  operator runs the new `agent-factory job-cap reset`, which stores a reset time; attempts before
  it stop counting. Raising the configured cap also clears it. `resume` does not clear it.
- **Alternatives considered:**
  - Set the persistent pause when the cap is reached. Rejected: it would not clear by itself, and
    `resume` would immediately hit the cap again unless it also reset the count. That mixes two
    controls.
  - Make a configuration change the only operator action. Rejected: shared configuration lives in
    a release, so a change needs a PR and a deploy, which is too slow as the only relief.
- **Decision-bearing:** no. It is within the issue's "until it clears or the operator acts".

## propose: where the notice appears

- **Decision:** Each held claim gets one factory-bot waiting comment on its issue for each cap
  episode, through the existing event and delivery path. Status shows the cap, the count, the
  clear time, and the reset command. The resident logs when the cap is reached and when it clears.
- **Alternatives considered:**
  - Open an operator issue in the factory repository. Rejected: it adds a new reporting channel,
    and the issue accepts "the board or issue".
  - Comment on unclaimed Ready cards. Rejected at first: it is noisy and those issues have no
    claim yet. (Revised by proposal-review PR-4 below: a Ready card that is held only by the cap
    now gets one deduplicated comment per episode.)
- **Decision-bearing:** no.

## propose: handling of `daily_sessions` and `budget-exhausted` history

- **Decision:**
  - Remove `daily_sessions` from the configuration model. The loader already ignores unknown keys,
    so an old key is ignored, not rejected.
  - Keep `budget-exhausted` as a readable ended state. `watch redispatch` still accepts it.
  - Older undelivered budget notices are not posted. (Revised by proposal-review PR-2 below:
    budget notices that are already queued are still delivered.)
  - No schema change, so a rollback is safe without a guard.
- **Alternatives considered:**
  - Reject a configuration that still sets `daily_sessions`. Rejected: the Codagent configuration
    does not set it, and a failing configuration load would stop every tick for no safety benefit.
  - Migrate the rows to another state. Rejected: the issue says they stay readable in history.
- **Decision-bearing:** no.

## proposal-review PR-1: atomic cap check for every reservation path

- **Finding:** Only `reserve_next` takes the `admission` advisory lock. The unblock and review
  round call `ClaimStore.reserve_run` directly, so the proposal's claim of a common lock was
  false.
- **Decision:** Applied. The cap is enforced inside `ClaimStore.reserve_run`, in its existing
  `BEGIN IMMEDIATE` transaction, so the count and the insert are atomic for every caller. A
  dedicated error is handled as a hold at each call site. A cycle pre-check before claiming or
  preparing a clone is only advisory.
- **Alternatives considered:**
  - A shared helper that all three paths call under the `admission` lock. Rejected: a future
    direct `reserve_run` caller could skip it.
  - Rely on the cycle lock. Rejected: it does not define the reservation boundary.
- **Decision-bearing:** no.

## proposal-review PR-2: already-queued budget notices

- **Finding:** The proposal said older undelivered budget notices would not be posted, but the
  generic delivery loop would still post queued ones.
- **Decision:** Applied by resolving the contradiction the other way. A budget notice that is
  already queued is still delivered once, and no new one is queued. The proposal now says so.
- **Alternatives considered:**
  - A one-time idempotent step that suppresses queued budget deliveries, as the review
    recommends. Rejected:
    - a queued notice reports an event that really was skipped, with its redispatch command;
      suppressing it hides that skip, which is the failure this issue is about;
    - suppression adds a migration step with no safety benefit;
    - the live store has no queued budget delivery today (one `budget-exhausted` row, with
      `delivery_pending = 0`).
- **Decision-bearing:** no. The issue only requires removing the budget path, and keeping
  history readable.

## proposal-review PR-3: clear time when the count exceeds the cap

- **Finding:** "When the oldest counted attempt leaves the window" is wrong once the count is
  above the cap, for example after the cap is lowered.
- **Decision:** Applied. The earliest clear time is the time the (N − `attempts` + 1)th oldest
  counted attempt leaves the window, with equal start times counted together. Status recomputes
  it from the current configuration on every call. Comments label the time "earliest" and point
  to status for the current value. The hold-clearing rule now says "the count falls below
  `attempts`".
- **Decision-bearing:** no.

## proposal-review PR-4: notice for unclaimed Ready cards

- **Finding:** Status reads stored claims, so an unclaimed Ready card held by the cap had no
  issue notice and no status entry. That could leave the issue's "board or issue" acceptance
  unmet for requests that arrive after the cap is reached.
- **Decision:** Applied. A Ready card that would be admitted now except for the cap (not paused,
  window open, slot free, readiness passing) gets one deduplicated factory-bot waiting comment
  per cap episode. The comment uses a marker-based receipt in the settings store, like the
  existing request-readiness receipts. The card stays unclaimed, so its revisions are not frozen
  early. Status lists the cards that have a receipt for the current episode, alongside the global
  cap line.
- **Alternatives considered:**
  - Claim the card and hold its first attempt. Rejected: it freezes revisions at admission,
    possibly hours before the work runs, and makes the cap look like started work.
  - Rely on the global status line only. Rejected: it does not meet the issue's notice on the
    board or the issue.
- **Decision-bearing:** no.

## spec: where the job cap requirements live

- **Decision:** Add four requirements to `factory-operations`:
  - "Cap attempts started across the factory";
  - "Notify when the job cap holds work";
  - "Reset the job cap";
  - "Document the factory job cap".

  "Persist pause and enforce configured admission controls" is left unchanged. The proposal's
  capability list was updated to match.
- **Alternatives considered:**
  - Extend "Persist pause and enforce configured admission controls", as the proposal first said.
    Rejected: the cap brings its own configuration, counting, notice, and command, and folding all
    of that into the pause requirement would make it hard to test.
  - A new `factory-job-cap` capability. Rejected: the cap is one more admission control, beside
    pause, windows, and holds, that the operations capability already owns.
- **Decision-bearing:** no.

## spec: cap episodes and notice eligibility

- **Decision:**
  - An episode begins when the factory finds the cap reached with no open episode, and ends when
    it finds the count below the cap. The open episode survives restarts.
  - Each held claim, and each unclaimed Ready card held only by the cap (not paused, window open,
    slot free, readiness passing), gets at most one comment per episode.
  - A card that also waits for a busy slot, a closed window, or a pause gets no job-cap comment,
    because the cap is not what holds it.
- **Alternatives considered:** Comment on every Ready card while the cap is reached. Rejected:
  it is noisy and misleading when another control also holds the card.
- **Decision-bearing:** no.

## spec: reset command semantics

- **Decision:**
  - `agent-factory job-cap reset` records the current time as a reset time. Only attempts
    reserved at or after it count.
  - It prints the reset time and the new count.
  - It is accepted whether or not the cap is reached.
  - It starts no work and does not clear a pause or other holds.
- **Alternatives considered:**
  - Refuse a reset when the cap is not reached. Rejected: refusing protects nothing, and it would
    make scripted relief fail on a race.
  - A temporary increase of the cap. Rejected: it needs its own expiry and is harder to explain.
- **Decision-bearing:** no.

## spec: leftover `daily_sessions` key

- **Decision:** A `[watch] daily_sessions` key loads without error and has no effect. A scenario
  in "Configure service-driven watching" captures this.
- **Alternatives considered:** Reject the key with an error. Rejected, for the reasons in the
  proposal decision on `daily_sessions`.
- **Decision-bearing:** no.

## design: how the cap reaches `reserve_run`

- **Decision:** `ClaimStore` takes `job_cap: JobCapConfig`, which defaults to the default cap.
  `runtime.cycle` passes `shared.job_cap`. `reserve_run` checks the cap inside its
  `BEGIN IMMEDIATE` transaction and raises `JobCapReached`.
- **Alternatives considered:**
  - A required `cap` argument on `reserve_run`. Rejected: it changes about 170 test call sites.
  - Persisting the cap in a settings row every cycle. Rejected: it adds stale-state cases.
  - A helper under the `admission` lock. Rejected: direct callers bypass it.
- **Decision-bearing:** no. It is an implementation choice within the specified behavior.

## design: counting and episode storage

- **Decision:**
  - Count `run.created_at` with a coarse SQL prefilter, then parse and compare in Python. Add an
    additive `run(created_at)` index.
  - The reset time is setting `("job-cap", "reset")`.
  - The open episode is `("job-cap", "episode")`, opened inside one transaction, and its id is
    the time it opened.
  - Card receipts use the `job-cap-card` namespace.
  - Claim holds use `set_hold(claim, "job-cap")`, and the claim notice is the event
    `job-cap:<episode>`.
- **Alternatives considered:**
  - A counter table. Rejected: it needs a migration and can drift.
  - Comparing ISO strings directly. Rejected: `isoformat()` varies in whether it includes
    microseconds.
  - Reusing the `request-readiness` receipts. Rejected: they have different wording and carry
    label state.
- **Decision-bearing:** no.

## design: pre-checks before preparation

- **Decision:** The card loop, `unblock`, and `review_round` check the cap before they prepare
  clones. `reserve_run` remains the hard boundary. When `reserve_run` raises `JobCapReached`
  after a pre-check passed, the call site discards the preparation and holds the claim.
- **Alternatives considered:** Rely on `reserve_run` alone. Rejected: every held cycle would cut
  and discard clones.
- **Decision-bearing:** no.

## test-plan: coverage layers and envelope

- **Decision:**
  - Three integration obligations in a new `tests/integration/test_job_cap.py`:
    - atomic enforcement across processes, with real SQLite;
    - every reservation path holds without side effects;
    - notices are delivered once per episode across restarts and failures.
  - Two model-free E2E journeys:
    - the operator journey through the CLI, in a new `tests/e2e/test_job_cap_cycle.py` built
      on the fix-cycle harness;
    - the watch step skipping no event, by rewriting the budget tests in
      `tests/e2e/test_watch_cycle.py`.
  - The acceptance envelope is local and temporary only. No GitHub writes, live store writes,
    live service, deploy, or credentials. Back-dating rows stands in for waiting out the window.
  - Human-only testing: none.
- **Alternatives considered:**
  - A live-service acceptance with a lowered cap. Rejected: it would hold real work and post
    real comments.
  - E2E coverage of the documentation requirements. Rejected: they are text, and the exploratory
    pass reviews them.
- **Decision-bearing:** no.

## approach-review APPROACH-001: classify reused claims as held claims

- **Finding:** The design treated a Ready card as an existing claim only when `_is_active` was
  true, which means it has a nonterminal run. `accept` also reuses a claim whose request
  fingerprint matches, which is the case for a fix awaiting its retry or an eval awaiting its
  next repetition. Those would have received an unclaimed-card notice instead of a claim hold.
- **Decision:** Applied.
  - `Controller.select_existing(snapshot, fresh)` mirrors the selection in `accept`, and `accept`
    is refactored to call it.
  - A selected claim with a due unit is held through `hold_claim`.
  - The spec's "Notify when the job cap holds work" requirement states that such a card is a held
    claim, and adds the scenario "A previously claimed card awaiting a retry".
  - The test plan adds INT-004 and E2E-003, which uses a Ready card with a retry due.
- **Alternatives considered:** Treat any card with a non-terminal claim as an existing claim.
  Rejected: it would diverge from `accept` for superseding and `fresh` requests.
- **Decision-bearing:** no.

## approach-review APPROACH-002: preflight so the job-cap notice means the cap is the sole blocker

- **Finding:** Provider quota holds and request resolution are checked only inside
  `reserve_next` and `accept`. A card blocked by a quota hold or by unresolvable revisions could
  have been told that a reset would admit it.
- **Decision:** Applied, with the preflight the review prefers.
  - `Controller.preflight` is the first half of `accept`, with no claim write. It resolves the
    request and returns a `ClaimDraft`, or delivers the existing invalid-request feedback.
  - A `ReadinessError` takes today's revision-readiness path.
  - Quota holds are checked against the draft's providers through a new per-kind
    `providers_for_spec`. Evals keep roles under `settings.roles`.
  - Only a card that passes all of these gets the job-cap notice. A receipt limits the
    preflight to once per card per episode.
  - The spec lists these conditions and adds the scenarios for unresolvable revisions and a
    quota hold. INT-004 tests them.
- **Alternatives considered:** Narrow the notice to the checks that can be proved before a claim
  exists, and state that limit in the comment. Rejected: the notice would then mislead operators
  about whether a reset would admit the card.
- **Decision-bearing:** no. It keeps the meaning the spec already promised.

## tasks: one implementation task

- **Decision:** `tasks.md` has exactly one task that implements the whole change. It lists the
  specs, the design, the overriding decision rows, and the test-plan obligations INT-001 to
  INT-004 and E2E-001 to E2E-003.
- **Alternatives considered:** Split the watch removal from the job cap. Rejected: the step
  requires exactly one task, and the docs and status changes overlap.
- **Decision-bearing:** no.
