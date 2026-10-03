## Context

The watcher's daily budget is implemented in these places:

- `watch/dispatch.py`: `daily_count` is checked before any other gate.
- `watch/deliver.py`: `deliver.end` queues a `"budget"` notice for a `budget-exhausted` dispatch.
- `watch/result.py`: `notice(..., budget=True)` holds the budget text.
- `watch/status.py`: the `N/daily_sessions` line.
- `config.py`: `WatchConfig.daily_sessions` and `_WATCH_MINIMUMS`.

`watch/store.py` keeps `budget-exhausted` in `ENDED`, which `redispatch` already accepts.
`deliver.deliver` is generic: it posts every queued delivery whatever its purpose.

Attempts are reserved in exactly one place, `ClaimStore.reserve_run`, which inserts a `run` row
(`created_at` is an ISO UTC string) inside a `BEGIN IMMEDIATE` transaction. It has three
production callers:

1. `Controller.reserve_next` (`controller.py`), under the `admission` advisory lock. It is
   reached from the cycle's card loop for:
   - first admission;
   - the next unit of an active claim: a retry, a recovery, or the next eval repetition.
2. `blocked.unblock` (`work_kinds/pull_request/blocked.py`). It is reached from the cycle's
   existing-claim loop, and it prepares clones before reserving.
3. `review.review_round` (`work_kinds/pull_request/review.py`). It is reached from the same
   loop. It checks pause, slot, memory, window, readiness, and quota first, then prepares and
   reserves.

The cycle runs `runtime.cycle` under the `cycle` advisory lock. It opens
`ClaimStore(state)` after loading `LocalConfig` and `SharedConfig`. Shared configuration is
reloaded every tick. Claim waiting comments use `store.record_event(claim_id, key, body)`, and
`Controller.deliver_reports` (through `_report`) delivers them exactly once per key, using a
marker. Pre-claim comments use `Controller.report_request_readiness`, with a receipt in the
`request-readiness` settings namespace. `status` (`operations.py`) receives only
`LocalConfig`. Watch status already loads `SharedConfig` from `local.shared_config`, and falls
back to defaults if loading fails.

About 170 test call sites call `reserve_run` directly, and 26 construct `Controller`.

## Goals / Non-Goals

**Goals:**

- Remove the watcher budget completely, while existing `budget-exhausted` rows stay readable and
  can be redispatched.
- Enforce one factory-wide cap on reserved attempts that no reservation path can bypass. Two
  concurrent processes must not be able to exceed it together.
- Hold capped work without side effects (no retry consumed, no lifecycle change). Avoid preparing
  clones for work the cap will refuse.
- Post one notice per held claim and per eligible unclaimed card in each cap episode. Show the cap
  in `status`. Provide `job-cap reset`.

**Non-Goals:**

- Caps per kind or per repository, and cost-based caps.
- Any new schema table or column, or a migration of watch rows.
- Changes to retry, recovery, or lifecycle limits.

## Approach

### Configuration (`config.py`)

```python
@dataclass(frozen=True)
class JobCapConfig:
    attempts: int = 100
    window_hours: int = 24
```

- Add `SharedConfig.job_cap: JobCapConfig = field(default_factory=JobCapConfig)`.
- Parse it with `_job_cap_config(raw)`, following `_watch_config`:
  - a missing section gives the defaults;
  - a value that is a non-int, a bool, or below 1 raises
    `ConfigurationError("job_cap.<key> must be an integer >= 1")`.
- `config/codagent.toml` gains `[job_cap]` with `attempts = 100` and `window_hours = 24`.
- In the watch configuration:
  - remove `daily_sessions` from `WatchConfig` and `_WATCH_MINIMUMS`;
  - `_watch_config` reads only known keys, so a leftover `daily_sessions` key is ignored. Keep it
    that way, and pin the behavior with a test.

### Counting and enforcement (`store.py`)

- `ClaimStore.__init__` gains a keyword `job_cap: JobCapConfig = JobCapConfig()`, stored on the
  instance. `store.py` can import `config` because `config.py` imports nothing from the package.
  `runtime.cycle` opens `ClaimStore(state, job_cap=shared.job_cap)`. Every other construction
  gets the defaults, so the cap still applies to tests and to any future caller. Supervisors and
  `status` never reserve.
- Add `class JobCapReached(RuntimeError)`, beside `NonterminalRunError`. It carries a
  `JobCapState`.
- Add the dataclass
  `JobCapState(count: int, attempts: int, window_hours: int, reached: bool, clears_at: datetime | None)`
  and the method `ClaimStore.job_cap_state(now, cap=None) -> JobCapState`. The `cap` argument
  defaults to the instance's cap.
  - Read the reset time from setting `("job-cap", "reset")` (`{"at": iso}`).
  - The lower bound is `max(now - window, reset_at)`.
  - Select `created_at` from `run` where `created_at >= (lower_bound - 1 day).isoformat()`. This
    is a coarse prefilter that tolerates differences in ISO formatting. Parse the values, and keep
    those at or after the lower bound, sorted ascending.
  - `count = len(kept)`, and the cap is reached when `count >= attempts`.
  - When reached, `clears_at = kept[count - attempts] + window`: the (N − attempts + 1)th oldest
    attempt, using a 0-based index. Equal timestamps leave the window together, so they need no
    special case.
  - A reset can only lower the count. It never moves `clears_at` later.
- `reserve_run` calls `self.job_cap_state(now)` inside its existing `BEGIN IMMEDIATE`
  transaction, before the insert, and raises `JobCapReached(state)` when the cap is reached.
  SQLite's write lock makes the count and the insert atomic across the resident, `tick`, and any
  direct caller.
- Add `CREATE INDEX IF NOT EXISTS run_created_at ON run(created_at)`. It is additive and harmless
  to older releases.

### Policy and notices (new `src/agent_factory/job_cap.py`)

The module imports `store`, so `store` does not need to know about GitHub.

- `open_episode(store, state, now) -> str`. In one `store._transaction()`, it reads
  `("job-cap", "episode")`. When the setting is absent, it writes
  `{"id": now.isoformat(), "opened_at": ...}` and logs
  `"factory job cap reached: N/A attempts in the last W h; earliest clear T"`. It returns the
  episode id. Because the read and the write share one transaction, a concurrent `tick` cannot
  open a second episode.
- `observe(store, state, now) -> str | None`. It is called once per cycle, before admission:
  - when the cap is reached, it calls `open_episode`;
  - when the cap is clear and an episode is open, it deletes the episode, deletes every
    `job-cap-card` receipt of that episode, and logs `"factory job cap clear: N/A attempts"`.
  - It returns the open episode id, or `None`.
- `hold_claim(store, claim_id, state, now)`:
  - calls `open_episode`;
  - sets the claim hold `store.set_hold(claim_id, "job-cap", {"episode": id})`;
  - calls `store.record_event(claim_id, f"job-cap:{id}", notice_body(state))`. The existing
    event delivery posts it once per key, so there is one comment per claim per episode, and a
    restart does not repost.
- `notice_body(state)` returns: "Waiting: the factory job cap is reached (N of A attempts started
  in the last W hours). Earliest clear time: T; `agent-factory status` shows the current value.
  To start held work sooner, run `agent-factory --config <local.toml> job-cap reset`, or raise
  `[job_cap] attempts` through a committed configuration change."
- `notify_card(store, client, bot_login, snapshot, episode, state)` posts the pre-claim notice:
  - Receipt: `("job-cap-card", "<repo>:<issue>")` =
    `{"episode", "repository", "issue", "comment_id"?, "failure"?}`.
  - Marker: `<!-- agent-factory:job-cap:<sha256(episode)[:16]> -->`.
  - When the receipt already has this episode and a `comment_id`, it returns.
  - Otherwise it lists the issue's comments, adopts a bot comment that carries the marker, or
    posts a new one, and records the `comment_id`.
  - On `GitHubApiError` or `OSError`, it records `failure` and retries in a later cycle.
  - This mirrors `report_request_readiness` but keeps its own namespace and wording, so the
    revision-readiness receipts and labels are untouched.
- `status_lines(store, cap, now) -> list[str]`:
  - always: `job cap: N/A attempts in the last W h`;
  - when the cap is reached:
    `job cap: reached; earliest clear T; reset: agent-factory --config <local.toml> job-cap reset`;
  - for each `job-cap-card` receipt of the open episode: `job cap waiting: <repo>#<issue>`.

### Cycle integration (`runtime.py`)

```mermaid
flowchart TD
  A[cycle start: ClaimStore(state, job_cap)] --> B[job_cap.observe]
  B --> C{existing-claim loop}
  C -->|blocked + writer answer| U[unblock: cap pre-check before prepare]
  C -->|settled + review comments| R[review_round: cap pre-check beside pause and slot]
  U -->|reached| H[hold_claim, stay blocked]
  R -->|reached| H2[hold_claim, stay in Review]
  C --> D{card loop: not paused, window open, slot free, kind_ready}
  D -->|cap reached and active claim| H3[hold_claim, skip prepare]
  D -->|cap reached and no active claim| N[notify_card, card stays Ready]
  D -->|clear| E[accept, prepare, reserve_next]
  E -->|JobCapReached from reserve_run| H4[hold_claim]
```

- Call `job_cap.observe` once, after `_consume_results` and before the existing-claim loop.
- **Card loop.** After the existing `ready` and `kind_ready` checks pass, compute
  `state = store.job_cap_state(now)`. When it is reached, classify the card with the same rules
  `accept` uses, without any write:
  - **Existing claim.** `Controller.select_existing(snapshot, fresh=fresh) -> Claim | None`
    returns the claim `accept` would reuse:
    - the current non-superseded, non-cancelled claim, if it has a nonterminal run;
    - or that claim, if its request fingerprint matches and `fresh` is false.

    Refactor `Controller.accept` to call the same helper, so the two cannot drift. When a claim
    is returned and `handler.next_unit(claim, runs)` names a unit, call `hold_claim`, then
    `_report`, so the comment goes out this cycle. When there is no unit, do nothing; the claim
    settles as it does today. This covers a fix awaiting its retry and an eval awaiting its next
    repetition.
  - **New request.** When no claim is returned:
    - If a `job-cap-card` receipt for this card and the open episode already exists, skip the
      card for the rest of the episode. So resolution runs at most once per card per episode
      after the card is found eligible.
    - Otherwise call the new `Controller.preflight(snapshot, resolve=handler.resolve_request)`
      `-> ClaimDraft | None`. It is the first half of `accept`, with no claim creation: it calls
      `handler.accept(snapshot, store, resolve)`, delivers invalid-request feedback through
      `_invalid_feedback` when that returns `Feedback`, and returns `None` in that case.
      `accept` itself is refactored to call `preflight` and then create or supersede the claim.
    - A `ReadinessError` from resolution takes exactly today's `except ReadinessError` branch:
      the factory attention label and `report_request_readiness`. No job-cap notice is posted.
    - With a draft, compute its providers with a new handler method
      `providers_for_spec(frozen_spec)`. The two kinds store roles in different places: the
      pull-request kinds use `frozen_spec["roles"]`, and evals use
      `frozen_spec["settings"]["roles"]`. Each handler's existing `providers(claim)` is
      refactored to call `providers_for_spec(claim.frozen_spec)`. If any provider has an active
      `admission` quota hold, post no job-cap notice.
    - Otherwise call `notify_card`.
  - In every case, `continue` without calling `prepare` or `reserve_next`. A card that fails
    `ready` or `kind_ready` gets no job-cap notice, as the spec requires. The draft is discarded,
    so revisions are re-resolved when the cap clears and the card is admitted.
- **`Controller.reserve_next`.** Catch `JobCapReached` around `reserve_run`, call `hold_claim`,
  and return `None`. This is the backstop when the pre-check passed but a concurrent process took
  the last slot. The runtime already treats `None` as "nothing to launch" and reports.
- **`blocked.unblock`.**
  - Before `handler.prepare`: if `store.job_cap_state(now).reached`, call `hold_claim` and return
    `None`. The claim stays `blocked`, and its eligible comments stay eligible for the next poll.
  - Catch `JobCapReached` from `reserve_run` the same way as `NonterminalRunError`: discard the
    clones, call `hold_claim`, and return `None`.
- **`review.review_round`.**
  - Add the cap to the existing early gate (`is_paused`, slot, memory, window, readiness). When
    it is reached, call `hold_claim` and return `None`. This runs before `prepare_review`, so no
    clone is cut.
  - Also catch `JobCapReached` from `reserve_run`: discard any clones `prepare_review` created, as
    `unblock` does for `NonterminalRunError`, then call `hold_claim` and return `None`.
- **`_launch`.** On success, also clear `("claim-hold", f"{claim.id}:job-cap")`, as it already
  does for readiness.

The pre-checks are advisory. `reserve_run` is the safety boundary. A path added later that
forgets the pre-check can at most prepare a clone it then discards. It cannot exceed the cap.

### Status (`operations.py`)

- `status` loads `SharedConfig.from_file(config.shared_config).job_cap` when `config` is given,
  and falls back to `JobCapConfig()` on `ConfigurationError` or `OSError`, as watch status does.
  It appends `job_cap.status_lines` after the pause and slot lines.
- `_hold_lines` adds `blocking condition: factory job cap reached; see job cap line` when the
  claim's `job-cap` hold names the currently open episode. A stale hold from a closed episode is
  ignored.
- Status calls only reads (`job_cap_state` and settings reads), and changes no state.

### CLI (`cli.py`)

Add `agent-factory job-cap reset`:

1. Load the cap from `SharedConfig.from_file(local.shared_config)`, or use the defaults when
   only `--state` is given.
2. Compute `before = job_cap_state(now)`.
3. Write `("job-cap", "reset") = {"at": now.isoformat()}`.
4. Compute `after`.
5. Print `job cap reset at T; N/A attempts in the last W h`, and also
   `held work can start in the next cycle` when `before.reached`.

The command does not close the episode. The next cycle's `observe` finds the cap clear, closes
the episode, and logs. The command starts no work and does not touch pause, holds, or runs.

### Watch removal

- `watch/dispatch.py`: delete `started_today`, the budget branch, and its comment. The dispatch
  order is now: kind and URL filtering, the concurrency cap, the PR-running check, then
  readiness.
- `watch/store.py`: delete `daily_count`. Keep `launched_today`, which status uses for the
  session count and cost, and keep `budget-exhausted` in `ENDED`.
- `watch/deliver.py`: `end` no longer computes `budget`. Every non-completed end queues the
  `"alert"` notice. `deliver` is unchanged, so an already queued `"budget"` delivery is posted
  once.
- `watch/result.py`: drop the `budget` parameter and the text it selects.
- `watch/status.py`: the line becomes
  `watch sessions today: N, known cost $X`. The listing of ended dispatches still includes
  `budget-exhausted`.

### Documentation

- `docs/operations.md`, in "Service-driven watch dispatch":
  - drop `daily_sessions` and the budget paragraph;
  - state that every event gets a session, limited by `max_sessions` and one per pull request;
  - state that old `budget-exhausted` rows can be redispatched.
- `docs/operations.md`, a new "Factory job cap" subsection near the admission controls:
  - settings and defaults;
  - what counts;
  - holds;
  - clearing;
  - comments;
  - status;
  - reset;
  - that raising the cap needs a committed configuration change.
- `AGENTS.md`, "Service-driven watcher": replace "budget" in the status description, and add one
  sentence saying no event is skipped for volume and that `[job_cap]` bounds factory volume.
- `.claude/skills/factory-status/SKILL.md`: replace the watch-budget item with a job cap that is
  reached or near its limit.

## Decisions

- **Enforce inside `reserve_run`, with the cap injected through the `ClaimStore` constructor.**
  - Alternatives considered:
    - A required `cap` argument on `reserve_run`. Rejected: it changes about 170 test call sites,
      and a caller could still pass a wrong value.
    - Persisting the configured cap in a settings row every cycle. Rejected: it adds stale-state
      cases, for example status between cycles.
    - A shared helper under the `admission` lock. Rejected: direct `reserve_run` callers bypass
      it.
  - With constructor injection, production gets the configured cap and every other store gets
    the default cap. No code path is uncapped.
- **Count from `run.created_at`. Keep the reset time and the episode in settings.**
  - Alternatives considered: a counter table. Rejected: it needs a migration and can drift from
    the runs it counts.
  - Each `run` row is one reservation, which matches the spec's definition of what counts.
- **Parse timestamps in Python after a coarse SQL prefilter.**
  - Alternatives considered: comparing ISO strings directly. Rejected: `isoformat()` omits
    microseconds when they are zero, so exact string bounds are fragile.
  - The prefilter keeps the scan small, and the new index supports it.
- **The episode id is its open time. The open is transactional.**
  - The id makes the event key `job-cap:<id>` and the card marker unique per episode.
  - The transaction prevents duplicate episodes when the resident and a `tick` overlap.
- **Pre-checks before preparation, and `reserve_run` as the backstop.**
  - The pre-checks avoid cutting clones that would be discarded.
  - The backstop makes the cap a hard boundary.
- **Classify and preflight a card through refactored `accept` halves (`select_existing` and
  `preflight`).**
  - These are shared with `accept`, so the classification and the eligibility check cannot drift
    from real admission.
  - Alternatives considered:
    - Checking `_is_active`. Rejected: it misses reused claims that are waiting for a retry or a
      repetition.
    - Narrowing the notice's promise. Rejected: the spec promises that the cap is the sole
      blocker.
  - The cost: resolution, which can fetch mirrors, runs at most once per eligible card per
    episode. A card whose resolution fails has no receipt, so it is re-resolved every cycle,
    which is what happens today.
- **A separate `job-cap-card` receipt namespace.**
  - Alternatives considered: reusing `report_request_readiness`. Rejected: its text says
    "revision readiness", and its receipt carries the factory attention-label state, which
    accept clears.

## Risks / Trade-offs

- **A burst of notices when the cap is reached with many held claims.**
  - There is at most one per claim, and per eligible card, in each episode.
  - The number of open claims and Ready cards bounds it, not cycles.
- **Tests that reserve more than 100 runs in one store would now fail with `JobCapReached`.**
  - Mitigation: run the full suite during implementation.
  - Any such test passes `job_cap=JobCapConfig(attempts=<large>)` to its `ClaimStore`.
- **The cap ignores `created_at` clock skew between processes.** All writers run on one Mac and
  use the same UTC clock.
- **The default cap might hold real work on a heavy day.**
  - The observed peak is 60 attempts in 24 hours (2026-09-28), against a cap of 100.
  - Relief is `job-cap reset`, or a committed raise.
- **A held claim's eligible comments, such as review comments, wait without a new
  acknowledgement.** The job-cap comment explains why. The eligibility checkpoints are unchanged,
  so nothing is lost.

## Migration Plan

- **Deploy.** No schema migration is needed beyond `CREATE INDEX IF NOT EXISTS`, which runs at
  store open.
  - The first cycle on the new release computes the count from existing runs, so the cap is
    effective immediately.
  - Existing `budget-exhausted` rows are untouched. Any queued budget notice is delivered by the
    generic loop.
- **Rollback.** An older release:
  - ignores the `job-cap`, `job-cap-card`, and `claim-hold …:job-cap` settings, and the index;
  - reads its own configuration, which brings back the watch budget;
  - finds no `budget-exhausted` rows created by the new release, because the new release creates
    none.

  No deploy guard is needed.

## Open Questions

None.
