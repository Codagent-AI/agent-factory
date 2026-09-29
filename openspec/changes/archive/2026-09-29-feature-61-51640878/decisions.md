# Decisions: feature-61-51640878

## D1 (proposal): Verdict go with caveats
- **Decision:** Go. Service-side detection removes the idle-wake cost (about 106k tokens a wake) and the manual keep-alive chore. Dispatched sessions start only for `PR-READY` and `FAILURE`. The caveat is that an unattended agent now acts on the live factory, which is bounded by the standing rules, a budget, and a concurrency cap.
- **Alternatives:** Compact or restart the interactive watcher; run `watch.sh` from launchd or cron and pipe it into `claude -p`; make every event deterministic.
- **Decision-bearing:** No. The issue sets this direction.

## D2 (proposal): Dispatch records in a new table, not the `run` table
- **Decision:** Add a dispatch table with a unique event key (event kind plus run or claim id) through an additive migration (schema 4 to 5). Keep the cursor in `settings`.
- **Alternatives:** Model dispatches as runs of a new `watch` kind.
- **Decision-bearing:** Yes. The run table's one-nonterminal-run-per-kind index and claim lifecycle would make dispatches compete with real work, and the watcher would detect its own sessions. The migration is additive, so it does not break the persisted format.

## D3 (proposal): At most one automatic session per event, never relaunched automatically
- **Decision:** Insert every detected event as a `pending` row and advance the cursor in one transaction. Start a session only through a compare-and-set from `pending` to `launched`. A dispatch whose process died without a result is marked interrupted and alerted (see D15). It is not relaunched automatically. (Revised by D13.)
- **Alternatives:** Relaunch interrupted dispatches once.
- **Decision-bearing:** Yes. The issue's guardrail says at most one session per event, and its acceptance says exactly one triage session across a restart.

## D4 (proposal): The resident posts the triage comment as the factory bot
- **Decision:** The triage session writes a result file. The resident posts it through `record_event` and `acknowledge_event` as a factory-bot issue comment.
- **Alternatives:** Let the session post with Paul's `gh` login.
- **Decision-bearing:** Yes. A writer comment on a claim's issue can be read as a gesture, for example an answer to a `needs-input` stop. Existing event delivery gives exactly one comment across restarts.

## D5 (proposal): The PR review still posts as a writer, and Paul's decisions are posted as a bot PR comment
- **Decision:** The dispatched `factory-pr-review` run posts its comment-only review with the writer `gh` login, as today, so the review still starts a factory round. Decisions for Paul, which were `AskUserQuestion` prompts, are posted by the factory bot as one PR comment through a PR-targeted delivery record (see D14). The comment mentions a configured operator login.
- **Alternatives:** Post the decisions as an issue comment; drop them.
- **Decision-bearing:** Yes. Headless sessions cannot ask questions, and bot comments never start review rounds.

## D6 (proposal): `CLAIM` and `EVAL-DONE` are only logged
- **Decision:** No agent and no new comment for these events.
- **Alternatives:** Post a deterministic eval summary.
- **Decision-bearing:** No. The issue allows "no agent", and the factory already posts the eval result comment.

## D7 (proposal): Detection runs while the factory is paused
- **Decision:** Detection and dispatch run whether or not the factory is paused, like feedback and reconciliation. `[watch] enabled` is the switch for watching.
- **Alternatives:** Stop dispatching while paused.
- **Decision-bearing:** Yes. Triage itself may pause the factory to contain a failure, and further failures still need triage.

## D8 (proposal): First enablement starts the cursor at the current time
- **Decision:** No historical events are replayed when watching is first enabled.
- **Alternatives:** Backfill from a configured time.
- **Decision-bearing:** No. This matches `watch.sh` without `--since`.

## D9 (proposal): Execute through a factory-owned Agent Runner workflow
- **Decision:** Recommend a one-step `factory-watch-v1.0.yaml` workflow, run as a detached host process like host fix runs, so the existing audit delivers usage to the same Sheet. Design falls back to `claude -p --output-format json` only if a one-step workflow cannot run a skill that starts subagents.
- **Alternatives:** Bare `claude -p` with usage read from its JSON output.
- **Decision-bearing:** Yes. The issue allows either one. Agent Runner meets "same reports as run cost" and the `cli:model:effort` model string.

## D10 (proposal): Budget-exhausted events are posted, and a full cap defers events
- **Decision:** When the day's budget is spent, the row becomes `budget-exhausted`, and the resident posts a factory-bot issue comment that names the event and says no agent ran. An event that finds the concurrency cap full stays pending and starts on a later tick. The day is counted in the schedule timezone.
- **Alternatives:** Drop events that are over the cap; count a rolling 24 hours.
- **Decision-bearing:** Yes. The issue says to "post the event without an agent" past the budget.

## D11 (proposal): Sessions run in a throwaway checkout at `origin/main`
- **Decision:** Each dispatch gets its own detached checkout of this repository under its evidence directory. The checkout is removed afterwards. Sessions never run in a release, the service clone, or Paul's checkout.
- **Alternatives:** Run in the current release.
- **Decision-bearing:** No. Releases are immutable and the service clone is off-limits under `AGENTS.md`.

## D12 (proposal): Model escalation by config
- **Decision:** One default `agent`, plus optional per-event overrides, in `[watch]`. There is no automatic escalation.
- **Alternatives:** Retry with a stronger model after a failed triage.
- **Decision-bearing:** No. The issue says escalation happens through config.

## D13 (proposal review PR-1, applied): The event queue is durable before the cursor moves
- **Finding:** A cap-full event could fall behind the cursor without a durable row.
- **Decision:** Applied. Every detected event is inserted as a `pending` row with a unique key in the same transaction that advances the cursor. Later ticks drain `pending` rows oldest first, by event time and then key. The states are `pending`, `logged`, `launched`, `budget-exhausted`, `completed`, `interrupted`, `timed-out`, and `launch-failed`, and all of them are recorded durably. Restart reconciliation acts on `launched` rows only.
- **Alternatives:** Advance the cursor only up to the oldest event that has not started. That would re-scan events and couple the cursor to dispatch capacity.
- **Decision-bearing:** Yes.

## D14 (proposal review PR-3, applied): The PR decisions comment gets its own delivery record
- **Finding:** `record_event` and `deliver_reports` always post to `claim.issue_number`, so they cannot deliver a PR comment exactly once.
- **Decision:** Applied. Claim-issue comments keep the existing path. The PR decisions comment is tracked on the dispatch row with the PR number, a marker derived from the dispatch key, and the delivered comment id. Delivery adopts an existing bot-authored comment that carries the marker, and it retries failures on the next tick. The comment mentions a configured operator login, and `status` lists open decisions.
- **Alternatives:** Post the decisions on the claim's issue through the existing path. That needs no new mechanism, but it puts PR decisions away from the PR that Paul reviews. Generalize `record_event` with a target. That touches every existing event.
- **Decision-bearing:** Yes.

## D15 (proposal review PR-4, applied): Failed dispatches alert, and redispatch is manual
- **Finding:** An interrupted, timed-out, or launch-failed dispatch was only "reported", which needs someone to notice `status`.
- **Decision:** Applied. Each such dispatch posts one factory-bot comment on the claim's issue, with the event, what happened, and the evidence path. `agent-factory watch redispatch <dispatch>` queues a new attempt under a new attempt key, and it counts against the budget. There is still no automatic relaunch.
- **Alternatives:** Retry once automatically. That breaks the issue's "at most one dispatched session per event" guardrail.
- **Decision-bearing:** Yes.

## D16 (proposal review PR-2, applied): No hold on automatic recovery; triage runs after the retry
- **Finding:** The containment promise cannot hold. `_consume_results` and the launch that follows it start a recovery or pre-suite relaunch in the same tick, before the grace period ends and before triage starts.
- **Decision:** Applied by choosing the second option the review offered. The promise to contain before the retry is removed. Triage diagnoses the failure together with the retry's state or result. It may pause the factory to protect later retries and other claims when the cause persists. The proposal states the cost: a persistent cause can spend the one retry.
- **Alternatives:** A deterministic hold on eligible automatic recovery, released by a recorded triage decision or by an operator. This was rejected for this change. It changes recovery behavior for every technical failure, including transient ones that the retry now fixes unattended, such as Fly manifest propagation. It adds agent latency before every retry, and it makes recovery depend on the dispatch budget and on watching being enabled. The issue does not ask for a change to recovery policy.
- **Decision-bearing:** Yes. The recovery hold is a reasonable follow-up issue if triage often shows retries wasted on a persistent cause.

## D17 (spec): Re-enabling watching restarts detection at the current time
- **Decision:** Detection covers only the time while watching is enabled. Re-enabling starts the "handled up to" point at the current time. Dispatches that were already `pending` survive a disable and are processed after re-enabling.
- **Alternatives:** Resume from the old point, which replays the whole disabled period into the budget. Drop pending dispatches on disable, which would silently lose events.
- **Decision-bearing:** Yes.

## D18 (spec): One review session per pull request at a time
- **Decision:** A `PR-READY` dispatch waits while another dispatch for the same pull request is `launched`.
- **Alternatives:** Allow parallel reviews of one pull request.
- **Decision-bearing:** No. The `factory-pr-review` skill already forbids a second reviewer on a pull request while one is running.

## D19 (spec): The decisions comment is posted only when there are decisions
- **Decision:** A clean review, and a review that posted only comment-only feedback, posts no factory-bot comment. The posted review itself tells Paul about the feedback.
- **Alternatives:** Always post a verdict summary comment.
- **Decision-bearing:** No. The skill already reports the verdict only when it is not "Mergeable as is" or when something was posted.

## D20 (spec): Configuration defaults, and enabling in the Codagent example
- **Decision:** `enabled` defaults to false; the cap to 2; the budget to 20 a day; the grace period to 7 minutes; the timeout to 90 minutes. The operator login is optional. Invalid values fail configuration loading only when watching is enabled. The Codagent example enables watching with `claude:claude-sonnet-5-5:medium`.
- **Alternatives:** Ship the Codagent example with watching disabled.
- **Decision-bearing:** Yes. Enabling it in the example is the point of the issue ("nobody has to start or keep a watcher session running"). The documentation says an interactive watcher that runs alongside the service duplicates the work.

## D21 (spec): A new doctor group for watching, which holds only session launches
- **Decision:** Add a `watch` group, run only when watching is enabled. It covers the Runner, `git`, `gh`, the profiles and CLI authentication, the watch workflow contract, fetch access to the factory repository, and a writer `gh` login that is not the factory bot. A failing group keeps dispatches `pending`. Detection, logging, and the other kinds continue. The doctor requirement is modified to add the group.
- **Alternatives:** Put these checks in the shared group, which would block every kind.
- **Decision-bearing:** Yes.

## D22 (spec): Budget-exhausted notices and failed-dispatch alerts go to the claim's issue
- **Decision:** Both are factory-bot comments on the claim's issue, and each names the redispatch command. Redispatch accepts terminal `PR-READY` and `FAILURE` dispatches, including `completed` ones, so an operator can re-review on purpose.
- **Alternatives:** Refuse to redispatch `completed` dispatches.
- **Decision-bearing:** No.

## D23 (spec): Usage delivery stays deferred to design
- **Decision:** The spec requires delivery to the same development-audit destination as fix and feature attempts. It marks the mechanism `deferred-to-design`, because the `claude -p` fallback cannot reach the Sheet.
- **Alternatives:** Weaken the requirement to recording usage in the store only.
- **Decision-bearing:** Yes. The issue asks that watcher cost appear "in the same reports as run cost".

## D24 (design): No schema version bump; the table is created idempotently
- **Decision:** Create `watch_dispatch` with `CREATE TABLE IF NOT EXISTS` after `_migrate()`, and keep `SCHEMA_VERSION` at 4.
- **Alternatives:** A v5 migration. `_migrate()` refuses a database newer than the code. So a bump would break deploy's automatic rollback to the previous release, and it would break that release's supervisors that are still running.
- **Decision-bearing:** Yes. The proposal's "SCHEMA_VERSION 4 to 5" text was revised.

## D25 (design): Delivery owned by the dispatch for every dispatch comment
- **Decision:** Triage, budget, alert, and decisions comments are delivered from the dispatch row's own records, using the marker, adopt, and retry protocol of `deliver_reports`, generalized to issue or PR targets.
- **Alternatives:** Use `record_event` for issue comments (D4/D14). That only delivers for claims that the per-card loop reports on, so a claim whose card is gone would never get its triage comment.
- **Decision-bearing:** Yes. The observable behavior is unchanged, and only the mechanism in D4 and D14 changes.

## D26 (design): Agent Runner workflow, with usage from `run-metrics.json`
- **Decision:** Use the packaged `factory-watch/1` workflow run by `agent-runner`. Tokens and cost come from `totals` in the session's `run-metrics.json`, with coverage. The Sheet is reached through the existing `agent_factory.audit host` in the wrapper. A session killed at its timeout has its audit recorded as missing.
- **Alternatives:** `claude -p --output-format json`.
- **Decision-bearing:** Yes. This resolves the deferred usage scenario, and the spec was updated.

## D27 (design): A new `[watch] repository` setting
- **Decision:** Sessions check out `[watch] repository` at `main` from its bare mirror, through the fix workspace's mirror code. The setting is required when watching is enabled, and the operations spec was updated.
- **Alternatives:** Derive the repository from the fix targets or the release's git remote. Both are implicit and fragile.
- **Decision-bearing:** No.

## D28 (design): Headless triage fixes in the dispatch's own clone
- **Decision:** Headless triage makes a fix on a new `fix/` branch in the throwaway clone and pushes before the session ends. It never touches the operator's checkout.
- **Alternatives:** A worktree of the operator's checkout, as the interactive skill does.
- **Decision-bearing:** No.

## D29 (design): Sessions use the operator's `gh` login
- **Decision:** The session environment carries no injected token, so the reviewer posts as a writer and triage pushes as the operator. The factory's own comments use the App client. Doctor checks the login's write access and that it is not the bot.
- **Alternatives:** Use the fix credential. Its reviews would not come from the operator, and the fix credential is meant for PR-only pushes.
- **Decision-bearing:** Yes.

## D30 (design): Supervision happens per cycle, with no watcher process
- **Decision:** The resident probes each launched session's `{pid, start}` every cycle. The timeout is enforced at the first cycle past the deadline, and the spec now says "no later than the first cycle after the timeout".
- **Alternatives:** A per-session watcher process like the run supervisor. That is more moving parts for no progress or quota tracking.
- **Decision-bearing:** No.

## D31 (design): The watch step runs in `finally` at the end of the cycle
- **Decision:** Watching runs even when the cycle's GitHub work raises, and a `FAILURE` brief sees any recovery launched in the same cycle.
- **Alternatives:** Run it right after `_consume_results`, where it would be skipped by any later exception and would not see this cycle's recovery.
- **Decision-bearing:** No.

## D32 (design): Detection takes `now` inside its transaction, with a 2-minute overlap
- **Decision:** Taking `now` under `BEGIN IMMEDIATE` makes every earlier stamp visible. A 2-minute look-back, bounded by the enablement time and deduplicated by the unique key, covers any writer that stamps outside a transaction.
- **Alternatives:** Port `watch.sh`'s exact windows, which can miss a row whose commit lands after the window closes.
- **Decision-bearing:** No.

## D33 (design): Dispatch evidence retention, and spec clarifications
- **Decision:** Add a spec requirement that removes a dispatch's evidence directory after `evidence_retention_days` and keeps its record. Reword "SHALL NOT create a claim or an attempt" to "SHALL NOT be recorded as a claim or an attempt", because a filed Bug issue legitimately becomes a claim through routing.
- **Alternatives:** Keep watch evidence forever.
- **Decision-bearing:** No.

## D34 (test plan): Automated tests make no model calls
- **Decision:** Integration and end-to-end tests use a stand-in `agent-runner` on `PATH` that writes `pid`, `watch-result.json`, and `run-metrics.json`, can block, and can spawn a grandchild. They reuse the `gh` stub and CLI subprocess fixtures from `tests/e2e/test_factory_cycle.py`. INT-007 runs the packaged workflow against the real installed Runner with a model-free step, and is skipped when the Runner is unavailable, as the existing host test is.
- **Alternatives:** Real model sessions in CI. They are costly, nondeterministic, and need credentials.
- **Decision-bearing:** No.

## D35 (test plan): Acceptance may run two real sessions, isolated from GitHub
- **Decision:** The acceptance pass may run up to two real headless sessions, about $5 in total. They run against an isolated storage root, with a `gh` stub first on `PATH` and a nonexistent watch repository, so nothing reaches GitHub or the live service.
- **Alternatives:** Stand-ins only, which leaves the connection between the workflow, the skills, and the result schema unproven until deploy. Real sessions against real PRs, which would post outward-facing reviews and start factory rounds.
- **Decision-bearing:** Yes.

## D36 (test plan): One human-only check, for the first live dispatches after deploy
- **Decision:** HT-001 asks Paul to confirm that the first live review and triage behave correctly under his login. Deploying, and letting an unattended session post as him on real PRs, are his outward-facing acts.
- **Alternatives:** None. The acceptance envelope forbids these effects.
- **Decision-bearing:** No.

## D37 (approach review RA-1, applied): `FAILURE` uses an eligibility scan
- **Finding:** With one cursor and the current grace, a failure can be skipped when the grace shrinks between cycles.
- **Decision:** Applied. Every cycle queues each run that still has a failure status, finished after enablement and within 7 days, is at least the current grace old, and is not yet queued. Unique keys prevent duplicates. The cursor now bounds only `EVAL-DONE`, `PR-READY`, and `CLAIM`. The spec's detection requirement was rewritten, with grace-shrink and grace-grow scenarios. INT-001 covers 7→0 and 7→15 minutes. The 7-day horizon is a recorded accepted limitation.
- **Alternatives:** Persist the previous grace value and cover the gap when it changes. That is more state and still fragile across several changes.
- **Decision-bearing:** Yes.

## D38 (approach review RA-2, applied): Headless instructions for the reviewer agent, and PR code from the mirror
- **Finding:** The unchanged `.claude/agents/factory-pr-reviewer.md` makes its agent fetch and add worktrees in `/Users/paul/codagent/<repo>`, and run `factory-assign` through `releases/current` from the operator's checkout. That breaks the spec's promise to leave the operator's checkout untouched, and it breaks the acceptance isolation.
- **Decision:** Applied.
  - The design adds a "Headless (dispatched) mode" section to the reviewer agent. It reads docs from `C`. It gets the PR code by a read-only `git clone --local` of the PR repository's factory mirror into the dispatch scratch directory. Dispatch start fetches that mirror, which carries `refs/pull/<N>/head`. It assigns issues with `C`'s `assign.py` under the resident's own Python, with `AGENT_FACTORY_CONFIG` set.
  - The spec's forbidden list now covers any use of the operator's checkout, fetching into mirrors, and `releases/current`. A new review scenario says the operator's checkouts are unchanged.
  - The test plan adds a unit check that the headless instructions and the brief name no such path. The acceptance pass now takes a read-only before-and-after snapshot of the refs, worktrees, and status of the operator's checkouts around a real review session, using a local fixture PR mirror.
- **Alternatives:** Clone the PR from GitHub with the operator's login, which does not work under the acceptance `gh` stub and adds network dependence. Pass overriding instructions only in the brief, which is weaker than fixing the agent file the child actually reads.
- **Decision-bearing:** Yes.

## D39 (approach review RA-3, applied): The budget is checked first
- **Finding:** Because readiness and the cap were checked before the budget, a zero or spent budget could leave events `pending` without their required notice.
- **Decision:** Applied. The budget check runs first and needs only the App client for its notice. A failing readiness check or a full cap stops launches, but the budget check still applies to the remaining rows. The spec states the order, with scenarios for zero budget while readiness fails and a spent budget while the cap is full. E2E-004 has a budget-of-0 variant with failing readiness and a full cap.
- **Alternatives:** Keep the order and accept delayed notices. That contradicts the spec.
- **Decision-bearing:** No.

## D40 (approach review RA-4, applied): A launch lease for crashes around the spawn
- **Finding:** A crash after `pending` → `launched` but before `Popen` or the pid file had no terminal path. The wrapper also did not actually write `E/pid` first.
- **Decision:** Applied.
  - The wrapper's first command is now `echo $$ > E/pid`.
  - Launching happens under the cycle lock, so a later cycle knows the launcher ended. A `launched` row with no identity and no adoptable pid for more than 2 minutes becomes `launch-failed` with an alert, frees its cap slot, and never starts a second session. Within the lease it is left alone.
  - The spec adds "stops during a launch" and "stops just after the spawn" scenarios. INT-004 adds cases 5 and 6.
- **Alternatives:** Record the process identity in the same transaction as `launched`, which is impossible before the process exists. Launch before claiming, which breaks the compare-and-set exactly-once guard.
- **Decision-bearing:** No.

## D41 (tasks): One implementation task for the whole change
- **Decision:** `tasks.md` has exactly one task, whose scope lists twelve areas in dependency order: configuration, store, detection, dispatch, session, supervision, delivery, cycle wiring, workflow, operations, skills and docs, and tests.
- **Alternatives:** Split into several tasks. The step instruction requires one task.
- **Decision-bearing:** No.
