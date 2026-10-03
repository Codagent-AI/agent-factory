- [x] Replace the watcher's daily session budget with a factory job cap

## Task: Cap factory attempts instead of watcher sessions

Implement the whole change described in these files, in the change directory
`openspec/changes/feature-101-9e43b218/`:

- `proposal.md`;
- the delta specs under `specs/`:
  - `factory-watch-dispatch`:
    - modified "Queue each event exactly once";
    - modified "Deliver dispatch comments exactly once";
    - modified "Check a ready pull request for factory defects";
    - removed "Bound sessions with a daily budget";
  - `factory-operations`:
    - modified "Expose current operational status";
    - modified "Run an immediate normal cycle with tick";
    - modified "Configure service-driven watching";
    - modified "Report watch dispatches in status";
    - modified "Redispatch a watch event";
    - modified "Document the service-driven watcher";
    - added "Cap attempts started across the factory";
    - added "Notify when the job cap holds work";
    - added "Reset the job cap";
    - added "Document the factory job cap";
- `design.md`;
- the decision log `decisions.md`. Where rows differ, later rows override earlier ones. In
  particular:
  - proposal-review PR-2 overrides the earlier propose row: an already-queued budget notice is
    still delivered once;
  - the `spec` row on where requirements live overrides the proposal's capability list: "Persist
    pause and enforce configured admission controls" is unchanged;
  - approach-review APPROACH-001 and APPROACH-002 override the design's first card-loop sketch:
    classify a card through `select_existing`, and preflight new requests;
- the automated obligations in `test-plan.md`: INT-001 to INT-004 and E2E-001 to E2E-003.

Rules:

- Always compare against `origin/main`.
- Never edit a release, the service clone, the live `~/.agent-factory` state or configuration,
  other claims' clones, or `/Users/paul/codagent/*`.
- Never deploy, and never change a real GitHub issue, board item, or branch.
- Admission, retry, recovery, unblock, review-round, and watch behavior must stay exactly as it
  is, except where the specs change it.
- No schema change except the additive `run(created_at)` index.
- Agent Runner, Agent Validator, Agent Skills, and Agent Evals are outside this repository. Do not
  change them.

### Scope

1. **Remove the watch budget** (design "Watch removal"):
   - Remove `daily_sessions` from `WatchConfig` and `_WATCH_MINIMUMS`. A leftover key must still
     load, with no effect.
   - In `watch/dispatch.py`, delete the budget check and `started_today`.
   - In `watch/store.py`, delete `daily_count`. Keep `launched_today`, and keep
     `budget-exhausted` in `ENDED`, so `redispatch` still accepts it.
   - `watch/deliver.end` never queues a `"budget"` notice. `deliver.deliver` stays generic, so a
     queued budget notice is still posted once.
   - In `watch/result.notice`, drop the `budget` parameter.
   - The `watch/status.py` line becomes `watch sessions today: N, known cost $X`.
2. **Configuration:**
   - Add `JobCapConfig(attempts=100, window_hours=24)` and `SharedConfig.job_cap`, parsed by
     `_job_cap_config`. Integers of at least 1; a failure names `job_cap.<key>`.
   - Add `[job_cap]` to `config/codagent.toml` with both values set explicitly.
3. **Store** (design "Counting and enforcement"):
   - `ClaimStore(path, *, read_only=False, job_cap=JobCapConfig())`.
   - `JobCapReached` and `JobCapState`.
   - `job_cap_state(now, cap=None)`: a coarse SQL prefilter, then parse and compare in Python. It
     honors the `("job-cap", "reset")` setting. `clears_at` is the
     (N − attempts + 1)th-oldest attempt plus the window.
   - `reserve_run` checks the cap inside its `BEGIN IMMEDIATE` transaction and raises
     `JobCapReached`.
   - Add `CREATE INDEX IF NOT EXISTS run_created_at ON run(created_at)`.
   - `runtime.cycle` opens `ClaimStore(state, job_cap=shared.job_cap)`.
4. **Policy module `src/agent_factory/job_cap.py`:**
   - `open_episode`: transactional, keyed by its open time, and logs when the cap is reached.
   - `observe`: opens or closes the episode, deletes the closed episode's card receipts, and logs
     when the cap clears.
   - `hold_claim`: the `job-cap` claim hold, plus the `job-cap:<episode>` event.
   - `notice_body`.
   - `notify_card`: a `job-cap-card` receipt, a marker, adoption of an existing comment, and a
     recorded failure with retry.
   - `status_lines`.
5. **Controller and cycle** (design "Cycle integration", as amended by APPROACH-001 and
   APPROACH-002):
   - Split `Controller.accept` into `select_existing(snapshot, fresh)` and
     `preflight(snapshot, resolve) -> ClaimDraft | None`, and use both in `accept`. `preflight`
     writes no claim, and delivers invalid-request feedback as `accept` does.
   - Add `providers_for_spec(frozen_spec)` to both handlers. Each `providers(claim)` delegates to
     it: pull-request kinds read `roles`, and evals read `settings.roles`.
   - Call `job_cap.observe` once per cycle, after `_consume_results`.
   - In the card loop, when the cap is reached after the `ready` and `kind_ready` checks:
     - a selected existing claim with a due unit gets `hold_claim` plus `_report`;
     - a new request without a receipt for this episode is preflighted:
       - a `ReadinessError` takes today's revision-readiness branch;
       - `Feedback` returns `None`;
       - any active provider quota hold means no notice;
       - otherwise `notify_card`.
     - Never call `prepare` or `reserve_next` in these cases.
   - `reserve_next` catches `JobCapReached`, calls `hold_claim`, and returns `None`.
   - `blocked.unblock` adds the cap pre-check before `prepare`. When `reserve_run` raises
     `JobCapReached`, it discards the clones and holds.
   - `review.review_round` adds the cap to its early gate. When `reserve_run` raises
     `JobCapReached`, it discards any clones `prepare_review` created and holds.
   - `_launch` clears the `job-cap` claim hold on success.
   - Held claims must still get their comment delivered when they are `blocked` or settled in
     Review.
6. **Status and CLI:**
   - `status` loads the shared `job_cap`, falling back to the defaults on a load error, and
     appends `job_cap.status_lines`.
   - `_hold_lines` shows the job-cap blocking condition only for the open episode.
   - Add `agent-factory job-cap reset` (design "CLI"). It prints the reset time and the new count,
     and says held work can start in the next cycle when the cap was reached. It starts no work
     and does not change pause.
7. **Documentation** (design "Documentation"):
   - `docs/operations.md`: the watch section without the budget, and a new "Factory job cap"
     subsection;
   - `AGENTS.md`: the "Service-driven watcher" section;
   - `.claude/skills/factory-status/SKILL.md`.
8. **Tests:**
   - unit tests the specs imply:
     - `[job_cap]` parsing;
     - a leftover `daily_sessions` key;
     - the count, window, reset, and `clears_at` arithmetic, including a lowered cap and equal
       timestamps;
     - the notice text;
     - `select_existing` and `preflight` parity with `accept`;
     - `providers_for_spec` for both handlers;
   - INT-001 to INT-004 in `tests/integration/test_job_cap.py`;
   - E2E-001 and E2E-003 in `tests/e2e/test_job_cap_cycle.py`;
   - E2E-002: rewrite the budget tests in `tests/e2e/test_watch_cycle.py`, including
     `test_quiet_cycle_and_budget_zero_deliver_once`;
   - remove `daily_sessions` from any other fixtures. Any existing test that now hits the default
     cap passes a larger `JobCapConfig` to its `ClaimStore`, without weakening its assertions.

   No test may call a real model or GitHub.

### Done when

- Two processes racing for the last slot reserve exactly one attempt. Reset, window expiry, and
  a lowered cap give the right count and `clears_at`. A store opened without a cap enforces 100
  (INT-001).
- With the cap reached, these are held without consuming a retry, changing the lifecycle, or
  leaving clones:
  - first admission;
  - a retry;
  - an eval repetition;
  - an unblock;
  - a review round.

  Pause and resume do not clear the cap, and a reset does (INT-002).
- Each held claim and each eligible card gets exactly one comment per episode, across restarts,
  failed posts, and overlapping processes, and a new episode posts again (INT-003).
- A Ready card awaiting a retry or a repetition is held as a claim. A card whose revisions do not
  resolve, one held by a quota, and an invalid one get today's notices and no job-cap comment.
  No claim is created by the check (INT-004).
- The CLI journey shows the waiting card, the status lines, one comment, a resume that does not
  clear the cap, and a reset that admits the card (E2E-001). A held retry resumes under the same
  claim after the reset (E2E-003).
- No watch event is skipped for volume. An old queued budget notice is posted once, an old
  `budget-exhausted` dispatch can be redispatched, and watch status shows no budget (E2E-002).
- Docs, `AGENTS.md`, and the `factory-status` skill match the specs, and no `daily_sessions` or
  budget wording remains outside history.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest`
  pass, and `agent-validator run` is green.
- `openspec validate feature-101-9e43b218 --strict` passes.
