## Why

The service watcher has a daily session budget (`[watch] daily_sessions`, default 20). Once a local
day's budget is spent, every new `PR-READY` and `FAILURE` dispatch is recorded `budget-exhausted`
and gets only a factory-bot comment with a redispatch command. Nothing checks it.

This budget limits the wrong thing. The watcher only reacts to factory runs, and each run produces
at most one event of each kind. So watcher volume follows factory volume. A runaway factory still
starts every attempt it wants, and the budget only stops the watcher from looking at those attempts.
The budget saves the least when the watcher matters most. On 2026-10-02 and 2026-10-03 normal work
spent the budget. The `FAILURE` event for agent-evals#57 was skipped as `budget-exhausted` while the
controller was crashing on every cycle. The live store shows 23 completed dispatches on 2026-10-02
and a `budget-exhausted` dispatch on 2026-10-03.

Nothing currently limits the work itself. Per-claim retry and recovery limits bound one claim. They
do not bound the factory as a whole: many Ready cards, repeated review rounds, or unblocks. There is
also a loop. Watch sessions file Bug issues assigned to the factory (Owner=factory, Status=Ready),
the factory fixes them, and each fix opens a pull request that produces another `PR-READY` event.
The watcher budget was the only brake on that loop, and it braked the wrong side. The brake belongs
on attempt launches.

**Verdict: go.** The change is small and fits the existing structure. Every attempt is reserved
through one of three `reserve_run` call sites (`Controller.reserve_next`, the blocked-claim unblock,
and the review round). Each already applies launch-time holds (pause, readiness, quota) without
consuming a retry. A job cap is one more hold at that point. Removing the watcher budget mostly
deletes code. Doing nothing leaves the watcher blind during bad days and the factory without a
volume brake.

## What Changes

- **Remove the watcher's daily session budget.** Every `PR-READY` and `FAILURE` dispatch can get
  a session, still limited by `max_sessions` (concurrency), one session per pull request at a
  time, and the watch readiness checks. Removed:
  - the `[watch] daily_sessions` setting and its default;
  - the dispatch-time budget check;
  - the `budget-exhausted` notice comment and its delivery;
  - the "sessions today N/budget" status line;
  - the documentation of these.

  `max_sessions`, `grace_minutes`, and `timeout_minutes` stay unchanged. Status still shows today's
  known watch cost, without a budget.
- **Keep `budget-exhausted` history readable.** The state stays a valid ended state in the store,
  and status, history, and evidence still show existing rows. `watch redispatch` still accepts a
  `budget-exhausted` dispatch, so the operator can check an event that was skipped (for example,
  agent-evals#57). No new dispatch is ever recorded `budget-exhausted`. If an older row's budget
  notice was queued but not yet posted when this change deploys, the generic delivery loop still
  posts it. That notice reports an event that really was skipped, along with its redispatch
  command, so it is not suppressed. The change queues no new budget notice.
- **Add a factory job cap** in shared TOML, a new `[job_cap]` section:
  - `attempts`: the maximum number of attempts started in the window, across all kinds. The
    default is 100, at least 1.
  - `window_hours`: a rolling window. The default is 24, at least 1.

  Each reserved attempt counts as one: an initial attempt, a retry, a recovery, an unblock, a
  review round, or an eval repetition. Watch sessions, merge syncs, and post-run audits do not
  count. The cap always applies; if the section is missing, the defaults apply. The Codagent
  configuration sets the section explicitly.
- **Reaching the cap holds new work and does not fail it.** While the count of attempts started
  in the window (after any operator reset) is at or above `attempts`:
  - no attempt of any kind is reserved: no admission of a new Ready card, retry, recovery,
    unblock, or review round;
  - running attempts continue under their own limits;
  - a held attempt does not consume a retry, and the claim keeps its frozen inputs;
  - the hold clears when enough counted attempts leave the window for the count to fall below
    `attempts`, or when the operator acts.
- **Operator actions.** A new command, `agent-factory job-cap reset`, records a reset time in the
  store. Attempts started before that time no longer count, so work resumes on the next cycle. The
  operator can also raise `attempts` through a committed configuration change. `pause` still stops
  everything, and `resume` does not clear the cap.
- **Visible notice.**
  - Each claim whose next attempt is held gets one factory-bot comment on its issue for each cap
    episode, through the existing waiting-event path. The comment states the cap, the count, the
    earliest time it clears, and the reset command.
  - A Ready card that is not yet claimed, and would be admitted now except for the cap, gets one
    deduplicated factory-bot waiting comment on its issue for each cap episode. Only the cap may
    stand in the way: the factory is not paused, the kind's window is open, its slot is free, its
    readiness checks pass, the request's revisions resolve, and no provider quota hold applies.
    A Ready card whose existing claim would be reused, such as a retry or the next eval
    repetition, is treated as a held claim. The factory records a receipt for that card, and the card stays
    Ready and unclaimed. Claiming it would freeze its revisions early. The notice covers new
    requests that arrive while the cap is already reached.
  - `status` shows the cap as `N/attempts in the last window_hours h`. While the cap is reached,
    it also shows that it is reached, the earliest time it clears, and the reset command. Held
    claims show the cap as their waiting reason. Status also lists the unclaimed cards that have
    a cap-waiting receipt for the current episode.
  - `doctor` validates the configuration.
  - The resident logs one line when the cap is reached and one when it clears.
- **Documentation.** `docs/operations.md`, the `AGENTS.md` "Service-driven watcher" section, and
  the `factory-status` skill drop the watch budget. They describe the job cap, its notice, and the
  reset command.

## Capabilities

### New Capabilities

None. The job cap is an admission control, so it is added to the existing `factory-operations`
capability rather than introduced as a new behavioral area.

### Modified Capabilities

- `factory-watch-dispatch`:
  - remove "Bound sessions with a daily budget";
  - in "Queue each event exactly once", keep `budget-exhausted` only as a historical state that no
    new dispatch enters, and drop the budget count from the `logged` path;
  - "Deliver dispatch comments exactly once" still delivers a budget notice that was already
    queued;
  - "Check a ready pull request for factory defects" drops the budget from its unparseable-URL
    scenario.
- `factory-operations`:
  - "Configure service-driven watching" loses the per-day budget;
  - "Report watch dispatches in status" loses the session-budget line and keeps listing older
    `budget-exhausted` dispatches;
  - "Redispatch a watch event" still accepts `budget-exhausted` and no longer applies a budget;
  - "Document the service-driven watcher" drops the budget;
  - new requirements add the job cap ("Cap attempts started across the factory"), its notices
    ("Notify when the job cap holds work"), the reset command ("Reset the job cap"), and its
    documentation ("Document the factory job cap"); "Persist pause and enforce configured
    admission controls" stays unchanged;
  - "Expose current operational status" shows the cap;
  - "Run an immediate normal cycle with tick" applies the cap like the other holds.

## Technical Approach

- **Watcher.**
  - Delete the budget check, the `daily_count` helper, and the budget branch in `deliver.end` and
    in the result text. `deliver.deliver` stays generic: it does not check the purpose, so any
    budget notice already queued is still posted once, and no new one is queued.
  - Remove `daily_sessions` from `WatchConfig`, `_WATCH_MINIMUMS`, and the codagent configuration.
    The configuration loader already ignores unknown keys, so an old `daily_sessions` key is
    ignored rather than rejected.
  - Keep `budget-exhausted` in `ENDED` and in the redispatch-eligible states.
  - No schema change.
- **Job cap.**
  - Add a `JobCapConfig` on `SharedConfig`, parsed and validated like the other sections.
  - Count `run.created_at` rows within the window, ignoring rows before the stored reset time.
    The count needs no new table, and the reset time is stored in the existing settings store.
  - Enforce the cap inside `ClaimStore.reserve_run`, in the same `BEGIN IMMEDIATE` transaction
    that inserts the run. The count and the insert are then atomic for every caller. Today those
    callers are `Controller.reserve_next`, which holds the `admission` advisory lock, and the
    blocked-claim unblock and the review round, which call `reserve_run` directly without that
    lock. A concurrent `tick`, the resident, or any future direct caller cannot pass the cap
    together, because SQLite's write lock serializes them. When the cap is reached, `reserve_run`
    raises a dedicated error. Each call site handles it as a hold: no run, no retry consumed, and
    the claim keeps its lifecycle.
  - The cycle also checks the cap before claiming a new card or preparing a clone, so no clone is
    created only to be discarded. That check is advisory; `reserve_run` remains the safety
    boundary.
  - A held claim gets a waiting event whose key includes the cap episode. `deliver_reports`
    already posts exactly one comment for each key.
  - While the cap is reached, the cycle does not claim new Ready cards. It posts the pre-claim
    waiting comment through a deduplicated, marker-based receipt in the settings store, like the
    existing request-readiness receipts but with its own wording. Status reads those receipts.
  - A cap episode starts when the cap is first observed reached, and its identifier is that time.
    It ends when the count falls below the cap. A new episode posts new notices.
- **Status and doctor.**
  - Status computes the count N and the earliest clear time. That is the time the
    (N − `attempts` + 1)th oldest counted attempt leaves the window, so a cap that was lowered
    while more than `attempts` attempts still count is handled. Equal start times are counted
    together.
  - The time is reported as "earliest", assuming no reset and no configuration change. Status
    recomputes it on every call from the current configuration. Claim and card comments state
    the time that was current when they were posted, and say that status shows the current
    value.
  - Doctor reports configuration errors in the existing configuration group.
  - Status and doctor do not change state.
- **Rollback.** An older release reads its own configuration and brings back its watcher budget.
  It ignores the reset setting. No persisted format changes. No deploy guard is needed.

## Out of Scope

- Separate caps for each kind or each repository. One overall cap covers the runaway case. A cap
  for each kind can follow if one kind starving another becomes a real problem.
- Cost-based or token-based caps.
- Capping watch sessions in any other way, beyond the existing `max_sessions` concurrency.
- Automatically pausing the factory, or opening an operator issue, when the cap is reached.
- Changing per-claim retry or recovery limits, or the attempt lifecycle limits.
- Migrating or deleting existing `budget-exhausted` rows.

## Impact

- **Code:**
  - `src/agent_factory/config.py`: remove the watch budget and add `[job_cap]`;
  - `watch/dispatch.py`, `watch/deliver.py`, `watch/result.py`, `watch/store.py`, and
    `watch/status.py`;
  - `store.py` (`reserve_run`, where the cap is enforced), plus the code that handles the cap
    error at each call site: `controller.py` (`reserve_next`), and `blocked.py` and `review.py`
    in `work_kinds/pull_request/`;
  - `runtime.py`: the advisory pre-check, no new card claimed while the cap is reached, and the
    unclaimed-card notice;
  - `operations.py` (status) and `cli.py` (`job-cap reset`).
- **Configuration:** `config/codagent.toml` adds `[job_cap]`. Existing local and shared
  configurations need no edit.
- **Tests:**
  - remove or rewrite the budget tests in `tests/e2e/test_watch_cycle.py` and any fixtures that
    set `daily_sessions`;
  - add tests that:
    - the cap holds every reservation path without consuming a retry, including when two
      reservations race;
    - the cap clears with the window and on reset;
    - a lowered cap reports the correct earliest clear time;
    - exactly one notice is posted per held claim, and per eligible unclaimed card, in each
      episode;
    - an already-queued budget notice is still delivered;
  - add a status test.
- **Docs:** `docs/operations.md`, `AGENTS.md`, `.claude/skills/factory-status/SKILL.md`, and the
  OpenSpec specs listed above.
- **Operators:**
  - Watcher volume now matches factory volume, which the cap bounds. Each attempt produces at
    most one watch event, plus any redispatches the operator requests, so the cap also bounds
    watch sessions.
  - Under the observed peak of 60 attempts in 24 hours (2026-09-28), the default cap of 100 does
    not affect normal work.
