## Why

The live factory is watched by a long-running interactive Claude session (Opus) that follows the
`factory-watch` skill. The session loops `.claude/skills/factory-watch/watch.sh`, and every wake
re-reads its whole accumulated context. In the Sep 21 to 28 analysis that was about 106k tokens a
turn, including the many wakes where nothing was wrong. The watcher cost about $129 that week, more
than all factory fix runs combined. It also has to be started by hand, kept alive, and restarted
with the right `--since` value. When it dies, failures and ready PRs go unnoticed until Paul looks.

The expensive part does not need a model. `watch.sh` already detects every event deterministically
with four SQL queries over `state.sqlite3`: `CLAIM`, `EVAL-DONE`, `PR-READY`, and `FAILURE` with a
grace period. Judgment is needed only for two of them: reviewing a ready PR, and triaging a
failure. Each of those needs a small brief, not a week of history.

**Verdict: go.** The saving is large and certain: idle periods drop from roughly 106k tokens a wake
to zero. The change also removes the manual start-and-keep-alive chore. It fits the resident, which
already ticks, owns the database, reconciles detached processes across restarts, and records
exactly-once issue events. Alternatives are weaker:

- *Keep the interactive watcher but compact or restart it more often.* This lowers the cost per wake
  but still pays for every idle wake, and still needs a human to keep it alive.
- *Run `watch.sh` from `launchd` or cron and start `claude -p` on output.* This duplicates the
  resident's scheduling, has no durable dispatch record, and cannot give the exactly-once guarantee
  across restarts that the acceptance criteria require.
- *Make every event deterministic, with no agent.* PR review and failure triage genuinely need
  judgment. Only `CLAIM` and `EVAL-DONE` can drop the agent, and this change drops it for them.

**Caveats.** An unattended agent now acts on the live factory. The dispatched sessions inherit the
`factory-watch` standing rules (never deploy, merge, or edit a release or the service clone), and
they run under a per-day budget and a concurrency cap. Interactive steps in the current skills,
such as `AskUserQuestion` for Paul's decisions and `SendMessage` to other sessions, have no headless
equivalent. Their output becomes a GitHub comment instead. Triage cannot pause the factory before a
failed claim's own automatic retry, because that retry launches in the same tick the failure is
consumed. Triage diagnoses after the retry and contains later damage.

## What Changes

- **Detection moves into the resident.** Each tick, after results are consumed, the resident runs
  the same four event queries that `watch.sh` runs, with the same `FAILURE` grace period (default 7
  minutes, configurable). A durable "handled up to" cursor in `state.sqlite3` replaces `--since`. On
  first enablement the cursor starts at the current time, so past events are not replayed. Detection
  runs whether or not the factory is paused, like feedback and reconciliation.
- **Dispatch by event:**
  - `CLAIM`: no agent. The event is logged.
  - `EVAL-DONE`: no agent. The event is logged. The factory already posts the eval result comment.
  - `PR-READY`: one headless session runs the `factory-pr-review` skill for that PR. The
    comment-only review is posted as a repository writer, as it is today, so it still starts a
    factory review round. Decisions that only Paul can make are posted by the factory bot as one
    comment on the PR instead of `AskUserQuestion`. Bot comments never start a round. The comment
    mentions a configured operator login, so Paul is notified, and `status` lists open decisions.
  - `FAILURE`: one headless triage session follows the skill's "Handling a failure" section:
    diagnose, decide the owner, and contain further damage. It does not hold the claim's own
    automatic retry. The resident consumes a technical failure and launches its recovery or
    pre-suite relaunch in the same tick, before the grace period ends. So triage diagnoses the
    failure together with the retry's state or result, and it may pause the factory to protect later
    retries and other claims when the cause persists. It writes its diagnosis and recommended next
    step to its evidence directory. The resident posts it as one factory-bot comment on the claim's
    issue through the existing exactly-once event delivery, so the comment is never read as a writer
    gesture. The session may open a fix PR under the existing rules (a separate worktree, tests
    first, Paul merges, no deploy). A handoff to another repository's owner goes into the comment,
    because no interactive session can be messaged.
- **A durable event queue, exactly once across restarts.** Each event has a stable key: the event
  kind plus the run or claim id. In one transaction, the resident inserts every event detected in
  the window as a `pending` row under its unique key, then advances the cursor. No event can fall
  behind the cursor without a row. Every later tick drains `pending` rows oldest first, by event
  time and then key. Each row moves through recorded states:
  - `logged` for `CLAIM` and `EVAL-DONE`;
  - `launched` when a session starts, recorded before the process is spawned;
  - `budget-exhausted` when the day's budget is spent;
  - `completed`, `interrupted`, `timed-out`, or `launch-failed` when it ends.

  Only a `pending` row can start a session, and the move to `launched` is a compare-and-set. So a
  later tick or a restarted resident never starts a second session for the same key. A restarted
  resident reconciles `launched` rows the way host attempts are reconciled: a live owned process
  is supervised again, and a dead one without a result is marked `interrupted`. It is not
  relaunched.
- **Failed dispatches alert, and redispatch is deliberate.** An `interrupted`, `timed-out`, or
  `launch-failed` dispatch posts one factory-bot comment on the claim's issue. The comment names the
  event, what happened, and the dispatch's evidence path, so an unreviewed PR or undiagnosed failure
  is not left to someone noticing `status`. An operator command, `watch redispatch <dispatch>`,
  queues a new attempt for the same event under a new attempt key. It counts against the budget like
  any dispatch.
- **Fresh, cheap sessions.** Each dispatch is a new detached session that does not block the tick.
  Its brief is small: the event line, the claim and run rows, and the evidence paths. No history
  carries over. The session runs in its own throwaway checkout of this repository at `origin/main`,
  never in a release, the service clone, or Paul's checkout. It has a configurable timeout.
- **Configuration.** A new `[watch]` section in `config/codagent.toml` holds `enabled`, the dispatch
  `agent` (for example `"claude:claude-sonnet-5-5:medium"`, in the same `cli:model:effort` form as
  role models), optional per-event overrides so a failure can be escalated to a stronger model, the
  concurrency cap, the per-day session budget, the grace period, the session timeout, and the
  operator login that decision comments mention.
- **Guardrails.** There is at most one automatic session per event, and never more than the
  concurrency cap at once. An event that finds the cap full stays `pending` and starts on a later
  tick. When the day's budget is spent, the row becomes `budget-exhausted` and the event is posted
  without an agent, as one factory-bot issue comment naming the event and the reason. It is never
  silently dropped, and it can be redispatched later.
- **Usage recording.** Each dispatch records its model, duration, input and output tokens, and
  estimated cost in the store. `status` shows the recent dispatches, the day's spend against the
  budget, and any undelivered comment. Sessions run through a factory-owned Agent Runner workflow,
  so the factory's existing post-run audit delivers their metrics to the same Sheet as fix and
  feature runs.
- **Skills.** `factory-watch` describes the service-driven mode as the normal one, keeps manual
  `watch.sh` for debugging, and documents `watch redispatch`. `factory-pr-review` gains a headless
  mode that writes Paul's decisions to a result file instead of asking them. The triage brief
  carries the standing rules and the "Handling a failure" steps, without the deploy step.

## Capabilities

### New Capabilities
- `factory-watch-dispatch`: service-side event detection into a durable queue with a cursor,
  exactly-once dispatch per event, the per-event actions, headless session execution with guardrails
  (budget, concurrency, timeout, forbidden actions), target-aware exactly-once comment delivery to
  the claim's issue or the PR, failed-dispatch alerts, and usage recording.

### Modified Capabilities
- `factory-operations`: the `[watch]` configuration, the `watch redispatch` command, `status`
  reporting of dispatches, open decisions, and watch spend, `doctor` readiness for dispatch (Agent
  Runner, CLI credentials, and the writer `gh` login used by PR reviews), and documentation of the
  service-driven watcher.

## Technical Approach

- **Detection** is a small module the resident's `cycle` calls after `_consume_results`. It ports
  `watch.sh`'s queries, including the `FAILURE` window shifted back by the grace period, so each
  event falls into exactly one check window. The cursor lives in the existing `settings` table. The
  dispatch queue is a new table with a unique event and attempt key, a state, and a delivery target.
  It is created idempotently without a schema version bump, so the previous release and its
  running supervisors can still open the database (design D24). No existing column or table
  changes. The `run` table is not reused: its one-nonterminal-run-per-kind index and claim lifecycle
  semantics would make dispatches compete with real work, and would make the watcher detect its own
  sessions.
- **Execution** reuses the host-process pattern that host fix runs use: a detached supervised
  process with ownership recorded durably, reconciled after a resident restart, and a timeout.
  Recommended mechanism: a factory-owned Agent Runner workflow (`factory-watch-v1.0.yaml`) with one
  agent step that loads the brief and the relevant skill. It gives role-string model selection and
  the existing audit path for usage metrics. The alternative is a bare `claude -p --output-format
  json`, which reports cost directly but bypasses the Sheet and ties dispatch to one CLI. Design
  confirms the Agent Runner route and falls back to `claude -p` only if a one-step workflow cannot
  run a skill that starts subagents.
- **Result posting** stays with the resident. A session writes a structured result file: diagnosis,
  next step, any PR opened, and decisions for Paul. The resident turns it into factory-bot comments.
  Only the PR review itself posts as a writer, because it must start a review round.
  - Every dispatch comment has its own delivery record on the dispatch row. That covers triage
    results, budget-exhausted notices, and failed-dispatch alerts on the claim's issue, and the
    decisions comment on the PR. Each record holds the target number, a marker derived from the
    dispatch key, and the delivered comment id.
  - Delivery follows the protocol of `deliver_reports`: list the target's comments, and adopt a
    bot-authored comment that already carries the marker. Otherwise post it and record the id, or
    record the failure and retry next tick. That keeps each comment exactly-once across restarts,
    whether or not the claim's card is in the cycle's card loop (design D25).
- **Budget accounting** counts dispatches started per local day in the configured schedule timezone.
  The cap counts live dispatches. Both read the dispatch table, so they survive restarts.

## Out of Scope

- Operator requests such as deploys, board edits, merges, and approving decisions. These stay with
  interactive sessions Paul opens when needed.
- Deploying anything from a dispatched session, including the hotfix exception.
- Changing how `CLAIM` and `EVAL-DONE` are reported beyond logging. The eval result comment already
  exists.
- New event types beyond the four `watch.sh` detects.
- Changes to Agent Runner, Agent Evals, or Skills repositories.
- Removing `watch.sh`. It stays for debugging.
- Automatic escalation to a stronger model on a hard failure. Escalation is a config edit, or a
  per-event override.

## Impact

- **Code:** a new watch module in `src/agent_factory/` (detection, dispatch, reconciliation, result
  posting), a hook in `runtime.cycle`, a `watch redispatch` subcommand in `cli.py`, an idempotent
  store table and accessors, `[watch]` parsing in `config.py`, `status` and `doctor` additions in
  `operations.py`, and a new `watch` package holding the workflow, which is staged into each
  session's checkout at launch.
- **Configuration:** a new `[watch]` section in `config/codagent.toml`. Absent or `enabled = false`
  keeps today's behavior.
- **Persisted data:** one new table and one new `settings` key. The table is created without a
  schema version bump, so an older release ignores it.
- **Skills and docs:** `.claude/skills/factory-watch/SKILL.md`,
  `.claude/skills/factory-pr-review/SKILL.md`, `AGENTS.md`, and `docs/operations.md`.
- **Operations:** the interactive watcher session is no longer needed. Dispatched sessions use the
  host's Claude or Codex credentials and Paul's `gh` login, the same way host fix runs do. Watch
  spend appears in `status` and in the audit Sheet.
- **Risk:** triage runs after the automatic retry has launched, so a persistent cause can still
  spend that one retry. This is the price of not holding recovery on an agent. An unattended triage
  session can pause the factory. Its comment must state the pause state, and `status` shows it. A
  mis-set budget could suppress triage, so the budget-exhausted path still posts the event.
