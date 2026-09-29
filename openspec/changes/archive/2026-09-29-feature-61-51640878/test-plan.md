## Coverage Strategy

The specifications remain the source of unit-test requirements. This plan records only the extra
integration and end-to-end obligations, the envelope for acceptance testing, and any human-only
obligations.

Unit tests cover:

- `[watch]` parsing and validation;
- the event window arithmetic: grace period, overlap, enablement bound, and ISO parsing;
- the ordering of pending dispatches, and the one-review-per-PR and cap rules;
- budget-day boundaries in the schedule timezone;
- result validation, including forged-marker stripping and length caps;
- comment rendering;
- parsing usage coverage from `run-metrics.json`;
- the redispatch state checks;
- that the headless section of `factory-pr-reviewer.md`, and the brief, name no
  `/Users/paul/codagent/` path and no `releases/current` path;
- status line formatting.

The obligations below cover what a unit test with doubles cannot prove:

- **Concurrency:** real SQLite transactions and compare-and-set between processes.
- **Process lifetime:** real detached processes, process groups, and adoption after restart.
- **Git:** real clones from a bare mirror.
- **Packaging:** the packaged workflow against the installed Agent Runner.
- **The GitHub boundary:** comments delivered exactly once.
- **The full journey:** the `agent-factory tick` → `status` → `watch redispatch` path through the CLI.

The automated tests make no model calls. A stand-in `agent-runner` on `PATH` behaves like a
session. It writes `pid`, `watch-result.json`, and
`agent-runner-session/run-metrics.json`, can block until released, and can spawn a
grandchild. The tests reuse the existing fixtures in `tests/e2e/test_factory_cycle.py`: the
`gh` stub with a board file, the token stub, and `_cli`. The `gh` stub is extended to record
issue and PR comments in a JSON file, and to answer `api user` and
`api repos/<repo>` for the writer-login check.

## Integration Tests

### INT-001: Detection and launch are exactly once across concurrent processes
- Covers: Queue each event exactly once (all scenarios); Detect watch events in every cycle
  (window and enablement rules).
- Boundary: a real SQLite file in WAL mode, shared by two OS processes. Each process runs
  `detect` and `dispatch` through `ClaimStore`, with the real `BEGIN IMMEDIATE`
  transactions and the `pending` → `launched` compare-and-set.
- Setup: a temporary state database with a claim, a failed run whose `finished_at` is older
  than the grace period, and a completed fix run with a `pull-request` result. The stand-in
  launcher records every launch to a file.
- Action:
  1. Two processes run detection and dispatch concurrently, many times in a loop.
  2. One process stamps a new `finished_at` inside its own transaction while the other
     detects.
- Assertions:
  - Each event key has exactly one row with `attempt = 1`.
  - The launch log has exactly one entry per session event.
  - A run stamped during detection is queued in the same pass or the next, and is never
    passed over.
  - The cursor never moves backwards.
  - A failure detected before enablement, or inside the grace period, is not queued.
  - **Grace changes** (approach review RA-1): a failure five minutes old in a cycle with a
    7-minute grace is queued exactly once in the next cycle after the grace is set to 0.
    With the grace raised to 15 minutes, it is queued exactly once, and not before 15
    minutes. A failure older than 7 days, or from before enablement, is never queued.
- Execution: `tests/integration/test_watch_detection.py`, under `uv run pytest`.

### INT-002: The watch table leaves the schema version and older readers intact
- Covers: Migration Plan (rollback); design decision D24.
- Boundary: `ClaimStore` opening a real database file, and a store that opens it with
  `SCHEMA_VERSION` 4.
- Setup: a version-4 database from the current fixtures that has claims and runs.
- Action:
  1. Open it with the new code, and insert dispatch rows.
  2. Reopen it with a `ClaimStore` whose `_migrate` refuses newer versions, as the
     previous release's does.
  3. Open a fresh database with the new code.
- Assertions:
  - `PRAGMA user_version` stays 4.
  - The older reader opens the file without error, and its claim and run queries return
    the same rows.
  - A second open by the new code is idempotent.
  - A fresh database has both the table and its index.
- Execution: `tests/integration/test_watch_store.py`.

### INT-003: The session launch runs in a throwaway clone with the operator environment
- Covers: Run dispatched sessions fresh and bounded (checkout, environment, and removal);
  Record each dispatched session's usage.
- Boundary: the real `session.start`, which covers the bare-mirror clone, workflow and
  profile staging, the brief, the wrapper script, and a detached `Popen`. Real `git`,
  `bash`, and process identity are used. The stand-ins are `agent-runner` and the audit
  runner.
- Setup:
  - A bare mirror fixture at `<root>/mirrors/<owner>__<repo>.git` with `main`, and one
    `pending` `FAILURE` dispatch.
  - The parent environment includes `GH_TOKEN`.
  - The stand-in `agent-runner` records its `argv`, the names of its environment
    variables, `cwd`, and `git remote get-url origin`. It then writes a valid result and
    `run-metrics.json` with complete coverage.
- Action: dispatch the row, wait for the wrapper to exit, then run `supervise`.
- Assertions:
  - The runner ran in `<root>/clones/watch/<id>/repo` at the mirror's `main` SHA, with
    `origin` set to the GitHub URL.
  - The `.agent-runner/` staging is untracked and excluded.
  - The runner received `--profile factory`, `--session-dir`, and the three
    parameters.
  - The environment names hold no `GH_TOKEN`, `GITHUB_TOKEN`, or `GIT_CONFIG_GLOBAL`.
  - The brief holds no `allowed_environment`.
  - `E/pid`, `E/started-at`, and `E/exit.json` exist.
  - The row is `completed`, with `usage_json` tokens and cost equal to the metrics.
  - The audit stand-in was called with `--project` pointing at the clone before it was
    removed.
  - The clone directory is gone.
  - When the metrics report partial coverage, tokens and cost are `null` with the coverage
    label.
- Execution: `tests/integration/test_watch_session.py`. It needs process-session semantics
  and is marked `darwin` where the existing host tests are.

### INT-004: Supervision handles a timeout, a dead process, and a resident restart
- Covers: Run dispatched sessions fresh and bounded (timeout, reconciliation, and no
  relaunch); Alert on a dispatch that did not finish.
- Boundary: real detached process groups, `process_identity_status`, and
  `terminate_owned_process`.
- Setup: the stand-in runner blocks and spawns a grandchild that records its pid. The
  timeout is short.
- Action and assertions, one case each:
  1. **Timeout.** Advance the clock past `deadline_at` and run `supervise`. The session
     and its grandchild are gone, the row is `timed-out`, one `alert` delivery is queued,
     the audit is `missing`, and the clone is removed.
  2. **Restart with the process alive.** Clear `process_json`, as if the resident died
     between the spawn and the record, and run `supervise` from a new process. The
     identity is adopted from `E/pid`, and the row stays `launched`.
  3. **Restart with the process dead and no result.** Kill the process and run
     `supervise`. The row is `interrupted` with its detail and one `alert`, and the
     launch log shows no second launch.
  4. **Invalid result.** The process exits 0 after writing an invalid result. The row is
     `interrupted` with the detail "invalid result".
  5. **Crash before the spawn** (RA-4). The row is `launched`, with `process_json` empty
     and no `E/pid`.
     - Within the 2-minute launch lease, `supervise` leaves it `launched`.
     - Past the lease, `supervise` records it `launch-failed` with "launch did not
       complete" and one `alert`. It no longer counts toward the cap, and the launch log
       shows no session.
  6. **Crash just after the spawn.** A real wrapper is spawned and its row keeps
     `process_json` empty. The wrapper's `E/pid` exists before the Runner stand-in's first
     recorded action, and `supervise` adopts the identity instead of failing the row.
- Execution: `tests/integration/test_watch_supervision.py`, marked `darwin`.

### INT-005: Comment delivery is exactly once to an issue or a PR
- Covers: Deliver dispatch comments exactly once (all scenarios); the triage, decisions,
  budget, and alert comments.
- Boundary: `watch.deliver` through the real `GitHubClient`, against the `gh` stub
  (`issues/{n}/comments`, with pagination).
- Setup: a completed review dispatch with decisions, a completed triage dispatch, and a
  `budget-exhausted` dispatch. The target PR already has 150 comments.
- Action:
  1. Deliver once with the stub failing `POST`, then deliver again.
  2. Separately, place a bot comment with the marker on page 2, then deliver.
  3. Place the same marker in a comment by a non-bot author, then deliver.
- Assertions:
  - After the failure, the failure reason is recorded and nothing is posted. The retry
    posts exactly one comment.
  - The marked bot comment on page 2 is adopted without posting.
  - A marker in a non-bot comment is not adopted.
  - The decisions comment goes to the PR number and mentions `@<operator>`. The triage,
    budget, and alert comments go to the claim's issue number.
  - Repeated cycles post nothing more.
- Execution: `tests/integration/test_watch_delivery.py`.

### INT-006: The doctor `watch` group and the launch gate share one check
- Covers: the modified Diagnose readiness with doctor requirement (watch group scenarios);
  the readiness gate in Run dispatched sessions.
- Boundary: `agent-factory --config … doctor` as a subprocess, with stand-in
  `agent-runner`, `claude`, and `gh` on `PATH`. Then one `tick` with a `pending` session
  dispatch.
- Setup: the `gh` stub answers `api user` with the operator login, the bot login, or an
  error, and `permissions.push` with true or false.
- Action: run doctor for each variant and for watching disabled, then run `tick` with the
  failing variant, then with the passing one.
- Assertions:
  - A bot login, no push access, or a failed `gh` fails the `watch` group, names the login
    problem, and exits 1, while the other groups report independently.
  - With watching disabled, no `watch` group is printed.
  - With a failing group, `tick` leaves the dispatch `pending` and status shows the
    readiness reason. With a passing group, the next `tick` launches it.
- Execution: `tests/integration/test_watch_readiness.py`.

### INT-007: The packaged workflow is accepted by the installed Agent Runner
- Covers: Record each dispatched session's usage (the Runner metrics path); design
  decision D26.
- Boundary: the packaged `factory-watch-v1.0.yaml` and staged scripts, run by the real
  installed `agent-runner`. The workflow is validated. A model-free copy runs where the
  `watch` agent step is replaced by a command step that writes a result.
- Setup: skipped with the existing `SKIP` reason when `agent-runner` with `--session-dir`
  is not installed, the same pattern as `tests/e2e/test_host_fix_launch.py`.
- Action: stage the workflow into a temporary clone and run the wrapper's
  `agent-runner run factory-watch …` command.
- Assertions:
  - The contract marker is the first line.
  - The Runner accepts the workflow and its parameters.
  - `check-result` fails the run when `watch-result.json` is missing or is not a JSON
    object, and passes otherwise.
  - The session directory holds a `run-metrics.json` with a `totals` object.
- Execution: `tests/integration/test_watch_workflow.py`.

## End-to-End Tests

All of these run through the public CLI (`agent-factory --config <tmp>/local.toml tick`,
`status`, and `watch redispatch`) with the `_setup` and `_cli` fixtures from
`tests/e2e/test_factory_cycle.py`. The configuration is `config/codagent.toml` with `[watch]`
set to a temporary repository, a short grace period and timeout, and a small cap and budget.
A bare mirror fixture stands in for the watch repository. Time moves forward by editing stored
timestamps, or by the entry-point clock hook that `_cli` already uses.

### E2E-001: A quiet day starts no session
- Covers: Detect watch events (quiet-day scenario); issue acceptance "no events, zero
  sessions"; Log claim and eval-completion events (no session).
- Surface: repeated `tick`, then `status`.
- Setup: watching enabled, a board with no Ready cards, and a stand-in runner that
  records every call.
- Journey:
  1. Run many `tick`s spread across a simulated day.
  2. Admit one Bug card, so a `CLAIM` event occurs.
  3. Run another `tick`.
- Assertions:
  - The stand-in runner was never called.
  - The `CLAIM` dispatch is `logged`, and the service log names it.
  - No comment was posted.
  - `status` shows "watch sessions today: 0/…".
- Execution: `tests/e2e/test_watch_cycle.py`.

### E2E-002: One failure gives exactly one triage session and one comment, across a restart
- Covers: Triage a failure; Queue each event exactly once; Deliver dispatch comments exactly
  once; issue acceptance "simulated FAILURE, exactly one triage session and one issue comment,
  across a resident restart".
- Surface: `tick` subprocesses (each one a fresh resident process), and `status`.
- Setup: a claim with a Fly-eval-style run recorded `failed`. The stand-in runner blocks
  until released, then writes a triage result with `owner = "transient"`.
- Journey:
  1. Run `tick` inside the grace period.
  2. Run `tick` after the grace period.
  3. Run `tick` twice more while the session runs.
  4. Release the stand-in, then run `tick`.
  5. Run `tick` again, after first deleting the recorded `comment_id` to simulate a crash
     after posting.
  6. Run `tick` again.
- Assertions:
  - Step 1 detects nothing, and step 2 launches exactly one session.
  - `status` shows it running with its profile.
  - Step 4 posts exactly one bot comment on the claim's issue. The comment carries the
    cause, "transient", and "Factory paused: no".
  - Step 5 adopts the existing comment instead of posting.
  - The runner call log has one entry.
  - The comment file has one triage comment.
- Execution: `tests/e2e/test_watch_cycle.py`.

### E2E-003: A ready PR gives one review session and one decisions comment
- Covers: Review a ready pull request (all scenarios); issue acceptance "simulated PR-READY,
  exactly one factory-pr-review session"; Configure service-driven watching (per-event
  profile).
- Surface: `tick` and `status`.
- Setup:
  - A settled fix claim whose run completed with `pull-request` and a PR URL.
  - A per-event profile for `FAILURE` that differs from the default.
  - A stand-in runner that blocks, then writes a review result with two decisions.
- Journey:
  1. Run `tick`.
  2. Add a second completed review-round run for the same PR, then run `tick`.
  3. Release the first session, then run `tick` twice.
  4. Release the second session with no decisions, then run `tick`.
- Assertions:
  - Step 1 launches one session, and its brief names the PR, kind, reason, and run id.
  - The staged profile is the default one, not the `FAILURE` profile.
  - In step 2 the second dispatch stays `pending`, and status explains why.
  - Step 3 posts one bot comment on the PR, with both decisions and `@<operator>`, then
    launches the second session.
  - Step 4 posts nothing.
  - No comment is posted on the issue.
- Execution: `tests/e2e/test_watch_cycle.py`.

### E2E-004: The budget, a timeout alert, redispatch, and disabling
- Covers: Bound sessions with a daily budget; Alert on a dispatch that did not finish; the
  Redispatch a watch event scenarios; Stop watching without losing queued events; Report
  watch dispatches in status; Prune dispatch evidence.
- Surface: `tick`, `status`, and `watch redispatch`.
- Setup: a budget of 1, a cap of 1, and two failed runs on different claims. A variant uses
  a budget of 0 (RA-3).
- Journey:
  1. Run `tick`. The first triage launches.
  2. Let it pass the timeout, then run `tick`.
  3. Run `tick` so the second failure meets the spent budget.
  4. Run `watch redispatch` on the `launched` row of a new third event, and on the
     `timed-out` row.
  5. Advance the local day, then run `tick`.
  6. Set `enabled = false`, add a new failure, and run `tick`.
  7. Set `enabled = true` again, and run `tick`.
  8. Age the ended rows past retention, then run `tick`.
- Assertions:
  - Step 2 records `timed-out` and posts one alert with the evidence path and the
    redispatch command.
  - Step 3 records `budget-exhausted` and posts one budget comment.
  - Step 4 refuses the `launched` row, names its state, and exits non-zero. For the
    `timed-out` row it prints a new id.
  - Step 5 launches the redispatched attempt.
  - Step 6 queues nothing new, and the `pending` row stays `pending`.
  - Step 7 does not detect the failure from the disabled period. The earlier `pending` row
    is processed.
  - Status lists the running, ended, and undelivered items, and the day's count.
  - Step 8 removes the evidence directories and keeps the rows.
  - **Budget-of-0 variant**, with the `gh` stub failing the writer-login check and one
    stand-in session holding the only cap slot. Every new `PR-READY` and `FAILURE` is
    recorded `budget-exhausted` and its notice posted in the same `tick`. Nothing waits
    `pending`, and no session launches.
- Execution: `tests/e2e/test_watch_cycle.py`.

### E2E-005: Watching runs even when the cycle's GitHub work fails
- Covers: design decision D31 (watch step in `finally`); Detect watch events ("An error
  during detection SHALL be logged … SHALL NOT stop the rest of the cycle").
- Surface: `tick`.
- Setup: a `gh` stub that fails project validation, and one failed run past the grace
  period.
- Journey: run `tick`, which is expected to exit with the GitHub error. Restore the stub,
  then run `tick` again.
- Assertions:
  - The first `tick` still launches the triage session, and its comment delivery records
    a failure.
  - The second `tick` delivers the comment once.
  - A detection error injected through `before_cli` leaves the cursor unchanged, and the
    cycle's other steps still run.
- Execution: `tests/e2e/test_watch_cycle.py`.

## Acceptance Testing Envelope

- **Environments and sandboxes:** Paul's Mac, inside the feature attempt's own clone. Use an
  isolated storage root: a temporary directory with its own `local.toml`, `state.sqlite3`,
  mirrors, clones, and artifacts. The fixtures from `tests/e2e/test_factory_cycle.py`
  provide the stub GitHub board and comment recorder. Build a bare mirror of this
  repository's current branch in that root to act as `[watch] repository`.
- **Credentials and secrets:**
  - Claude and Codex logins exist in the operator's Keychain and CLI config.
  - The operator's `gh` login exists.
  - The Factory App key and the fix credential exist under `~/.agent-factory/private/`.
  - The pass MUST NOT read or print anything under `~/.agent-factory/private/`, and MUST
    NOT use the App key.
- **Authorized effects:**
  - Any number of runs with the model-free stand-in `agent-runner`.
  - Up to two real headless sessions through the installed `agent-runner` and
    `claude:claude-sonnet-5-5:medium`, at an estimated $5 in total, to confirm that the
    workflow, skills, reviewer agent, and result schema work together. One is a review
    brief and one a triage brief, against the isolated root.
    - They run with a `gh` stub first on `PATH` that serves the PR's metadata and diff.
    - `[watch] repository` and the PR's repository are set to names that do not exist on
      GitHub. Their bare mirrors are local fixtures in the isolated root, and the PR
      mirror carries `refs/pull/<N>/head`.
    - So no comment, review, issue, or push can reach GitHub.
  - Read-only inspection of the operator's checkouts under `/Users/paul/codagent/`, to
    check that a real review session leaves them unchanged (RA-2). Before and after the
    review session, capture `git for-each-ref`, `git worktree list --porcelain`, and
    `git status --porcelain` for each one. The pass must find no difference. Any
    difference is a defect to report, not to repair.
  - Temporary directories must be removed afterwards. Check that no `claude` or
    `agent-runner` process the pass started is left running.
- **Off limits:**
  - The live service: `~/.agent-factory/state.sqlite3`, `config.toml`, `releases/`, the
    service clone `~/.agent-factory/agent-factory`, and the live `mirrors/`.
  - The LaunchAgent, `scripts/deploy.sh`, and `pause` or `resume` on the live factory.
  - Any write to Paul's checkouts under `/Users/paul/codagent/`. Only the read-only
    snapshot above is allowed.
  - Any real GitHub issue, PR, review, comment, board item, or branch.
  - Fly Machines.
- **Permitted substitutes:**
  - The stand-in `agent-runner` for all but the two authorized real sessions.
  - The `gh` stub for GitHub.
  - A local bare mirror for the watch repository.
  - If the installed `agent-runner` lacks `--session-dir`, the pass records the gap and
    uses the stand-in only.
- **Known risk areas:**
  - Exactly-once behavior across `tick` and resident overlap and restarts.
  - Timestamp comparisons, because `isoformat()` drops zero microseconds.
  - Process-group termination on macOS.
  - The review loop: a posted review starts a round, which is another `PR-READY`.
  - A skill that writes an older result schema, which shows as `interrupted`.
  - The reviewer agent's inherited interactive instructions, which name the operator's
    checkout and `releases/current`. Only a real session exposes them, not the stand-in.
  - Accepted limitations:
    - a failure is missed if no cycle runs for 7 days after its grace period;
    - timeouts are enforced at cycle granularity, up to one poll interval late;
    - a timed-out session skips its audit, so its usage is partial and its audit is
      `missing`;
    - an `unknown` process probe holds a cap slot.

## Human-Only Testing

### HT-001: The first live dispatches after deploy
- Reason: this needs Paul's authority to deploy the change to the live service and to let an
  unattended session post a writer review on a real factory PR, and triage a real failure,
  under his GitHub login. Both are outward-facing, irreversible effects that the acceptance
  envelope forbids.
- Prerequisites:
  - INT-001 through INT-007 and E2E-001 through E2E-005 pass in CI.
  - The acceptance pass has run its two real sessions in isolation with valid results.
  - `doctor` shows the `watch` group passing on the Mac.
- Instructions:
  1. After merging, deploy with `scripts/deploy.sh` and stop any interactive watcher
     session.
  2. When the next factory PR becomes ready, or the next failure happens, open that PR or
     issue.
  3. Run `agent-factory … status` and look at the watch section.
- Required decision or observation:
  - One review session ran, and any review it posted is under Paul's login and started a
    round.
  - Any decisions comment is from the factory bot and mentions Paul.
  - A failure produced one bot triage comment.
  - Status shows the session's cost.
  - Paul confirms the watcher can stay enabled, or disables it with a PR.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Detect watch events in every cycle (including grace changes) | INT-001 | E2E-001, E2E-002, E2E-005 | — |
| Queue each event exactly once | INT-001 | E2E-002 | — |
| Log claim and eval-completion events without an agent | — | E2E-001 | — |
| Review a ready pull request in a dispatched session | — | E2E-003 | HT-001 |
| Triage a failure in a dispatched session | — | E2E-002 | HT-001 |
| Run dispatched sessions fresh and bounded (including the launch lease) | INT-003, INT-004, INT-006 | E2E-003, E2E-004 | — |
| Bound sessions with a daily budget (budget before readiness and the cap) | — | E2E-004 | — |
| Alert on a dispatch that did not finish | INT-004 | E2E-004 | — |
| Deliver dispatch comments exactly once | INT-005 | E2E-002, E2E-005 | — |
| Record each dispatched session's usage | INT-003, INT-007 | — | HT-001 |
| Prune dispatch evidence | — | E2E-004 | — |
| Stop watching without losing queued events | — | E2E-004 | — |
| Configure service-driven watching | — | E2E-003 | — |
| Report watch dispatches in status | — | E2E-002, E2E-004 | HT-001 |
| Redispatch a watch event | — | E2E-004 | — |
| Diagnose readiness with doctor (watch group) | INT-006 | — | — |
| Rollback safety (design D24) | INT-002 | — | — |
