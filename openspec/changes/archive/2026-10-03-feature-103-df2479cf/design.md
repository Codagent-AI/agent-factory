## Context

The resident runs one cycle every few minutes (`runtime.cycle`). The service watcher is wired in
through `_watch_finally` (`src/agent_factory/runtime.py:49`). That context manager's `finally`
calls `watch.step` after the cycle body, while the cycle lock and the store are open, whether the
body succeeded or not and whether the factory is paused or not. `watch.step` wraps each sub-step in
`_safe`, so one failing step never stops another or the cycle.

The facts this design builds on were verified on Paul's Mac on 2026-10-03:

- **Claude Code session registry.** Every live session has `~/.claude/sessions/<pid>.json`. It holds
  `sessionId` (the `CLAUDE_CODE_SESSION_ID` UUID), `name` (the current `SendMessage` address),
  `pid`, and `procStart`. Beside it is a `<pid>.<hash>.key` file that must never be read. The file
  disappears when the session exits (`78510.json` was gone once `agent-factory-ab` ended).
  `procStart` equals `TZ=UTC ps -o lstart= -p <pid>` exactly, for example
  `'Sat Oct  3 21:25:39 2026'`.
- **Headless delivery works.** A `claude -p` process started with a minimal environment (`PATH`,
  `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`, matching `supervisor.inherited_environment`) and
  `--tools ListAgents,SendMessage --allowedTools ListAgents,SendMessage` delivered a
  `<cross-session-message>` to another session. The sender showed as
  `from-name="<cwd basename>-<nn>"` and `from-mode="prompting"`.
- **Structured result.** With `--output-format json --json-schema <schema>`, the result has
  `structured_output` (for example `{"outcome": "no-session", "detail": "..."}`), `is_error`, and
  `total_cost_usd`.
- **Cost.** One delivery on `claude-haiku-4-5-20251001` took 3 turns and about $0.007.
  `--effort low` is accepted with that model.
- **Agent Runner cannot restrict tools.** Its workflows have no tool allowlist; it only passes
  `--disallowedTools AskUserQuestion` internally. The notifier therefore calls the Claude CLI
  directly.
- **The board snapshot.** `GitHubClient.list_project_items` reads every Project card, open or
  closed, with the issue body, labels, state, and single-select fields, but not the title. A card
  absent from a successful snapshot is usually off the Project, but `_queue_item` silently
  drops malformed items, so absence alone is not proof. `get_source_item(repo, number)` reads one
  issue, including its body, over REST.
- **Pending review rounds.** `review.process_review_claim` records eligible human review activity in
  `claim.outcome.waiting_review` before the slot and pause gates, and clears it when the round
  starts. Status already reads it.
- **Watch dispatches.** They live in `watch_dispatch`, keyed by `run_id`. Their end states are
  `watch_store.ENDED` plus `logged`. The rules for which runs raise `PR-READY` or `FAILURE` are in
  `watch/detect.py`.

## Goals / Non-Goals

**Goals:**
- Record one session per issue from the two skills, in a form the resident can parse.
- Detect, from the store, when the factory stops progressing on a marked issue, with no extra GitHub
  calls in the common case.
- Deliver one notify-only message per stop through a headless Claude session that can only list
  sessions and send messages. Record every outcome, and never affect a claim.
- Keep the change additive: a new table, a new configuration section, and new doctor and status
  entries. Rollback to an older release is safe.

**Non-Goals:**
- Changing the watcher, its events, or its budget.
- Delivery to sessions on another machine, in the cloud, or through Remote Control.
- Reading replies, retrying, or tracking more than one session per issue.

## Approach

### Package layout

The new package is `src/agent_factory/notify/`, mirroring `watch/`:

| Module | Responsibility |
|---|---|
| `marker.py` | Parse, render, stamp, and carry the session marker. Also a small CLI (`python -m agent_factory.notify.marker`). |
| `registry.py` | Read `~/.claude/sessions/*.json` and resolve a UUID to a live session. |
| `store.py` | The `notify_stop` table helpers, the enablement cursor, and the daily count. |
| `detect.py` | Find candidate claims, classify stops, track the settle period and the watch gate. |
| `deliver.py` | Render the message, apply the budget and readiness gates, and launch the notifier. |
| `supervise.py` | Finish launched notifiers: parse the result, or time out and terminate. |
| `readiness.py` | The `notify` doctor group. |
| `status.py` | The notify section of `status`. |
| `__init__.py` | `step(...)`, with each sub-step wrapped in `_safe`, like `watch.step`. |

### Cycle wiring

`_watch_finally` yields a small mutable holder, `CycleView(cards: list[ProjectQueueItem] | None)`.

**Before the body.** Before it yields, `_watch_finally` calls `notify.begin(store, shared)`, wrapped
in `_safe`. When notifications are enabled and no cursor exists, it persists
`settings('notify','cursor') = {"enabled_at": now}`. This happens before `_consume_results` and
the claim loop, so a run that finishes and settles in the first enabled cycle has
`finished_at > enabled_at` and stays eligible. `begin` never clears the cursor.

**After the body.** The cycle body sets `view.cards = cards` right after `list_project_items`
succeeds. In `finally`, after `watch.step`, it calls
`notify.step(store, client, shared, local, view.cards)`:

```
notify.step:
  supervise()                         # always, even when disabled
  if shared.notify.enabled:
      if cards is not None:           # skip detection when this cycle's snapshot failed
          detect(...); deliver(...)
  else:
      clear cursor; drop rows still in `settling`
  prune()                             # always
```

If `begin` failed, `step` finds no cursor and skips detection for that cycle.

Notify runs after the watch step, so a dispatch the watcher created in this cycle is visible to the
watch gate. It runs whether the factory is paused or not, like the watcher. When this cycle's
snapshot failed, detection is skipped. GitHub is most likely unavailable then, and the next cycle
catches up. Every settling row is flagged `restart_settle`, so a full, observed settle period always
follows an outage (see Detection, step 2).

### Marker

The marker is one line at the end of the body:

```
<!-- codagent-session: {"session_id":"c2ae018f-230c-437f-bd07-ff9f49ab6a82","name":"agent-factory-ab","recorded_at":"2026-10-03T21:17:24Z"} -->
```

- **Parsing.** It matches `<!--\s*codagent-session:\s*(\{.*?\})\s*-->`, multiline. The last match
  wins. A JSON object is valid when `session_id` is a canonical UUID. `name` and `recorded_at` are
  optional and informational. Anything else is malformed and treated as absent.
- **Rendering.** `json.dumps(..., separators=(",", ":"))`. The name is reduced to
  `[A-Za-z0-9._-]`, at most 64 characters, with runs of `-` collapsed to one. Neither `--` nor `>`
  can appear, so the HTML comment cannot be closed early. If the name is empty after reduction, it
  is omitted.
- **`stamp(body, session_id, name, now)`.** Removes every existing marker and appends exactly one,
  separated from the text by a blank line. The rest of the body is unchanged.
- **`carry(old_body, new_body)`.** Puts `old_body`'s marker into `new_body` in place of any marker
  there. If `old_body` has none, `new_body` is returned with no marker.
- **CLI.** It is run with the release interpreter,
  `~/.agent-factory/releases/current/.venv/bin/python -m agent_factory.notify.marker`:
  - `stamp FILE` rewrites `FILE` in place with the current session (read from
    `CLAUDE_CODE_SESSION_ID`, with the name looked up in the registry). Without the variable it
    leaves the file unchanged and exits 0.
  - `carry OLD NEW` rewrites `NEW`.
  - Both print one line saying what they did.

**`codagent-github-project` skill.** Before `gh issue create --body-file F`, it runs `stamp F`.
Before any `gh issue edit --body-file F`, it saves the current body to `OLD` and runs `carry OLD F`.
If the command fails (the release predates this change, or the interpreter is missing), the skill
says so in one line and continues without a marker. Recording is best effort.

**`factory-assign` skill (`assign.py`).** After every refusal check passes and before Owner and
Status are set, `apply` reads the body again and computes `marker.stamp(...)` with the process's
`CLAUDE_CODE_SESSION_ID`. If the body changed, it PATCHes it through Paul's `gh`:
`repos/{r}/issues/{n}` with `body` set from a file. Writing the marker before the card reaches Ready
means the claim can never stop before its marker exists. Without the variable, nothing is written.
`report` prints `session: <name> (<uuid>)` or `session: none` from the parsed marker.

### Registry resolution

`registry.resolve(session_id, root=Path.home()/".claude/sessions") -> LiveSession | None`:

1. Iterate `root.glob("*.json")`. A file whose name contains a second `.` is skipped, so `.key`
   files are never opened. Each file is parsed with a 64 KiB cap. Only `sessionId`, `name`, `pid`,
   and `procStart` are read. Unreadable or malformed files are skipped.
2. Keep the entries whose `sessionId` equals the UUID. If none match, the result is `None`.
3. For each match, run `ps -o lstart= -p <pid>` with `TZ=UTC` and a 5-second timeout. The match
   holds only when the output equals `procStart` after whitespace is normalized. A missing process,
   a different start, or a probe error gives `None`. With two live matches (not expected), the
   result is also `None`.
4. Return `LiveSession(session_id, name, pid)`.

A missing or unreadable registry directory gives `None`, and the outcome is `no-session`. Resolution
runs when a stop's settle period completes and again immediately before the notifier is started.

### Store

The `notify_stop` table is created by `ClaimStore._ensure_notify_schema()`. It is called after
`_ensure_watch_schema()`, outside the versioned schema, with `CREATE TABLE IF NOT EXISTS` and
`ADD COLUMN` checks, for the same rollback reason the watch table uses.

```sql
CREATE TABLE IF NOT EXISTS notify_stop (
  id TEXT PRIMARY KEY,
  claim_id TEXT NOT NULL REFERENCES claim(id),
  run_id TEXT NOT NULL,
  repository TEXT NOT NULL, issue_number INTEGER NOT NULL, claim_kind TEXT NOT NULL,
  stop_kind TEXT NOT NULL,          -- pull-request|needs-input|failed|settled|cancelled|not-queued
  state TEXT NOT NULL,              -- settling|launched|ended
  outcome TEXT,                     -- sent|no-session|failed|budget-exhausted|unmarked
  detail TEXT NOT NULL DEFAULT '',
  watch_note TEXT NOT NULL DEFAULT '', -- ''|watch event missed|watch dispatch waiting
  session_id TEXT, session_name TEXT,
  pr_url TEXT, message TEXT,
  stopped_since TEXT NOT NULL,
  restart_settle INTEGER NOT NULL DEFAULT 0,
  launched_at TEXT, deadline_at TEXT, finished_at TEXT,
  profile TEXT, evidence_path TEXT, process_json TEXT NOT NULL DEFAULT '{}',
  cost_usd REAL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(claim_id, run_id, stop_kind));
CREATE INDEX IF NOT EXISTS notify_stop_state ON notify_stop(state);
```

The `UNIQUE` key is the stop identity from the specification. A stop moves only forward, from
`settling` to `launched` to `ended`, or from `settling` straight to `ended`, by conditional
`UPDATE ... WHERE state=?`. So a concurrent manual `tick` cannot launch it twice. A manual `tick`
and the resident both hold the cycle lock anyway.

- `outcome = 'unmarked'` records a stop whose issue had no valid marker, so the stop is not
  evaluated again. Status hides it.
- The cursor is `settings('notify','cursor') = {"enabled_at": ...}`, set on the first enabled cycle
  and removed when notifications are disabled.

### Detection

`detect(store, cards, shared, now)` does the following.

1. **Candidates (SQL).** For each `(repository, issue_number)`, take the latest claim (by
   `created_at`) and that claim's latest run (by `created_at`). Keep it when:
   - the run's `finished_at` is after `enabled_at` and within 7 days;
   - no run of the claim has `finished_at IS NULL`.
2. **Verify issues absent from the snapshot.** The candidates that pass step 3's "already
   handled" filter and whose issue is not in the snapshot are read with one new GraphQL call per
   issue, `GitHubClient.get_issue_presence(repository, number) -> IssuePresence(state, labels,
   body, on_project)`. `on_project` is true when the issue's `projectItems` include
   `shared.project.id`. The result is cached for the cycle.
   - If the call fails or returns no issue, the state is **unknown**. Classification and settlement
     are skipped for that candidate, and any `settling` row it has gets `restart_settle = 1`.
   - When a cycle's snapshot is missing and detection is skipped, `step` sets `restart_settle = 1`
     on every `settling` row.
   - The next successful classification of a row with `restart_settle = 1` sets
     `stopped_since = now` and clears the flag. The settle clock therefore restarts at the first
     successful read after any outage.
   - An issue that is verified as still on the Project but missing from the snapshot (a malformed or
     transiently dropped item) is also unknown, not `not-queued`.
3. **Classify (pure function `classify(claim, run, card)`).** `card` is the snapshot item for
   `(repository, number)`. For a verified absent issue, it is a synthetic card built from the
   `IssuePresence`, with `on_project = false`, so it is never queued and never factory-owned.
   The first rule that applies wins:

   | Condition | Result |
   |---|---|
   | `outcome.waiting_review` is non-empty | progressing |
   | card is open, `Owner=factory`, `Status=Ready`, no `needs-input` label | progressing (queued) |
   | lifecycle `cancelled` or `superseded` | `cancelled` |
   | lifecycle `settled` or `blocked`, and the run status is in `detect._FAILURES` or the run outcome is `failed` | `failed` |
   | lifecycle `settled` or `blocked`, and the outcome is `needs-input` | `needs-input` |
   | lifecycle `settled` or `blocked`, and the outcome is `pull-request` | `pull-request` |
   | lifecycle `settled` and the claim kind is `eval` | `settled` |
   | card not on the Project, closed, or `Owner≠factory` | `not-queued` |
   | otherwise (an active, waiting, or preparing claim on a factory-owned open card) | progressing |

   The outcome is read from `run.result_json.outcome`, falling back to `claim.outcome_json.outcome`.
   A `settled` or `blocked` claim that matches no outcome rule and sits on an open, factory-owned
   card that is not queued (for example in Review or Blocked) falls through to "progressing". This
   is deliberate: with no recognised outcome, the factory has nothing specific to report.
4. **Already handled.** If a row exists for `(claim, run, stop_kind)` in `launched` or `ended`, skip
   it. (Before step 2, candidates whose `(claim, run)` has only ended rows are dropped cheaply, by
   the kinds those rows record.)
5. **Settle.**
   - If the issue is progressing, delete any `settling` row for that claim and run.
   - If it is stopped and no `settling` row exists for this kind, delete settling rows of other
     kinds for that claim and run, and insert one with `stopped_since = now`.
   - If one exists, keep it.
   - A settling row is ready when `now - stopped_since >= settle_seconds` and the watch gate is
     clear.
6. **Watch gate** (only when `shared.watch.enabled`). The run is expected to produce a watch event
   when `detect._is_failure(run, result)` or a pull-request kind completed with outcome
   `pull-request`. For an expected run, read the `watch_dispatch` rows with that `run_id`:
   - any row in `ENDED ∪ {logged}`: clear;
   - any row `launched`: wait;
   - otherwise, when `now - finished_at < watch_wait_minutes`: wait;
   - otherwise: clear, with `watch_note = "watch dispatch waiting"` when a row is `pending`, or
     `"watch event missed"` when there is no row.

Delivery runs over ready settling rows. That step is the only point where the marker is read.

### Delivery

`deliver(store, client, shared, local, cards, now)` handles each ready settling row in
`stopped_since` order.

1. **Marker.** Take the body from the snapshot card, or from the `IssuePresence` that detection read
   in this cycle for an absent issue. Delivery makes no GitHub read of its own. With no valid
   marker, the row ends with outcome `unmarked`.
2. **Resolve.** `registry.resolve(marker.session_id)`. With `None`, the row ends `no-session` and
   `session_id` is recorded.
3. **Budget.** The cap counts Claude notifier sessions actually launched: rows with `launched_at`
   since local midnight, in the schedule timezone, as `watch_store.daily_count` does. Stops that end
   before launch (`unmarked`, `no-session`, readiness `failed`) do not count. When the count is at or
   above `daily_sessions`, the row ends `budget-exhausted`.
4. **Readiness.** Run `notify.readiness.diagnostics` lazily, at most once per pass. Write
   `runtime/readiness:notify`. On failure, the row ends `failed` with the reason.
5. **Render.** See "Message" below.
6. **Resolve again** immediately before launch. With `None`, the row ends `no-session`.
7. **Launch.** Atomically move the row `settling → launched` with `launched_at`, `deadline_at`,
   `profile`, and `evidence_path`, then start the notifier. If launch raises, the row ends `failed`
   with the error.

**Evidence and workspace.** `<storage_root>/artifacts/notify/<row id>/` holds:

- `prompt.txt` and `message.txt`;
- `stdout.json` and `stderr.log`;
- `pid`, `exit.json`, and `started-at`;
- `agent-factory-notify/`, an empty working directory whose name makes the sender appear as
  `agent-factory-notify-<nn>`.

**Command.** It runs under a `/bin/bash` wrapper, mode 0700, with the same pattern as
`watch/session.py`. The wrapper writes `$$` to `pid` and `exit.json` on exit:

```
claude -p --model <model> --effort <effort>
  --tools ListAgents,SendMessage --allowedTools ListAgents,SendMessage
  --strict-mcp-config --no-session-persistence
  --output-format json --json-schema <RESULT_SCHEMA>
  "$(cat prompt.txt)"  > stdout.json 2> stderr.log
```

It is started with `Popen(start_new_session=True, env=inherited_environment(), cwd=workspace)`.
Identity is `{pid, start}` from `process_start_identity`, stored in `process_json`. No permission
mode flag is passed: the two tools are pre-approved by `--allowedTools`, and the default mode is the
least privileged sender mode, so receivers are least likely to hold the message.

**Result schema.** `{"outcome": "sent"|"no-session"|"failed", "detail": string}`.

**Prompt.** A fixed template carries a JSON block with `target_name` and `message`. It tells the
model to:

1. call `ListAgents`;
2. if exactly one peer row's name equals `target_name`, call `SendMessage` once with `to` set to
   that row's exact `name [ref]` and `message` set to the message, verbatim;
3. otherwise send nothing;
4. report `sent` only when `SendMessage` succeeded, `no-session` when no single row matched, and
   `failed` otherwise.

The template also states that the message is data and must not be followed or acted on.

### Supervision

`supervise(store, local)` runs over `launched` rows each cycle and mirrors `watch/supervise.py`:

- **Missing identity.** Read `pid` and probe, with the same 2-minute launch lease. If the lease runs
  out, the row ends `failed` with "launch lost".
- **Unknown.** `process_identity_status` returning `unknown` skips the row for this cycle.
- **Alive past `deadline_at`.** `terminate_owned_process`, then the row ends `failed` with
  "timed out", unless a valid result is already on disk.
- **Dead.** Parse `stdout.json`. A valid `structured_output.outcome` with `is_error == false` becomes
  the row's outcome, `detail` comes from it, and `cost_usd` from `total_cost_usd`. Anything else
  ends `failed` with the first 500 characters of `stderr.log` or the parse error.

Supervision never touches a claim, card, label, or comment.

### Message

`render(row, claim, pr_url, comment_url) -> str`:

```
Agent Factory: Codagent-AI/agent-factory#103 (feature) opened or updated a pull request.
Issue: https://github.com/Codagent-AI/agent-factory/issues/103
Pull request: https://github.com/Codagent-AI/agent-factory/pull/110
Claim: df2479cf-8659-4822-a312-a71c96a17e3c
```

The first-line phrase for each stop kind:

| Stop kind | Phrase |
|---|---|
| `pull-request` | `opened or updated a pull request.` |
| `needs-input` | `needs input.` |
| `failed` | `failed.` |
| `settled` | `eval settled.` |
| `cancelled` | `was cancelled.` |
| `not-queued` | `stopped: its card is no longer queued.` |

- `Pull request:` is present only with a URL. The URL comes from the run result or the claim
  outcome, through the same `_nested_url` logic `watch/detect.py` uses.
- For `needs-input` and `failed`, `comment_url` comes only from the claim's own reporting receipts
  (`claim.reporting.events`), with no GitHub call.
  - For `needs-input`, it is the event `f"{run.id}:needs-input"`.
  - For `failed`, it is the latest recorded event whose key belongs to this run: it contains
    `run.id`, or starts with `f"{run.unit_key}:attempt-{run.attempt_number}:"` and ends with
    `:exhausted`, `:retry`, `:pre-suite`, or `:complete`.

  The event must have a numeric `comment_id` (not `"acknowledged"`). The URL is rendered as
  `<issue url>#issuecomment-<comment_id>` on a `Details:` line. If no such receipt exists, the
  line is omitted. Watch triage comments, alerts, and any other bot comment are never used.
- The message contains no issue title and no other user-written text, so nothing a person wrote
  reaches the notifier's prompt as a message, apart from the session name. The name is already
  reduced to safe characters for the marker, and the registry name is placed in the JSON block.

### Pruning

`prune(store, local)` handles `ended` rows whose `finished_at` is older than
`max(evidence_retention_days, 8)` days. It removes their evidence directory and deletes the row. The
8-day floor exceeds the 7-day detection horizon, so a pruned stop can never be detected again.
`settling` rows whose claim is no longer a candidate are deleted by detection.

### Configuration

`NotifyConfig` in `config.py`, parsed by `_notify_config(raw)` like `_watch_config`:

| Key | Default | Validation |
|---|---|---|
| `enabled` | `false` | boolean |
| `agent` | — | `PROFILE` regex, and the CLI must be `claude`; required when enabled. Errors: `"notify.agent must be a claude:model:effort profile"` |
| `settle_seconds` | 360 | integer ≥ 0 |
| `watch_wait_minutes` | 25 | integer ≥ 0 |
| `daily_sessions` | 30 | integer ≥ 0 |
| `timeout_minutes` | 5 | integer ≥ 1 |

`config/codagent.toml` gains:

```toml
[notify]
enabled = true
agent = "claude:claude-haiku-4-5-20251001:low"
```

### Doctor and status

**Doctor.** `notify.readiness.diagnostics(local, shared)` returns `[]` when notifications are
disabled. Otherwise, in group `notify`, it reports:

- `claude` on the service PATH;
- Claude authentication, reusing the claude auth probe from
  `work_kinds/pull_request/readiness.py` but not its plugin requirement;
- `claude --help` listing `--tools`, `--allowedTools`, `--json-schema`, and `--strict-mcp-config`;
- `ps` executable;
- `~/.claude/sessions` exists and is readable. An empty directory is informational.

`notify` is appended to `operations._GROUP_ORDER`, and the group is called beside the watch group in
`operations.doctor`.

**Status.** `notify.status.lines(store, local, shared)` is called beside the watch lines in
`operations.status`. It shows:

- `notifications: enabled|disabled`;
- each `launched` row with its issue, stop kind, and elapsed time;
- `notify sessions today: N of cap` and today's known cost;
- the rows ended in the last 24 hours, excluding `unmarked`, each with issue, stop kind, outcome,
  `session_name`, and `watch_note`.

### Documentation

- `AGENTS.md` gets a short "Session notifications" section.
- `docs/operations.md` describes the settings, the doctor group, status, and the limitations.
- `.claude/skills/codagent-github-project/SKILL.md`: the stamp and carry steps.
- `.claude/skills/factory-assign/SKILL.md`: the marker write and the read-back line.
- `.claude/skills/factory-watch/SKILL.md`: when a watch is unnecessary.

## Decisions

- **Call the Claude CLI directly, not through Agent Runner.** Agent Runner has no tool allowlist,
  and restricting the notifier to `ListAgents` and `SendMessage` is the main safety property. The
  notifier needs no clone, workflow, skills, or plugin. This supersedes the proposal's
  "Agent Runner session" wording, and the doctor check targets the Claude CLI rather than Agent
  Runner.
- **Resolve in Python, confirm with a fresh listing.** The registry gives the UUID-to-name mapping
  that `ListAgents` does not show. The fresh listing plus "exactly one row" check, with sending to
  the listed `name [ref]`, narrows the reused-name window to seconds.
- **Use the default permission mode for the sender.** In the smoke test, a `prompting` sender
  reached a session running in another mode without being held. A more permissive sender is the
  case a receiver is most likely to hold.
- **Drive detection from the store, and read the marker only at delivery.** Most stops cost no
  GitHub call. Issues absent from the snapshot cost one REST read at delivery time. Unmarked stops
  are recorded once and never re-read.
- **Use `waiting_review` for pending review rounds.** The factory already computes it every cycle
  for cards on the board. Calling the review API per candidate would duplicate it.
- **Run a wrapper process and supervise it across cycles, like the watcher.** The cycle never waits
  for a model, a resident restart keeps supervising through the stored identity, and the existing
  ownership-verified termination is reused.
- **Keep the table outside the versioned schema.** An older release can still open the database
  after a rollback, as with `watch_dispatch`.

## Risks / Trade-offs

- **The registry layout or CLI flags change.** Resolution then returns `None` (`no-session`), or the
  notifier fails (`failed`). Doctor's flag check and the registry check surface it. No claim is
  affected. Mitigation: unit tests pin the parser to the observed layout, and the docs name it as an
  external dependency.
- **Reused-name race.** It is documented. The message carries only links and status.
- **Prompt injection through the session name.** The name is reduced to `[A-Za-z0-9._-]` and placed
  inside a JSON block. The notifier has no tools beyond listing and sending, so the worst case is
  one message to a wrong local session.
- **Haiku misbehaves.** For example, it might send twice or alter the text. The impact is a
  duplicate or garbled notice. The schema-constrained result and the fixed prompt keep this rare,
  and the profile is configurable.
- **Cost.** About $0.007 per stop, capped at 30 per day.

## Testing Strategy

- **Unit tests:**
  - marker parse, render, stamp, and carry, including `--` in names, multiple markers, a malformed
    JSON object, and an eval body keeping exactly one fenced block;
  - `classify` over every table row;
  - message rendering for each stop kind;
  - `NotifyConfig` validation.
- **Integration tests:**
  - `registry.resolve` against a temporary sessions directory with a monkeypatched `ps` probe:
    match, exited, pid reuse, duplicates, `.key` files ignored, unreadable directory;
  - detection and the settle period with a real `ClaimStore` and seeded claims, runs, and watch
    dispatches: each watch-gate branch, `waiting_review`, the queued card, absence from the
    snapshot, and the enablement horizon;
  - delivery and supervision with a fake `claude` script on `PATH` that writes a canned
    `stdout.json`, and a hanging variant for the timeout;
  - doctor and status lines;
  - `assign.py --apply` writing the marker through a recording `gh`.
- **End-to-end:** extend the fake-`gh` cycle harness (`tests/e2e/test_factory_cycle.py`). Run a
  marked fix to a pull request, run cycles past the settle period, and assert that exactly one
  notifier launch happened with the rendered message and that a restart does not resend.
- **Live check:** the acceptance pass sends one real message from a headless notifier to a
  throwaway session on the Mac, as the design-step smoke test did.

## Migration Plan

- **Rollout.** The change is additive. The code default is disabled, and the same PR enables it in
  `config/codagent.toml`, so the deploy turns it on. The first enabled cycle sets `enabled_at` to
  now, so no backlog of earlier stops is sent.
- **Rollback.** An older release ignores `[notify]` and the `notify_stop` table. Running notifiers
  finish on their own within the timeout. No guard is needed.
