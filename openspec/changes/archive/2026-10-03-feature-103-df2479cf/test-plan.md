## Coverage Strategy

The specifications remain the source of unit-test requirements. This plan records only the extra
obligations: integration and end-to-end tests, the envelope for the acceptance pass, and any
human-only checks.

Unit tests cover the logic that needs no real boundary:

- marker parse, render, `stamp`, and `carry`, including names with `--` or `>`, several markers, a
  malformed JSON object, and a non-UUID `session_id`;
- `classify` for every row of the design's classification table;
- message rendering for each stop kind, with and without a pull request and a details comment;
- `NotifyConfig` validation and its error messages;
- the result-schema parser for `stdout.json`.

The obligations below cover the boundaries where unit tests would rely on mocks:

- the real Claude Code session registry layout and the real `ps` start-time probe;
- the marker CLI run as a subprocess, as the skills run it;
- `assign.py` writing to GitHub in the right order;
- the SQLite store, across restarts and next to `watch_dispatch`;
- the notifier subprocess, its argv, environment, timeout, and identity-verified termination;
- the `doctor` and `status` wiring;
- the full `agent-factory tick` path through `_watch_finally`.

A fake `claude` executable on `PATH` stands in for the Claude CLI in every automated test. It
records its argv, environment, and working directory, and writes a canned `stdout.json`. Only the
acceptance pass uses the real CLI.

## Integration Tests

### INT-001: Registry resolution against real processes

- **Covers:** `factory-session-notification` "Resolve the recorded session to one live local
  session": renamed, ended, pid reused, two matches, unreadable registry.
- **Boundary:** `registry.resolve` reading a temporary sessions directory, with the real
  `TZ=UTC ps -o lstart= -p <pid>` probe.
- **Setup:** a temporary `sessions/` directory with these entries:
  - `<pid>.json` for the test process itself, with `procStart` taken from the real `ps` output;
  - one for a child process that has already exited and been reaped;
  - one whose `procStart` differs from the live process (pid reuse);
  - two files with the same `sessionId` and live pids;
  - a `<pid>.<hash>.key` file that is a directory or has mode 000, so opening it would raise;
  - a malformed JSON file.

  A second case uses a sessions directory with mode 000 or a path that does not exist.
- **Action:** call `resolve(uuid, root=...)` for each UUID.
- **Assertions:**
  - The live match returns the current `name`, including after the file's `name` is rewritten
    between calls.
  - The ended, reused, and duplicate cases each return `None`.
  - The `.key` path is never opened (the call does not raise).
  - The unreadable directory returns `None` without raising.
- **Execution:** `tests/integration/test_notify_registry.py` under `uv run pytest`.

### INT-002: Marker CLI as the skills invoke it

- **Covers:** "Record the originating session when the board skill creates an issue": with and
  without a session, edit keeps the marker, an eval request still parses. Also "Replace the
  recorded session": the marker form that is written.
- **Boundary:** `python -m agent_factory.notify.marker stamp FILE` and `carry OLD NEW`, run as
  subprocesses with the test interpreter, a controlled environment, and `HOME` pointed at a
  temporary registry.
- **Setup:**
  - body files: plain prose, a body that already has a marker for another UUID, and the shipped
    executable eval-request template body;
  - a registry entry for the test process's own pid, holding the session UUID and the name
    `agent-factory-ab`.
- **Action:**
  - run `stamp` with `CLAUDE_CODE_SESSION_ID` set, and again with it unset;
  - run `carry` from a marked old body into an edited new body that has no marker.
- **Assertions:**
  - The set case leaves exactly one marker line, holding that UUID and name, and every other byte of
    the body unchanged.
  - The unset case leaves the file unchanged and exits 0.
  - `carry` copies the old marker byte for byte.
  - The stamped eval body still contains exactly one fenced eval TOML block, and `parse_request`
    accepts it.
- **Execution:** `tests/integration/test_notify_marker_cli.py`.

### INT-003: Assign writes the marker before handing off

- **Covers:** "Replace the recorded session when assigning to the factory": all three scenarios
  and the read-back line.
- **Boundary:** `assign.py` `apply` and `report`, using recording fakes for the app client and
  Paul's `gh`. This follows the existing `tests/unit/test_factory_assign.py` loading pattern.
- **Setup:** a fake factory whose issue body carries session A's marker, and a recorder that keeps
  every GitHub write in order. `CLAUDE_CODE_SESSION_ID` is set to session B.
- **Action:**
  - `apply` for a feature;
  - `apply` for an issue that has a live claim (refused);
  - `apply` with the variable unset;
  - `report`.
- **Assertions:**
  - The body PATCH, which holds exactly one marker for B and an otherwise unchanged body, is
    recorded before the Owner and Status writes.
  - The refused apply and the unset apply make no body write.
  - `report` prints `session: <name> (<uuid>)` or `session: none`.
- **Execution:** `tests/unit/test_factory_assign.py`, extended.

### INT-004: Detection, settle period, and the watch gate in the real store

- **Covers:**
  - "Detect when the factory stops progressing on a marked issue": all scenarios;
  - "Wait for the service watcher before notifying": all scenarios;
  - "Deliver one notify-only message per stop": same stop seen again, and a second stop after a
    review round;
  - "Stop notifying without losing records": re-enable after a pause.
- **Boundary:** `notify.step` (detect plus deliver) against a real `ClaimStore`, with real claims,
  runs, `consumed-results`, `watch_dispatch` rows, and `notify_stop`. The clock is injected.
  `session.start` is replaced by a recorder, and `registry.resolve` by a stub returning a live
  session.
- **Setup:**
  - seeded claims for each stop kind: a fix that ended with a pull request, a feature blocked on
    `needs-input`, a failed run, an eval between repetitions, a settled eval, and a cancelled
    claim;
  - snapshot cards built as `ProjectQueueItem`s carrying marked and unmarked bodies;
  - one marked card absent from the snapshot, served by a fake `get_issue_presence` that can raise,
    report the issue as off the Project, or report it as still on the Project;
  - watch dispatch rows in each state;
  - `waiting_review` set on one claim's outcome.
- **Action:** run `step` repeatedly while advancing the clock past `settle_seconds` and
  `watch_wait_minutes`. Close and reopen the store between some steps, and toggle `enabled`.
- **Assertions:**
  - Exactly one launch per `(claim, run, stop_kind)`, with the expected kind.
  - No launch before the settle period, while `waiting_review` is set, while the card is queued,
    for the active eval, or for the unmarked issue (recorded `unmarked`, and its presence is
    not read again).
  - A raising `get_issue_presence` stops classification. After an outage longer than
    `settle_seconds`, the first successful read restarts the clock, and the launch happens only a
    full settle period later. A skipped-detection cycle (`cards=None`) has the same effect.
  - An absent issue that still reports Project membership is never classified `not-queued`.
  - A run that finishes in the first enabled cycle is eligible: the test seeds the cursor through
    `notify.begin` before consuming the run, then settles it and expects one launch.
  - Delivery for an absent issue uses the body detection read and makes no further GitHub call.
  - A failing run with watching on waits for its dispatch to end. A missing dispatch is notified
    after the wait with `watch event missed`, and a pending one with `watch dispatch waiting`.
    With watching off there is no wait.
  - Runs from before enablement or re-enablement are never notified, and a reopened store does not
    resend.
  - `cards=None` skips detection, and supervision still runs.
  - Claims, runs, and outcomes are byte-identical before and after.
- **Execution:** `tests/integration/test_notify_detection.py`.

### INT-005: Notifier launch, supervision, and termination

- **Covers:**
  - "Deliver one notify-only message per stop": tools, profile, structured outcome, daily cap;
  - "Never let notification affect a claim": registry unreadable, delivery hangs;
  - "Stop notifying without losing records": disable while a delivery runs.
- **Boundary:** the real wrapper script and `Popen` with a fake `claude` on `PATH`, plus
  `supervise` with the real `process_identity_status` and `terminate_owned_process`.
- **Setup:** a fake `claude` script with these modes, chosen through a file it reads:
  - `sent`;
  - `no-session`;
  - `is_error: true`;
  - unparseable stdout;
  - sleep forever.

  The parent environment has dummy `GH_TOKEN` and `GITHUB_TOKEN` values. `timeout_minutes` is
  forced short through the deadline.
- **Action:** launch one notifier for each mode, then run `supervise` until each row ends, using a
  new `ClaimStore` instance for the second pass to simulate a resident restart.
- **Assertions:**
  - argv contains:
    - `-p`;
    - `--model <m>` and `--effort <e>` from the profile;
    - `--tools ListAgents,SendMessage` and `--allowedTools ListAgents,SendMessage`;
    - `--strict-mcp-config`, `--no-session-persistence`, `--output-format json`, and
      `--json-schema`;
    - no `--permission-mode`.
  - The prompt contains the rendered message verbatim and the target name in its JSON block.
  - The environment contains no `GH_TOKEN` or `GITHUB_TOKEN`.
  - The working directory is named `agent-factory-notify`.
  - Each mode ends as expected: `sent`, `no-session`, `failed`, `failed`, and `failed` (timed out).
    The hanging process is gone after supervision.
  - `cost_usd` is recorded from `total_cost_usd`.
  - Once the day's launches reach `daily_sessions`, the next stop is `budget-exhausted` with no
    process started.
  - Disabling notifications mid-run still ends the running row.
- **Execution:** `tests/integration/test_notify_session.py`.

### INT-006: Configuration, doctor group, and status wiring

- **Covers:**
  - `factory-operations` "Configure session notifications";
  - "Diagnose notification readiness";
  - "Report session notifications in status".
- **Boundary:** `SharedConfig.from_file` with real TOML, then `operations.doctor` and
  `operations.status` over a real store, with fake `claude` and `ps` executables on `PATH` and a
  temporary `HOME`.
- **Setup:** configurations as follows:
  - no `[notify]` section;
  - `agent = "codex:gpt-5:low"`;
  - `agent = "sonnet"`;
  - a valid configuration.

  For the valid one, a fake `claude` whose auth probe fails, then one whose `--help` lacks
  `--json-schema`, then one that passes. A missing `~/.claude/sessions`, then an empty one.
  Seeded `notify_stop` rows: two `sent`, one `no-session`, and one `unmarked` in the last 24 hours,
  plus one `launched`.
- **Action:** load each configuration; run `doctor` and `status`.
- **Assertions:**
  - The invalid profiles fail and name `notify.agent`.
  - The missing section gives a `notifications: disabled` line and no notify doctor group.
  - The doctor group lists the failing check with an action and passes otherwise. An empty registry
    is informational.
  - Status shows the launched row, the three recent ended rows (`unmarked` is hidden), and
    `3 of <cap>` launched sessions with their cost. The two `sent` rows and the launched row count;
    the `no-session` row does not.
  - The checked-in `config/codagent.toml` parses with notifications enabled and the profile
    `claude:claude-haiku-4-5-20251001:low`.
- **Execution:** `tests/integration/test_notify_operations.py`, with config cases in
  `tests/unit/test_notify_config.py`.

### INT-007: Additive, rollback-safe schema

- **Covers:** design migration plan: an older release can still open the database.
- **Boundary:** `ClaimStore` opening an existing v4 database file.
- **Setup:** a v4 database file with seeded `claim` and `run` rows and no `notify_stop` table (drop
  it after creation), with its `PRAGMA user_version` recorded.
- **Action:** open with the new `ClaimStore` twice.
- **Assertions:**
  - `notify_stop` and its index exist.
  - `PRAGMA user_version` is unchanged (`SCHEMA_VERSION` not bumped).
  - Opening twice is idempotent.
  - Existing `claim` and `run` rows are untouched.
- **Execution:** `tests/integration/test_notify_store.py`.

## End-to-End Tests

### E2E-001: A marked issue's pull request notifies its session once through `tick`

- **Covers:**
  - the critical journey from claim to stop, settle, and one notifier launch to a recorded outcome,
    through the public `agent-factory tick` and `status` commands;
  - a cycle whose board read fails still supervises a running notifier through `_watch_finally`;
  - notification while paused.
- **Surface:** the `agent-factory` CLI, run as a subprocess by the existing fake-`gh` board harness
  (`tests/e2e/test_factory_cycle.py` `_setup` and `_cli`).
- **Setup:**
  - a board holding:
    - one marked fix issue (marker for a UUID in a temporary registry whose entry points at a live
      helper process with the correct `procStart`);
    - one unmarked fix issue;
  - `[notify] enabled`, `settle_seconds = 0`, watching disabled;
  - a fake `claude` that writes a `sent` result;
  - the fix run's completed pull-request result seeded as in the existing fix cycle tests.
- **Journey:**
  1. `tick` until both claims settle with pull requests.
  2. `tick` again, then `pause`, then `tick` twice more.
  3. `status`.
  4. One more `tick` with `validate_project` forced to raise.
- **Assertions:**
  - The fake `claude` ran exactly once, for the marked issue only. Its message's first line names
    `o/r#<n> (fix)` and "opened or updated a pull request", followed by the issue URL, pull request
    URL, and claim id, and no other lines.
  - The row ends `sent`, and later ticks (including while paused) launch nothing more.
  - `status` lists the notification.
  - The board-failure tick still runs notify supervision.
  - Neither issue's card, labels, or comments changed because of notification.
- **Execution:** `tests/e2e/test_notify_cycle.py` under `uv run pytest`.

No further E2E test is planned. The watch gate, classification breadth, and failure handling are
covered faithfully at INT-004 and INT-005 without the cost of the CLI harness.

## Acceptance Testing Envelope

- **Environments and sandboxes:**
  - this Mac, with the change's worktree and its `uv` environment;
  - a temporary storage root and temporary local configuration;
  - the fake-`gh` board harness from the E2E suite;
  - the real `claude` CLI on `PATH` and the real `~/.claude/sessions` registry (read only).
- **Credentials and secrets:** Paul's existing Claude CLI login, used only by notifier processes.
  No GitHub credential is needed or allowed: the board is the fake harness. Never read or print
  `~/.claude/sessions/*.key`, `~/.agent-factory/private/`, or tokens.
- **Authorized effects:**
  - Up to 10 real notifier launches (Haiku 4.5, about $0.01 each). They may send cross-session
    messages only to the acceptance session itself, or to throwaway Claude sessions the pass starts
    and then ends.
  - Temporary files under the temporary storage root, removed afterwards.
- **Off limits:**
  - the live factory service, its LaunchAgent, `~/.agent-factory/state.sqlite3`, releases,
    `config.toml`, and deploys;
  - the real Codagent board and any real GitHub issue, comment, or pull request (no marker writes on
    real issues);
  - messages to any of Paul's existing sessions;
  - `config/codagent.toml` edits beyond the committed `[notify]` section.
- **Permitted substitutes:**
  - the fake `gh` board for GitHub;
  - a fake `claude` for runs that only check orchestration;
  - when the real CLI is unavailable or logged out, the pass reports that it could not perform live
    delivery rather than substituting silently.
- **Known risk areas:**
  - The dependency on the registry layout (`sessionId`, `name`, `pid`, and `procStart` as
    `TZ=UTC` lstart) and on the Claude CLI flags (`--tools`, `--allowedTools`, `--json-schema`).
  - The seconds-long reused-name race (an accepted limitation).
  - A receiver in a different permission mode may hold messages (an accepted limitation; the
    outcome is still `sent`).
  - Haiku may deviate from the fixed prompt, for example by altering the text or sending twice.
  - Ordering inside `_watch_finally` and the snapshot holder.
  - The watch-gate timing against the watcher's grace period.
  - The 8-day prune floor.
  - The skills' marker steps when the current release predates the change, which must degrade to
    no marker.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Record the originating session when the board skill creates an issue | INT-002 | — | — |
| Replace the recorded session when assigning to the factory | INT-002, INT-003 | — | — |
| Detect when the factory stops progressing on a marked issue | INT-004 | E2E-001 | — |
| Wait for the service watcher before notifying | INT-004 | — | — |
| Resolve the recorded session to one live local session | INT-001 | E2E-001 | — |
| Deliver one notify-only message per stop | INT-004, INT-005 | E2E-001 | — |
| Never let notification affect a claim | INT-004, INT-005 | E2E-001 | — |
| Stop notifying without losing records | INT-004, INT-005 | — | — |
| Configure session notifications | INT-006 | — | — |
| Diagnose notification readiness | INT-006 | — | — |
| Report session notifications in status | INT-006 | E2E-001 | — |
| Rollback-safe additive schema (design) | INT-007 | — | — |
