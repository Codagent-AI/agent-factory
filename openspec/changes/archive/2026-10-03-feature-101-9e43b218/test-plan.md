## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

Unit tests (not listed here) cover:

- `[job_cap]` parsing and validation;
- the count, window, reset, and earliest-clear-time arithmetic, including a lowered cap and equal
  timestamps;
- the notice text;
- removing `daily_sessions` from `WatchConfig`.

The obligations below cover what unit tests cannot show:

- that the cap is enforced in the store transaction across real SQLite connections;
- that every reservation path holds without side effects;
- that notices are delivered exactly once in each episode;
- the operator journey through the CLI;
- that the watch step skips nothing for volume.

All of them are model-free. They use temporary stores, stub GitHub clients, or the stub `gh`
already in the suite.

## Integration Tests

### INT-001: The cap is atomic in `reserve_run` across processes
- Covers: "Cap attempts started across the factory": concurrent reservations at the edge, every
  path capped by the store, reset, window expiry, and the earliest clear time.
- Boundary: `ClaimStore.reserve_run` and `job_cap_state`, on a real SQLite database file shared
  by two processes, each with its own `ClaimStore` connection.
- Setup:
  - a temporary state database built with `ClaimStore(path, job_cap=JobCapConfig(attempts=3, window_hours=24))`;
  - claims of two different kinds, so the per-kind slot rule does not interfere;
  - two attempts already reserved and finished within the window;
  - one older attempt, back-dated outside the window.
- Action:
  1. Two subprocesses, released by a shared barrier file, each call `reserve_run` for a
     different claim at the same time.
  2. Then call `job_cap_state`.
  3. Write a reset setting and call it again.
  4. Back-date rows to cross the window edge and call it again.
- Assertions:
  - exactly one subprocess reserves a run, and the other raises `JobCapReached`;
  - the `run` table gains exactly one row;
  - the back-dated attempt is not counted;
  - after the reset, the count is 0 and `reached` is false;
  - with 5 counted attempts and a cap of 3, `clears_at` is the third-oldest attempt's time
    plus the window;
  - a `ClaimStore` opened without `job_cap` enforces the default of 100.
- Execution: `tests/integration/test_job_cap.py`, in the default `uv run pytest` run.

### INT-002: Every reservation path holds without consuming a retry or preparing clones
- Covers:
  - "Cap attempts started across the factory": reach the cap; every reservation path is held;
    resume does not clear the cap.
  - "Notify when the job cap holds work": the claim hold is recorded.
- Boundary: the real `Controller.reserve_next`, `blocked.unblock`, and `review.review_round`,
  with a real `ClaimStore` and the pull-request handler. A stub GitHub client supplies comments,
  permissions, and branches. A recording stub records `prepare` and `prepare_review` calls.
- Setup: a store whose cap is already reached, holding:
  - an active fix claim with a retry due;
  - a blocked feature claim with an eligible writer comment;
  - a settled fix claim in Review with eligible review comments and a pull request;
  - an active eval claim with a repetition left.

  A second variant passes every pre-check, then fills the cap from another connection before
  `reserve_run`, to exercise the backstop.
- Action:
  1. Call each path once with the cap reached.
  2. Pause and resume the factory, and call them again.
  3. Clear the cap with a reset, and call them again.
- Assertions, with the cap reached:
  - no run is reserved;
  - each claim keeps its lifecycle, outcome, attempt numbers, and `waiting_review` and
    eligible-comment checkpoints;
  - no `prepare` or `prepare_review` is called on the pre-check path;
  - on the backstop path, any clones already cut are removed and the claim is held;
  - each claim has a `job-cap` hold and one `job-cap:<episode>` event.

  After pause and resume, everything is still held. After the reset, each path reserves
  normally, and `_launch` clears the job-cap hold.
- Execution: `tests/integration/test_job_cap.py`.

### INT-003: Cap notices are delivered once per episode across restarts and failures
- Covers: "Notify when the job cap holds work": a held retry is announced once, a later episode,
  delivery is retried, and the open episode survives restarts.
- Boundary:
  - `job_cap.observe`, `open_episode`, `hold_claim`, and `notify_card`;
  - `Controller.deliver_reports`;
  - a stub GitHub comment client that fails on the first post, and stores comments with
    authors and markers;
  - two `ClaimStore` connections that race `open_episode`.
- Setup: a store with the cap reached, one held claim, and one unclaimed card snapshot.
- Action:
  1. Run observe, hold, deliver, and notify for several cycles, including the failing first
     post.
  2. Close the store and reopen it (a restart), and repeat.
  3. Race `open_episode` from both connections.
  4. Reset the cap so a cycle closes the episode.
  5. Fill the cap again so a new episode opens.
- Assertions:
  - one claim comment and one card comment exist for the first episode;
  - the failed post is recorded in the receipt with its reason, then posted once;
  - a comment posted before a simulated crash, which recorded nothing, is adopted by its marker
    and not posted again;
  - the race yields one episode id;
  - closing the episode removes its card receipts;
  - the new episode posts exactly one new comment per target.
- Execution: `tests/integration/test_job_cap.py`.

### INT-004: Card classification and preflight match admission
- Covers: "Notify when the job cap holds work": a previously claimed card awaiting a retry; a new
  request whose revisions do not resolve; a new request held by a provider quota; checking a card
  creates no claim.
- Boundary:
  - the runtime card loop, run through `runtime.cycle`;
  - the real `Controller.select_existing`, `preflight`, and `accept`;
  - the fix and eval handlers;
  - a real `ClaimStore`;
  - a stub GitHub client;
  - a stub resolver that can raise `ReadinessError`.
- Setup: the cap is already reached through back-dated runs. The store and board hold:
  - a fix claim with a retry due, whose card is Ready, with no nonterminal run;
  - an eval claim with a repetition left;
  - a new fix card whose resolution raises `ReadinessError`;
  - a new feature card whose default roles use a provider under an active `admission` quota
    hold;
  - a new, clean task card;
  - a new card whose request is invalid for admission.

  Every kind's slot is free, and its window is open.
- Action: run two cycles.
- Assertions:
  - no claim is created, superseded, or changed except for the job-cap holds;
  - the fix and eval claims each get one `job-cap:<episode>` event and comment, and status shows
    them held and does not list them under unclaimed waiting cards;
  - the unresolvable card gets the existing revision-readiness comment and the attention label,
    and no job-cap comment;
  - the quota-held card gets no comment;
  - the invalid card gets its existing needs-input feedback, and no job-cap comment;
  - the clean task card gets exactly one job-cap comment across both cycles, and its resolver
    is called once;
  - `select_existing` and `accept` choose the same claim for each card. Assert this by
    clearing the cap and checking which claim `accept` returns.
- Execution: `tests/integration/test_job_cap.py`.

## End-to-End Tests

### E2E-001: An operator meets a reached cap through the CLI
- Covers:
  - "Cap attempts started across the factory": configuration wiring.
  - "Notify when the job cap holds work": a new request arrives while the cap is reached; a card
    waiting for a busy slot.
  - "Reset the job cap".
  - "Expose current operational status": inspect a reached job cap.
  - "Run an immediate normal cycle with tick": tick while the cap is reached.
- Surface: the `agent-factory` CLI (`tick`, `status`, `job-cap reset`, `pause`, `resume`) run as
  subprocesses with `--config`.
- Setup:
  - the fix-cycle harness from `tests/e2e/test_fix_cycle.py`: a temporary local configuration,
    state, and storage root, the controlled sandbox or host stub runner, and the stub `gh`;
  - a shared TOML with `[job_cap] attempts = 1` and `window_hours = 24`, and an otherwise
    default `[watch]` section that still sets `daily_sessions = 5`;
  - two Ready Bug cards in one fix target.
- Journey:
  1. `tick` admits and launches card A. The attempt completes.
  2. `tick` runs with card B Ready.
  3. `status` runs.
  4. `tick` runs again.
  5. `pause` and `resume` run.
  6. `tick` runs.
  7. `job-cap reset` runs.
  8. `tick` runs.
- Assertions:
  - configuration loads despite `daily_sessions`;
  - after step 2, card B is unclaimed and still Ready, and its issue has exactly one job-cap
    comment naming 1 of 1 attempts, an earliest clear time, and the reset command;
  - `status` shows `job cap: 1/1 attempts in the last 24 h`, `reached`, the earliest clear time,
    the reset command, and B's issue under the waiting cards;
  - the later ticks before the reset post no second comment and start no attempt;
  - `resume` does not clear the cap;
  - `job-cap reset` prints the reset time, `0/1`, and that held work can start in the next
    cycle;
  - the next `tick` admits and launches B.
- Execution: `tests/e2e/test_job_cap_cycle.py`, in the default `uv run pytest` run.

### E2E-002: The watch step skips no event for volume
- Covers:
  - "Queue each event exactly once": a busy day; a busy day while the concurrency cap is full; a
    dispatch an earlier release skipped for budget.
  - "Deliver dispatch comments exactly once": a budget notice queued before the upgrade.
  - "Report watch dispatches in status": the day's spend; a dispatch skipped for budget.
  - "Redispatch a watch event": redispatch an event skipped for budget.
  - "Configure service-driven watching": a leftover budget setting.
  - "Check a ready pull request for factory defects": no parseable URL.
- Surface: the complete watch step (`agent_factory.watch.step`) and watch `status`, with the
  model-free session stubs and comment stub already used in `tests/e2e/test_watch_cycle.py`.
- Setup:
  - a store with 30 sessions already launched today, `max_sessions = 2`, and readiness passing;
  - a `budget-exhausted` `FAILURE` dispatch whose `"budget"` delivery is still queued, written as
    an earlier release would have written it;
  - a `[watch]` table that still sets `daily_sessions = 0`.
- Journey:
  1. Detect a `FAILURE` event and run the step.
  2. Fill the concurrency cap and detect a `PR-READY` event.
  3. End one session.
  4. Redispatch the old `budget-exhausted` dispatch.
  5. Render status.
- Assertions:
  - a triage session launches for the new event, and no new dispatch is ever
    `budget-exhausted`;
  - the `PR-READY` dispatch stays `pending`, with no comment, until a slot frees, then launches;
  - the queued budget notice is posted exactly once across two steps;
  - the redispatched attempt launches when the concurrency cap and readiness allow;
  - status shows `watch sessions today: N, known cost $X` with no `/budget`, and lists the old
    dispatch as `budget-exhausted`.
- Execution: `tests/e2e/test_watch_cycle.py`. Rewrite the existing
  `test_quiet_cycle_and_budget_zero_deliver_once` and any budget assertions there.

### E2E-003: A Ready card's retry is held as a claim, not as a new request
- Covers: "Notify when the job cap holds work": a previously claimed card awaiting a retry;
  "Reset the job cap".
- Surface: the `agent-factory` CLI (`tick`, `status`, `job-cap reset`), run as subprocesses.
- Setup:
  - the fix-cycle harness, with `[job_cap] attempts = 1`;
  - one Ready Bug card whose first attempt ends with a technical failure that earns an
    automatic retry, the existing recovery path in `tests/e2e/test_fix_cycle.py`.
- Journey:
  1. `tick` launches the first attempt, which fails with a retry due.
  2. `tick` runs twice.
  3. `status` runs.
  4. `job-cap reset` runs.
  5. `tick` runs.
- Assertions:
  - no retry starts before the reset, and the claim's attempt count and lifecycle are
    unchanged;
  - the issue has exactly one job-cap comment, and no revision-readiness or new-request comment;
  - status shows the claim's blocking condition as the job cap, and lists no unclaimed waiting
    card;
  - after the reset, `tick` starts the retry under the same claim with the next attempt number,
    and no new claim is created.
- Execution: `tests/e2e/test_job_cap_cycle.py`.

## Acceptance Testing Envelope

- Environments and sandboxes:
  - a scratch checkout of this branch and its `uv` environment;
  - temporary local configurations, state databases, and storage roots under a temporary
    directory;
  - the suite's stub `gh` and fake GitHub clients;
  - the host stub runner and controlled sandbox used by the e2e harnesses.

  The pass may run any `agent-factory` command (`tick`, `status`, `doctor`, `job-cap reset`,
  `pause`, `resume`, `watch redispatch`) with `--config` and `--state` pointing at the temporary
  setup. It may craft store rows directly, for example back-dated runs, old `budget-exhausted`
  dispatches, and queued budget deliveries.
- Credentials and secrets: none are needed. Real GitHub App keys, fix tokens, and Fly tokens exist
  on this Mac under `~/.agent-factory/credentials`. The pass must not use them.
- Authorized effects: only local temporary files and processes. Remove temporary directories and
  processes afterwards. There is no model or cloud cost.
- Off limits:
  - the live service: the LaunchAgent, `launchctl`, `scripts/deploy.sh`, and anything under
    `~/.agent-factory/releases`;
  - `~/.agent-factory/config.toml`;
  - writes to the live store `~/.agent-factory/state.sqlite3`. Read-only `sqlite3 -readonly`
    queries are allowed, for example to compare attempt volumes;
  - creating, editing, or commenting on GitHub issues, pull requests, or project cards in real
    repositories;
  - pushing anywhere other than the claim's own branch through the workflow;
  - committing pins in `config/codagent.toml`.
- Permitted substitutes:
  - the stub `gh` or fake GitHub clients in place of GitHub;
  - stub runners in place of Agent Runner and model CLIs;
  - back-dating `run.created_at` in a temporary store in place of waiting out a real window.
- Known risk areas:
  - ISO timestamp handling: `isoformat()` omits microseconds when they are zero, and rows can be
    near the window edge or the reset time.
  - Duplicate episodes, or duplicate comments, when the resident and a `tick` overlap.
  - Card classification that drifts from `accept`: a reused claim versus a new request, the
    `fresh` gesture, and supersession of a changed request.
  - Delivery of a held claim's comment when the claim is `blocked` or settled in Review, not only
    `active`.
  - Clones cut and then left behind by a held unblock or review round. Review rounds already
    return early on several gates.
  - Status falling back to the default cap when shared configuration fails to load.
  - Existing tests that reserve many runs in one store and would now hit the default cap.
  - The previous watch defect cluster (`budget-exhausted`, `delivery_pending`, and redispatch).
  - Accepted limitations:
    - the cap is checked at cycle granularity;
    - each notice states the earliest clear time from when it was posted, and status shows the
      current one;
    - one overall cap can let one kind starve the others.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| factory-operations: Cap attempts started across the factory | INT-001, INT-002 | E2E-001 | — |
| factory-operations: Notify when the job cap holds work | INT-002, INT-003, INT-004 | E2E-001, E2E-003 | — |
| factory-operations: Reset the job cap | INT-001 | E2E-001, E2E-003 | — |
| factory-operations: Expose current operational status (job cap) | — | E2E-001 | — |
| factory-operations: Run an immediate normal cycle with tick (job cap) | — | E2E-001 | — |
| factory-operations: Configure service-driven watching (no budget) | — | E2E-001, E2E-002 | — |
| factory-operations: Report watch dispatches in status | — | E2E-002 | — |
| factory-operations: Redispatch a watch event | — | E2E-002 | — |
| factory-watch-dispatch: Queue each event exactly once | — | E2E-002 | — |
| factory-watch-dispatch: Deliver dispatch comments exactly once (queued budget notice) | — | E2E-002 | — |
| factory-watch-dispatch: Check a ready pull request for factory defects (no budget) | — | E2E-002 | — |
