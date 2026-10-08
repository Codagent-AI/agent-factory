# Decisions: feature-103-df2479cf

## propose: verdict

- **Decision:** go with caveats. Build best-effort, notify-only messages to the session recorded on
  an issue when the factory stops progressing on it.
- **Alternatives considered:**
  - No-go, and keep `factory-watch`. Rejected: it needs a session started by hand for each issue, and
    the issue asks to reduce that.
  - No-go because headless sessions might not be able to message. Rejected: this run, which the
    resident launched through Agent Runner, has `CLAUDE_CODE_MESSAGING_SOCKET`, and `ListAgents`
    listed Paul's interactive sessions.
- **Decision-bearing:** no. The issue asks for this behavior.

## propose: where the session is recorded

- **Decision:** a single hidden HTML-comment marker in the issue body, holding the session UUID, the
  name at recording time, and the time it was recorded. `factory-assign` replaces it.
- **Alternatives considered:**
  - A comment. Rejected: a comment from Paul's login is an eligible writer comment, which the factory
    reads as feature input and as an answer to `needs-input`. Overwriting it would also mean deleting
    or ignoring earlier comments.
  - The factory's sqlite store, written by the skills. Rejected: the skills run from any project, and
    sqlite would not survive with the issue. The issue suggests the issue itself.
- **Decision-bearing:** no. The issue lists this as an open question and gives a body marker as an
  example.

## propose: how the identifier resolves to a SendMessage address

- **Decision:** record the `CLAUDE_CODE_SESSION_ID` UUID (and the name, for readers). At send time,
  resolve the UUID through `~/.claude/sessions/<pid>.json` (`sessionId`, `name`, `pid`) to the
  current name, and require the pid to be alive.
- **Alternatives considered:**
  - Record the `[ref]`. Rejected: `SendMessage` documents that a ref not just read from a listing
    does not resolve, and the ref is not derived from the UUID (`41c0bf` belongs to `c2ae018f…`).
  - Record only the name. Rejected: names change (`nameSince` and `nameSource` exist in the
    registry), and a reused name could reach the wrong session.
  - Talk to the messaging socket directly from Python. Rejected: the protocol is undocumented and
    authenticated per peer (`peerToken`, `.key` files).
- **Decision-bearing:** yes. It depends on an undocumented Claude Code file layout. That layout
  can only cause a missed message, never a failure, and the issue asks us to check this.

## propose: notifier mechanism

- **Decision:** a dedicated, lightweight headless Claude session launched through Agent Runner, with
  only `ListAgents` and `SendMessage`, a small model, and a short timeout. The resident renders the
  fixed message text. One notifier handles every stop event.
- **Alternatives considered:**
  - Reuse the watcher for `PR-READY` and `FAILURE` and something lighter for the rest. Rejected:
    the watcher was rescoped to factory health only (`a830128`), its budget and enablement are
    separate, and two delivery paths would be harder to make exactly-once.
  - No model: a macOS notification or a GitHub comment. Rejected: neither reaches the session, which
    is what the issue asks for.
- **Decision-bearing:** no. The issue leaves this open, and the choice keeps the watcher's scope
  unchanged.

## propose: what counts as stopped

- **Decision:** the same signals `factory-watch` uses: no unfinished run, no open watch dispatch, no
  pending human review round, and the card is not queued (open, Owner=factory, Ready, no
  `needs-input`), held for a settle period. Only issues the factory has claimed are considered. Each
  stop is keyed by claim, latest run, and stop kind, and notifies at most once.
- **Alternatives considered:**
  - Notify only on run completion. Rejected: a review round or re-queue that follows immediately
    would send a premature notice, and cancellations would be missed.
  - Also notify for marked issues that were never claimed ("not queued" before admission). Rejected:
    `factory-assign` already reports admission failure in the session that ran it, and
    notifying would mean polling every marked card on the board.
- **Decision-bearing:** no. It follows the issue's reference to `factory-watch`'s stop conditions.

## propose: configuration

- **Decision:** a new shared `[notify]` section (`enabled`, `agent`, `settle_seconds`,
  `daily_sessions`, `timeout`), off by default in code and turned on in `config/codagent.toml`. The
  step runs in every cycle, whether or not the factory is paused.
- **Alternatives considered:** put the keys under `[watch]`. Rejected: they are different features
  with different purposes and budgets.
- **Decision-bearing:** no.

## proposal-review: PR-1 (notify before the watcher's own dispatch) — applied

- **Decision:** when watching is enabled, a run that should produce `PR-READY` or `FAILURE` does
  not count as stopped until its watch dispatch exists and has ended, or until a bounded detection
  window (the failure grace period plus a margin, matching `factory-watch`'s 25 minutes) has passed.
  In that case the notification records that the watch event was missed. When watching is
  disabled, this gate is skipped.
- **Alternatives considered:** a settle period long enough to cover the grace period. Rejected: it
  delays every notification, and it still does not wait for a slow triage session to end.
- **Decision-bearing:** no. It aligns with `factory-watch`, which the issue names as the reference.

## proposal-review: PR-2 (cards missing from the board snapshot) — applied

- **Decision:** stop detection starts from the latest claims in the store, not from the board
  snapshot. Issues absent from the snapshot are fetched by repository and number, and their Project
  membership is checked. A GitHub read failure is unknown state, neither a stop nor `no-session`,
  and a later cycle retries.
- **Alternatives considered:** keep the snapshot only. Rejected: a card removed from the Project,
  one of the promised stop cases, would never notify.
- **Decision-bearing:** no.

## proposal-review: PR-3 (a name can be reused between resolution and send) — applied

- **Decision:** the resident confirms the registry UUID and process identity (`pid` plus
  `procStart`) when resolving and again just before launch. The notifier calls `ListAgents`
  immediately before sending, requires exactly one live row with the resolved name, and sends to
  that row's freshly listed `name [ref]`. If there is no match or more than one, it sends nothing
  and records `no-session`. The remaining seconds-long race, which exists because `ListAgents` does
  not expose UUIDs, is stated in the proposal as a delivery limitation.
- **Alternatives considered:** send to the bare name. Rejected: it can reach a different session
  that took the name.
- **Decision-bearing:** no. It narrows delivery and adds no new dependency.

## spec: which stops count for an active claim

- **Decision:** a stop needs the claim to be `settled`, `blocked`, `cancelled`, or `superseded`, or
  needs its card to be closed, not `Owner=factory`, or off the Project (the `not-queued` stop). An
  active eval between repetitions whose card is open and `Owner=factory` is still progressing.
- **Alternatives considered:** copy `factory-watch` exactly, where a card that is not in Ready counts
  as stopped. Rejected: an eval's card sits in Running between repetitions, so every gap longer
  than the settle period would send a false "not queued" notice.
- **Decision-bearing:** no. It narrows the issue's reference definition to avoid false notices.

## spec: when factory-assign overwrites the marker

- **Decision:** only a successful `--apply` from a session with `CLAUDE_CODE_SESSION_ID` replaces
  or adds the marker. A read-only check or a refused apply (already claimed, closed, `needs-input`)
  leaves it unchanged.
- **Alternatives considered:** overwrite on every run of the skill. Rejected: a status-only check
  from another session would silently redirect notifications for work it did not hand off.
- **Decision-bearing:** no. The issue says the overwrite happens when the skill "hands an issue to
  the factory".

## spec: settings and defaults

- **Decision:** `[notify]` with `enabled` (false), `agent` (required, `claude` CLI only), a settle
  period of 360 seconds (the `factory-watch` default), a watch wait of 25 minutes (the
  `factory-watch` detection window), a daily cap of 30, and a timeout of 5 minutes. The exact
  names and the small-model profile are deferred to design.
- **Alternatives considered:** derive the watch wait from the watch grace period. Rejected: one
  explicit setting is simpler to read and tune.
- **Decision-bearing:** no.

## spec: outcomes and failure handling

- **Decision:** the outcomes are `sent`, `no-session`, `failed`, and `budget-exhausted`, with no
  retries. An ambiguous name match counts as `no-session`. A failing notify doctor group records
  the stop `failed` with the reason and does not hold it for later. A message held by the receiver
  for approval still counts as `sent`.
- **Alternatives considered:** queue stops until doctor passes. Rejected: a late notice for an old
  stop is of little use, and queuing adds state. The issue asks for best effort.
- **Decision-bearing:** no.

## spec: message content

- **Decision:** the first line names `owner/repo#N`, the kind, and what happened. It is followed by
  the issue URL, the pull request URL when there is one, the claim id, and, for `needs-input` and
  `failed`, a pointer to the explaining issue comment when one exists. No instructions.
- **Alternatives considered:** include watcher results. Rejected: the issue limits the message to
  issue, kind, what happened, and links. Status shows the watcher's notes instead.
- **Decision-bearing:** no.

## design: launch the notifier with the Claude CLI, not Agent Runner

- **Decision:** start `claude -p` directly under a supervised wrapper with
  `--tools ListAgents,SendMessage --allowedTools ListAgents,SendMessage --strict-mcp-config
  --no-session-persistence --output-format json --json-schema <result>`, in the default
  permission mode. The `notify` doctor group checks the Claude CLI and its flags instead of Agent
  Runner. The proposal and the operations spec were updated to match.
- **Alternatives considered:** an Agent Runner workflow like the watcher's. Rejected: Agent Runner
  has no tool allowlist (it only passes `--disallowedTools AskUserQuestion`), and it would need a
  clone, a workflow, and the plugin for a two-tool task.
- **Evidence:** on 2026-10-03, this command with a minimal environment delivered a
  `<cross-session-message>` to the define session, and returned `structured_output`
  `{"outcome":"no-session",...}` for an unknown name, for about $0.007 on Haiku 4.5.
- **Decision-bearing:** no. It is an implementation mechanism inside this repository.

## design: marker syntax

- **Decision:** one line, `<!-- codagent-session: {"session_id":…,"name":…,"recorded_at":…} -->`.
  The name is reduced to `[A-Za-z0-9._-]` with no `--`. The last valid marker wins. Both skills
  use `python -m agent_factory.notify.marker` from the current release (`stamp`, `carry`), so one
  implementation serves the skills and the resident.
- **Alternatives considered:** `key=value` fields. Rejected: JSON parses more robustly. A
  standalone script in the skill directory. Rejected: it would duplicate the resident's parser.
- **Decision-bearing:** no.

## design: detection inputs

- **Decision:** candidates come from the store. The snapshot cards are passed to the notify step
  through a holder that `_watch_finally` yields. Detection is skipped in a cycle whose snapshot
  failed. A pending review round is read from `claim.outcome.waiting_review`. The marker is read
  only when a stop is ready for delivery: from the snapshot, or with one REST read for an issue
  absent from it. Unmarked stops are recorded `unmarked` so they are not read again.
- **Alternatives considered:** call the review-activity API for each candidate. Rejected: it
  duplicates `waiting_review`.
- **Decision-bearing:** no.

## design: classify a settled or blocked claim with no recognised outcome as progressing

- **Decision:** a `settled` or `blocked` claim whose latest run has no recognised outcome and whose
  card is open, factory-owned, and not queued sends no notice.
- **Alternatives considered:** classify it as `not-queued`. Rejected: that would notify for every
  settled claim sitting in Review with an unusual outcome, and the message would have nothing
  specific to say.
- **Decision-bearing:** no.

## design: pruning floor

- **Decision:** ended rows are pruned after `max(evidence_retention_days, 8)` days. The spec was
  reworded from "with the claim's evidence" to this age rule, which is how the watcher prunes.
- **Alternatives considered:** prune with the claim's evidence. Rejected: watch rows already use
  age-based pruning, and claim-based pruning could drop a row inside the 7-day detection horizon
  and re-notify.
- **Decision-bearing:** no.

## design: default profile

- **Decision:** `claude:claude-haiku-4-5-20251001:low`, verified to accept `--effort low` and to
  deliver.
- **Alternatives considered:** Sonnet. Rejected: it costs several times more for a single fixed
  `SendMessage`.
- **Decision-bearing:** no.

## test-plan: a fake Claude CLI in automated tests, the real one only in acceptance

- **Decision:** every automated test puts a fake `claude` executable on `PATH`. The acceptance pass
  may launch up to 10 real Haiku notifiers (about $0.01 each), sending only to its own session or to
  throwaway sessions it starts.
- **Alternatives considered:** a live-CLI integration test in CI. Rejected: it needs Paul's Claude
  login and costs money on every run, and it fails offline.
- **Decision-bearing:** no.

## test-plan: acceptance never touches the real board or the live service

- **Decision:** acceptance uses the fake-`gh` board harness with a temporary storage root. Real
  issues, the Codagent board, the live LaunchAgent and state database, and Paul's existing sessions
  are off limits.
- **Alternatives considered:** create a real marked issue and run a live tick. Rejected: it would
  mutate the production board and could be admitted by the live factory.
- **Decision-bearing:** no.

## test-plan: one end-to-end journey

- **Decision:** a single E2E test (a marked pull-request stop through `tick`, with an unmarked
  control, pause, and a board-failure cycle). The watch gate, classification, and failure modes are
  covered by integration tests against the real store and subprocesses.
- **Alternatives considered:** E2E tests for each stop kind. Rejected: they would duplicate INT-004
  at much higher cost.
- **Decision-bearing:** no.

## approach-review: AR-1 (enablement time set after the first cycle's results) — applied

- **Decision:** `_watch_finally` calls `notify.begin` before it yields. That persists `enabled_at`
  before `_consume_results` and the claim loop, so a run that finishes in the first enabled cycle is
  eligible. `step` in `finally` still supervises, and skips detection if no cursor exists. The spec
  gained the requirement and a scenario, and INT-004 gained a test.
- **Alternatives considered:** keep setting the cursor in `finally` and subtract a margin.
  Rejected: any margin can admit pre-enablement runs or miss slow cycles.
- **Decision-bearing:** no.

## approach-review: AR-2 (absent cards and outages shortening the settle period) — applied

- **Decision:**
  - Detection reads each candidate absent from the snapshot directly, with a new
    `get_issue_presence` GraphQL call (state, labels, body, Project membership), before classifying
    it.
  - A failed read, a missing issue, or an issue that still reports Project membership is unknown,
    and is never classified `not-queued`.
  - Unknown state and skipped-detection cycles set `restart_settle` on settling rows. The first
    successful classification afterwards restarts `stopped_since`.
  - Delivery reuses the body detection read, so it makes no GitHub read of its own.

  The spec gained an outage scenario, and the test plan was updated.
- **Alternatives considered:**
  - Page the whole Project with `find_project_item`. Rejected: one issue-scoped query is cheaper.
  - Keep `stopped_since` across outages. Rejected: a notification could fire on the first read
    after the outage, without a stable settle period.
- **Decision-bearing:** no.

## approach-review: AR-3 (the details link could point at an unrelated bot comment) — applied

- **Decision:** the `Details:` link comes only from the claim's own reporting receipts
  (`claim.reporting.events`).
  - For `needs-input`, the event is `{run.id}:needs-input`.
  - For `failed`, it is the latest event belonging to that run (its id, or its
    `{unit_key}:attempt-{n}:` exhausted, retry, pre-suite, or complete event).

  The event needs a numeric `comment_id`. Otherwise the link is omitted. Watch triage comments,
  alerts, and other bot comments are never linked. The spec gained a scenario.
- **Alternatives considered:** search issue comments for the factory's hidden event marker.
  Rejected: the receipt already records the comment id, with no GitHub call.
- **Decision-bearing:** no.

## approach-review: AR-4 (what the daily cap counts) — applied

- **Decision:** the cap and its status counter count Claude notifier sessions actually launched,
  matching `daily_sessions` and the design. Stops that end before launch (`unmarked`,
  `no-session`, readiness `failed`) do not count. The status scenario now expects 2 against the
  cap, with all three outcomes listed. INT-006 counts the launched row and the two `sent` rows.
- **Alternatives considered:** count every stop outcome. Rejected: the cap exists to bound model
  sessions and cost, and pre-launch outcomes cost nothing.
- **Decision-bearing:** no.

## tasks: a single implementation task

- **Decision:** one task covers the whole change, as the workflow requires. It points to the
  design for exact names, and to approach-review AR-1 to AR-4 as overrides.
- **Alternatives considered:** split it into marker and skills, resident detection and delivery,
  and operations. Rejected: the workflow asks for exactly one task.
- **Decision-bearing:** no.

## tasks: an orange PR item for the external dependency

- **Decision:** the PR description flags that live delivery depends on Claude Code's undocumented
  session registry and cross-session messaging, and is verified only by the acceptance pass.
- **Alternatives considered:** leave it out. Rejected: it is the main residual risk, and Paul
  should see it at review.
- **Decision-bearing:** no.
