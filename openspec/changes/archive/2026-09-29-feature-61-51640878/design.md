## Context

Today the factory is watched by an interactive Claude session that loops
`.claude/skills/factory-watch/watch.sh`. The script polls `state.sqlite3` with four SQL
queries and prints `CLAIM`, `EVAL-DONE`, `PR-READY`, and `FAILURE` lines. The session then
reviews ready PRs with `factory-pr-review`, which starts the `factory-pr-reviewer` agent,
and triages failures by the skill's "Handling a failure" section. This change moves
detection into the resident and starts a short, headless session only for `PR-READY` and
`FAILURE`.

Relevant current state:

- **Cycle** (`runtime.cycle`): it runs under `advisory_lock(state, "cycle")`, so the
  resident and a manual `tick` never overlap. The order is:
  1. reconcile backends and read the board;
  2. `_consume_results`, then `publish_eval_results`;
  3. the per-card loop: launches, review rounds, reports, cleanup;
  4. `terminal.sweep`;
  5. admission.

  Nothing returns early on pause or the admission window. An uncaught GitHub error, for
  example in `validate_project`, aborts the rest of the cycle.
- **Store** (`store.py`): SQLite in WAL mode, autocommit, and `_transaction()` is
  `BEGIN IMMEDIATE`. Timestamps are `datetime.now(UTC).isoformat()`. `_migrate()` raises
  when the file's `user_version` is newer than the code's `SCHEMA_VERSION` (4). The
  `settings` table holds namespaced JSON values. Issue events live in
  `claim.reporting_json` and are delivered by `Controller.deliver_reports`, but only for
  claims that the per-card loop reports on.
- **Host execution** (`work_kinds/pull_request/launch.py`, `supervisor.py`):
  - A host attempt runs a bash wrapper that calls
    `agent-runner run <workflow> --profile factory --session-dir <evidence>/agent-runner-session`
    in a fresh clone. The workflow and a `factory` profile set are staged into the clone's
    untracked `.agent-runner/`.
  - The wrapper then always runs `python -P -m agent_factory.audit host`, which replays
    the Runner audit and delivers step metrics to the development-audit Sheet. The result
    is written to `<evidence>/audit.json`.
  - Processes are spawned with `start_new_session=True` and an environment filtered to
    `PATH HOME USER LOGNAME TMPDIR LANG LC_ALL`. They are owned through a
    `{pid, start}` identity probed with `ps`. `process_identity_status` and
    `terminate_owned_process`, which runs `killpg` with TERM then KILL, are reusable.
- **Agent Runner usage**: every session directory gets a `run-metrics.json`. Its `totals`
  object holds `token_totals.{input,output,total}`, `token_total_coverage`,
  `estimated_api_cost_usd`, `cost_coverage`, and `active_duration_ms`. The coverage fields
  say whether a value is complete, partial, or unavailable.
- **Mirrors** (`PullRequestWorkspace`): bare mirrors live at
  `<storage_root>/mirrors/<owner>__<repo>.git` and are fetched with the installation token.
  Clones are made with `git clone --local` at a SHA.
- **Doctor and status**: doctor groups are `Diagnostic(..., group=...)`. The host checks
  are in `work_kinds/pull_request/readiness.py` (`_host_diagnostics`,
  `_role_cli_diagnostic`, `_runner_settings_diagnostic`, `_which_diagnostic`, and
  `_gh_auth_status_diagnostic`). Status appends `lines` in `operations.status`, and a
  per-kind readiness failure is saved as the `runtime`/`readiness:<kind>` setting.

## Goals / Non-Goals

**Goals:**

- Detect the four events deterministically in every cycle, with no model cost when
  nothing happens.
- Queue each event durably and exactly once. Start at most one automatic session per
  event, across `tick`/resident overlap and restarts.
- Run review and triage as fresh Agent Runner sessions in throwaway checkouts, with a
  concurrency cap, a daily budget, and a timeout.
- Post results as factory-bot comments exactly once. Record usage, and deliver it to the
  same audit Sheet as fix and feature attempts.
- Keep rollback to the previous release safe, and leave running jobs unaffected by a
  deploy.

**Non-Goals:**

- Holding or changing automatic recovery (decision D16).
- New event types, operator requests such as deploys or board edits, and deploys from
  sessions.
- Changes to Agent Runner, Agent Evals, or Skills.
- Sub-poll timeout precision. Timeouts are enforced at cycle granularity.

## Approach

### Components

A new package, `src/agent_factory/watch/`, holds:

| Module | Responsibility |
|---|---|
| `config.py` (or `WatchConfig` in `agent_factory/config.py`) | parse and validate `[watch]` |
| `store.py` | the `watch_dispatch` table and the cursor, both on `ClaimStore`'s connection |
| `detect.py` | the four event queries and queueing in one transaction |
| `dispatch.py` | pending processing: readiness, budget, cap, one session per PR, and launch |
| `session.py` | the evidence directory, throwaway clone, staged workflow, brief, wrapper, and spawn |
| `supervise.py` | the per-cycle probe of `launched` rows: timeout, exit, result, usage, and cleanup |
| `result.py` | result schema validation and comment rendering |
| `deliver.py` | comment delivery owned by the dispatch, to an issue or a PR |
| `readiness.py` | the `watch` doctor group |
| `workflow/factory-watch-v1.0.yaml` | the packaged Agent Runner workflow (contract `factory-watch/1`) |

`runtime.cycle` calls one entry point, `watch.step(store, client, shared, local,
config_path)`. `operations.status` and `operations.doctor` add the watch section and
group. `cli.py` adds `watch redispatch`.

### Cycle integration

```
cycle:
  with advisory_lock("cycle"), ClaimStore as store:
      try:
          <existing body, unchanged>
      finally:
          watch.step(...)   # own try/except: log, never raise
```

`watch.step` runs even when the body raised, for example on a GitHub outage in
`validate_project`. A factory that cannot talk to GitHub is exactly when triage matters.
Detection and supervision need only the store. Delivery fails and is retried. It runs
last, so a `FAILURE` brief sees any recovery attempt the same cycle launched. The step
runs in this order:

1. `supervise`: probe `launched` rows, and finish exited or timed-out sessions.
2. `detect`: skipped when watching is disabled.
3. `dispatch`: process `pending` rows. Skipped when disabled.
4. `deliver`: post any undelivered comments.
5. `prune`: remove expired evidence.

Supervision and delivery always run, including while watching is disabled, as the "Stop
watching" requirement needs. Each sub-step has its own `try/except`, which logs and
continues. A failed detection leaves the cursor unchanged, because its transaction rolls
back.

### Store: `watch_dispatch` table and cursor

The table is created with `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS`,
run in `ClaimStore.__init__` right after `_migrate()`. It is **not** a `SCHEMA_VERSION`
bump (decision D24).

```sql
CREATE TABLE IF NOT EXISTS watch_dispatch (
  id TEXT PRIMARY KEY,                 -- uuid4; the <dispatch> of `watch redispatch`
  event_key TEXT NOT NULL,             -- 'FAILURE:<run>' | 'EVAL-DONE:<run>' | 'PR-READY:<run>' | 'CLAIM:<claim>'
  attempt INTEGER NOT NULL,            -- 1, then +1 per redispatch
  event_kind TEXT NOT NULL,
  claim_id TEXT NOT NULL REFERENCES claim(id),
  run_id TEXT,
  repository TEXT NOT NULL,
  issue_number INTEGER NOT NULL,
  pr_number INTEGER,
  pr_url TEXT,
  event_at TEXT NOT NULL,              -- run.finished_at or claim.created_at
  state TEXT NOT NULL,                 -- pending|logged|launched|budget-exhausted|completed|interrupted|timed-out|launch-failed
  detail TEXT NOT NULL DEFAULT '',     -- reason for a non-completed end state
  profile TEXT,
  evidence_path TEXT,
  process_json TEXT NOT NULL DEFAULT '{}',   -- {pid, start}
  launched_at TEXT, deadline_at TEXT, finished_at TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  usage_json TEXT NOT NULL DEFAULT '{}',
  audit_json TEXT NOT NULL DEFAULT '{}',
  deliveries_json TEXT NOT NULL DEFAULT '{}',
  redispatch_of TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(event_key, attempt)
);
CREATE INDEX IF NOT EXISTS watch_dispatch_state ON watch_dispatch(state);
```

The cursor is the `settings` row `("watch", "cursor")` =
`{"handled_up_to": <iso>, "enabled_at": <iso>}`. Disabling watching clears the row. The
next enabled cycle sees no row and sets both fields to that cycle's now. That gives "start
at the current time" on first enablement and on re-enablement (D17).

### Detection

Detection runs as one `BEGIN IMMEDIATE` transaction, `store.detect_watch_events(now_fn,
grace, overlap)`:

1. Read the cursor, then take `now` **inside** the transaction. Every writer stamps
   `finished_at` and `created_at` inside its own `BEGIN IMMEDIATE` transaction, and
   writers are serialized. So any row stamped before `now` has already committed and is
   visible, which removes the stamp-then-commit race that `watch.sh` has.
2. For `EVAL-DONE`, `PR-READY`, and `CLAIM`, select candidates with a small look-back
   overlap (2 minutes) below `handled_up_to`, never before `enabled_at`. Duplicates are
   removed by `UNIQUE(event_key, attempt)` through `INSERT OR IGNORE`. The overlap guards
   against any writer that stamps outside a transaction. `FAILURE` does not use the cursor
   window (see below).
3. Filter in Python with `datetime.fromisoformat`, not by string comparison, because
   `isoformat()` drops zero microseconds. The event definitions port `watch.sh`:
   - `FAILURE`: an **eligibility scan**, not a shifted window.
     - Every cycle selects runs with `status IN (failed, interrupted, cancelled,
       timed_out)` and `max(enabled_at, now − 7 days) < finished_at ≤ now − grace`, using
       the grace configured for this cycle, whose `FAILURE:<run>` key is not yet queued.
     - A run becomes eligible in the first cycle after its grace has passed, whatever the
       grace was when the run finished. So changing the grace can neither skip a failure
       nor queue one twice. A decrease makes pending failures eligible sooner, and an
       increase delays those not yet queued.
     - The status is read at detection time, so a host fix that went from `interrupted`
       to `completed` within the grace period never matches.
     - The 7-day horizon bounds the scan. A failure is missed only if no cycle runs for 7
       days after its grace, which is an accepted limitation.
   - `EVAL-DONE`: `run.kind = 'eval'`, any other terminal status, and
     `lo < finished_at ≤ now`.
   - `PR-READY`: `run.kind IN (fix, feature)`, `status = 'completed'`,
     `result.outcome = 'pull-request'`, and `lo < finished_at ≤ now`. The PR URL is taken
     from `result.pr.url`, or else `claim.outcome.pr.url`. The number is parsed from the
     URL.
   - `CLAIM`: `lo < claim.created_at ≤ now`.
4. Insert each event as `pending` with `attempt = 1`, then set
   `handled_up_to = now`, all in the same transaction. The cursor bounds only the other
   three event kinds.

### Dispatch (pending processing)

Once per cycle, pending rows are processed ordered by `(event_at, event_key, attempt)`:

- `CLAIM` and `EVAL-DONE`: log with `logger.info`, which the resident's LaunchAgent log
  captures, and set `logged`. No budget or cap.
- `PR-READY` and `FAILURE`:
  1. **Budget, first.** Count rows whose `launched_at` falls in today's local day
     (`local.schedule.timezone`), redispatches included. When the count has reached
     `daily_sessions`, set `budget-exhausted`, add a `budget` delivery to the claim's
     issue, and continue with the next row.
     - This runs before readiness, the one-per-PR rule, and the cap, and it needs none of
       the launch prerequisites. So a zero or spent budget always produces its notice,
       even while the watch doctor group fails or older sessions hold the cap.
     - Delivery uses only the App client.
  2. **Readiness.** Run the `watch` doctor group once per cycle, lazily, only when a
     session dispatch within budget is pending and a cap slot is free. Save the result as
     the `runtime`/`readiness:watch` setting, which status already renders. On failure,
     launch nothing this cycle, but keep applying step 1 to the remaining rows.
  3. **One session per PR.** Skip, and leave `pending`, a `PR-READY` row while another row
     with the same `(repository, pr_number)` is `launched`. Continue with later rows.
  4. **Cap.** When the count of `launched` rows reaches `max_sessions`, launch nothing
     more this cycle, but keep applying step 1 to the remaining rows.
  5. **Claim the launch** with `UPDATE ... SET state='launched', launched_at, deadline_at,
     profile, evidence_path WHERE id=? AND state='pending'`. When `rowcount == 0`, another
     process won, so skip. This compare-and-set is the exactly-once guard, together with
     the cycle lock.
  6. **Start the session** (below). Any exception sets `launch-failed` with the reason,
     adds an `alert` delivery, and removes the clone.

The profile is `agents[event_kind]` when set, or else `agent`. It is read from the
configuration of the cycle that launches, and frozen on the row.

### Session start

Paths: `E = <storage_root>/artifacts/watch/<dispatch-id>` and
`C = <storage_root>/clones/watch/<dispatch-id>/repo`.

1. Fetch the mirror of `[watch] repository` with `PullRequestWorkspace.fetch_mirror`,
   using the installation token. Resolve `refs/heads/main`, run `git clone --local` into
   `C` at that SHA, and set `origin` to `https://github.com/<repo>.git`, as the fix
   clones do.
   - For `PR-READY`, also fetch the mirror of the PR's repository. GitHub mirrors carry
     `refs/pull/<N>/head`. The session reads the PR code from that mirror only, through a
     read-only `git clone --local` into `E/scratch`. It never fetches into the mirror,
     which the factory owns.
2. Stage `factory-watch-v1.0.yaml` and `check-contract.sh` into `C/.agent-runner/workflows/`.
   Write `C/.agent-runner/config.yaml` with `staged_config_text(tracked, {"watcher": profile})`,
   and exclude both through `.git/info/exclude`. Reuse `_refuse_symlinked_staging`,
   `_refuse_tracked_workflow_files` (extended to the watch file names), and
   `_exclude_from_git`.
3. Write `E/input/brief.json`. The brief is the only context a session gets:
   - `dispatch` id, `event_kind`, `event_line` (in the same format `watch.sh` prints),
     and `attempt`;
   - `claim`: id, repository, issue number, kind, lifecycle, and `outcome`;
   - `run`: id, kind, reason, attempt number, status, `result`, `evidence_path`,
     `finished_at`, and `backend`. The plan's `allowed_environment` is left out;
   - `pull_request` URL and number (`PR-READY`);
   - `paths`:
     - the state database;
     - the running release's `agent-factory` executable
       (`Path(sys.executable).parent / "agent-factory"`) and its Python
       (`factory_python`, used to run `C/.claude/skills/factory-assign/assign.py` with
       `AGENT_FACTORY_CONFIG` set to the local config path);
     - the local config path;
     - `E`, `C`, and a scratch directory `E/scratch`;
     - for `PR-READY`, `pr_source`: the PR repository's mirror path;
   - `operator_login` and `result_file = E/watch-result.json`;
   - `procedure`: `review` or `triage`;
   - `forbidden`: the standing rules from the spec.
4. Write the wrapper `E/private/watch-run.sh` (mode 0700):

   ```bash
   #!/bin/bash
   echo $$ > E/pid                            # first, before anything that can fail
   set -uo pipefail
   exec > >(tee -a E/logs/agent-runner.log) 2>&1
   export AGENT_RUNNER_NO_TUI=1
   cleanup() { rm -rf "$(dirname C)"; }       # the throwaway clone always goes
   trap cleanup EXIT
   date -u +%FT%TZ > E/started-at
   cd C
   <runner> run factory-watch --profile factory --session-dir E/agent-runner-session \
     --param brief_file=E/input/brief.json --param artifact_dir=E \
     --param contract_version=factory-watch/1
   status=$?
   <python> -P -m agent_factory.audit host --runner <runner> \
     --session-dir E/agent-runner-session --project C --evidence E || true
   printf '{"code": %d, "finished_at": "%s"}\n' "$status" "$(date -u +%FT%TZ)" > E/exit.json
   exit "$status"
   ```

   No `GH_TOKEN`, `GITHUB_TOKEN`, or `GIT_CONFIG_GLOBAL` is set, so `gh` and `git push`
   use the operator's own login and credential helper. That login is the writer that a
   PR review needs.
5. Spawn with `subprocess.Popen(["/bin/bash", wrapper], cwd=C, env=<the inherited
   subset>, start_new_session=True, stdout/stderr → E/factory-watch.log)`. Record
   `process_json = {pid, start}` through the supervisor's `_process_start`. The wrapper's
   first command writes `$$`, which is the `Popen` pid and the process-group leader, to
   `E/pid`. If the resident dies between the spawn and the record, the next cycle adopts
   the identity from `E/pid`, so a live session is never mistaken for a dead one. A crash
   before the spawn is handled by the launch lease in Supervision.

### The workflow and skills

`factory-watch-v1.0.yaml`:

- `# factory-contract: factory-watch/1`;
- params `brief_file`, `artifact_dir`, and `contract_version`;
- one session `watcher` (agent `watcher`);
- steps:
  - `check-contract`;
  - `watch`: an autonomous prompt. It reads the brief. For `review` it follows
    `.claude/skills/factory-pr-review/SKILL.md` **Headless mode**. For `triage` it
    follows `.claude/skills/factory-watch/SKILL.md` **Headless triage**. It writes
    exactly one JSON object to `{{artifact_dir}}/watch-result.json` in the schema below,
    and never asks a question;
  - `check-result`: a script that fails the workflow when the file is missing or is not
    a JSON object. The resident validates the file fully anyway.

Skill changes, all in this repository:

- `factory-pr-review` gains a "Headless mode" section. It starts the
  `factory-pr-reviewer` agent in the foreground, waits for it, never uses
  `AskUserQuestion`, and writes the verdict, review URL, filed issues, and decisions to
  `result_file`. It passes the brief's paths to the agent and says the agent runs
  headless. It uses `E/scratch` as the scratchpad.
- `.claude/agents/factory-pr-reviewer.md` gains a "Headless (dispatched) mode" section.
  When the prompt says headless, it replaces the rules that name the operator's checkout
  and the live release:
  - read `AGENTS.md` and docs from `C`, which is `origin/main`, instead of
    `/Users/paul/codagent/agent-factory`;
  - get the PR code with
    `git clone --local --no-checkout <pr_source> <scratch>/pr<N> && git -C <scratch>/pr<N> checkout --detach <headRefOid>`,
    never with `git -C /Users/paul/codagent/<repo> …`, and never by fetching into the
    mirror;
  - assign filed issues with
    `AGENT_FACTORY_CONFIG=<config> <factory_python> C/.claude/skills/factory-assign/assign.py …`,
    run from `C`, never through `~/.agent-factory/releases/current` or from the
    operator's checkout;
  - report back in the same format, which the headless skill turns into `result_file`.
  - The interactive rules stay as they are.
- `factory-watch` describes service mode as the normal one, and keeps `watch.sh` for
  debugging. It adds a "Headless triage" section. Triage runs steps 2–4 of "Handling a
  failure". It may pause and resume through the brief's `agent-factory` path. It does a
  fix in `C` on a new `fix/<name>` branch, not in the operator's checkout, then tests,
  runs `agent-validate run`, pushes, and opens the PR. It never deploys, merges, or
  messages other sessions. It writes the triage result.

Because `C` is a checkout of `origin/main`, sessions use the skills as merged. The
resident's release only supplies the workflow file and the brief format.

### Result schema and comments

`watch-result.json` has two forms:

```json
{"procedure": "review", "verdict": "...", "review_url": null, "issues_filed": [],
 "decisions": [{"question": "...", "context": "...",
                "options": [{"label": "...", "consequence": "..."}],
                "recommendation": "..."}]}
{"procedure": "triage", "cause": "...", "evidence": ["..."],
 "owner": "factory code|Agent Runner|Agent Evals|Skills|environment|transient",
 "retry": "...", "actions": ["..."], "pull_request": null,
 "paused_by_session": false, "resumed_by_session": false,
 "next_step": "...", "handoff": null}
```

`result.py` validates the fields:

- `procedure` must match the dispatch;
- types and the `owner` enum are enforced;
- there are at most 10 decisions;
- each string is capped at 4,000 characters;
- the text `<!-- agent-factory:` is removed from agent text, so a session cannot forge a
  marker.

An invalid or missing result with exit code 0 still counts as `interrupted`, with the
detail "invalid result: …".

Comments are rendered deterministically from the validated result:

- **Triage**, to the claim's issue: cause, evidence, owner, retry, actions (with the PR
  link), next step, and handoff. Then "Factory paused: yes/no", read from
  `store.is_paused()` when the exit is processed, and whether this session paused or
  resumed it. Then a footer with the dispatch id, profile, and evidence path.
- **Decisions**, to the PR, only when `decisions` is not empty: `@<operator>` when set,
  then each decision with its context, its options and consequences, and "Recommended:".
- **Budget notice** and **alert**, to the claim's issue: the event line, the PR if any,
  what happened, the evidence path, and
  `agent-factory --config <path> watch redispatch <id>`.

### Delivery (owned by the dispatch)

`deliveries_json` maps a purpose (`triage`, `decisions`, `budget`, or `alert`) to
`{target: "issue"|"pr", number, marker, body, comment_id, failure: {attempts, error, at}}`.
The marker is `<!-- agent-factory:watch:<dispatch-id>:<purpose> -->`. Each cycle, for
every delivery without a `comment_id`:

1. List the target's comments with `list_comment_records`, which paginates and works for
   PRs through the issues endpoint.
2. Adopt a comment whose author is `shared.bot_login` and that carries the marker.
3. Otherwise run `create_comment(repo, number, f"{marker}\n{body}")` and record the id.
4. On `GitHubApiError`, record the failure and retry next cycle.

This uses the same protocol as `Controller.deliver_reports`, generalized to any target.
It does not depend on the claim's card appearing in the per-card loop (decision D25).
The body is stored when the delivery is created, so a retry posts the same text.

### Supervision of launched rows

For each `launched` row, every cycle:

1. If `process_json` is empty, adopt the identity from `E/pid` when the start time can
   be read. If there is no identity and no adoptable `E/pid`, apply the **launch lease**:
   - Launching happens under the cycle lock, so a later cycle that holds the lock knows
     the launching cycle has ended.
   - When `launched_at` is more than 2 minutes old, the launch never produced a process.
     Set `launch-failed` with the detail "launch did not complete", add an `alert`, and
     remove `C`. This releases the cap slot, and no second session is started.
   - Within the 2 minutes, which covers a wrapper that is spawned but has not yet written
     its pid, leave the row for the next cycle.
   - An `E/pid` whose process is gone, with no `exit.json`, follows the `missing` path
     below and is recorded `interrupted`.
2. `process_identity_status(identity)`:
   - `alive`: if `now > deadline_at`, run `terminate_owned_process`, set `timed-out`, and
     add an `alert`. Otherwise leave it.
   - `missing`: read `E/exit.json` and `E/watch-result.json`. A valid result gives
     `completed`, plus the `triage` or `decisions` delivery. Anything else gives
     `interrupted` with a detail ("no exit record", "exit code N", or "invalid result: …"),
     plus an `alert`.
   - `unknown`: leave it `launched`, and show it in status.
3. On any end state: read usage, read `E/audit.json` into `audit_json`, and remove `C`'s
   parent directory as a backstop to the wrapper's trap.

The resident restart case is the same code. There is no separate reconciliation path,
because the identity and files are durable. Nothing is ever relaunched.

### Usage

`usage_json` records:

- `profile`;
- `started_at` and `finished_at`, from `E/started-at` and `exit.json`, or else
  `launched_at` and the time the end was observed;
- `duration_seconds`;
- `input_tokens`, `output_tokens`, and `estimated_cost_usd`, from
  `E/agent-runner-session/run-metrics.json` `totals`;
- `coverage`.

A value whose coverage is not `complete`, or whose file is missing, is stored as `null`
with the Runner's coverage label, never as 0. Sheet delivery is the existing host audit
in the wrapper. `audit_json` holds its `outcome`, and status lists dispatches whose audit
is not `delivered`. A session killed at its timeout skips the wrapper's audit, so its
audit shows `missing`. That is accepted.

### Evidence retention

The `prune` sub-step removes `E` for dispatches that ended more than
`limits.evidence_retention_days` ago. The row, with its result, usage, audit, and
deliveries, is kept. It never touches a `pending` or `launched` row.

### Doctor `watch` group

The group runs only when watching is enabled. Its checks are built from existing helpers
and regrouped with `dataclasses.replace(group="watch")`:

- `agent-runner` on the service PATH, its version, and support for `--session-dir`;
- `git` and `gh` through `_which_diagnostic`;
- `_role_cli_diagnostic` for each adapter the default and per-event profiles select, and
  `_runner_settings_diagnostic`;
- the packaged `factory-watch` workflow declares `factory-watch/1`, checked by the
  `check_host_runner_workflow` equivalent;
- the watch repository's mirror can be fetched with the installation token;
- `gh api user -q .login` succeeds, is not `shared.bot_login`, and
  `gh api repos/<repo> -q .permissions.push` is `true`.

The dispatch step runs the same function, so the gate in the cycle and doctor cannot
disagree.

### Status and CLI

`operations.status` appends a `watch` block after `host attempts:`:

```
watch: enabled, handled up to 2026-09-29T14:05:00+00:00
watch sessions today: 7/20, known cost $4.12
watch running: 3f2a… PR-READY Codagent-AI/agent-factory#61 PR #70 claude:claude-sonnet-5-5:medium 12m
watch pending: 1 (concurrency cap)
watch ended: 9c1e… FAILURE Codagent-AI/agent-evals#40 timed-out /Users/…/artifacts/watch/9c1e…
watch undelivered: 5d0b… decisions → PR #70: HTTP 502
watch audit: 5d0b… pending-delivery
watch decisions: https://github.com/Codagent-AI/agent-factory/pull/70 (5d0b…)
```

The "ended" and "decisions" lines are limited to claims that are not yet terminal, which
means not Done-observed, cancelled, or superseded. When watching is disabled, status
prints `watch: disabled` plus any `launched` or `pending` rows.

`agent-factory watch redispatch <id>` opens `ClaimStore` like `pause` does. In one
transaction it checks the row's kind and state, then inserts a new `pending` row with the
same event fields, `attempt = max(attempt) + 1`, and `redispatch_of = <id>`. It prints
the new id. When `[watch] enabled` is false in the loaded configuration, it adds "waits
until watching is enabled". A refusal exits with code 2 and names the state.

### Configuration

`SharedConfig.watch: WatchConfig | None` is parsed from `[watch]`:

| Setting | Type and default |
|---|---|
| `enabled` | bool, false |
| `repository` | `owner/name`, required when enabled |
| `agent` | profile, required when enabled |
| `agents` | table keyed `PR-READY` / `FAILURE` |
| `max_sessions` | int ≥ 1, default 2 |
| `daily_sessions` | int ≥ 0, default 20 |
| `grace_minutes` | int ≥ 0, default 7 |
| `timeout_minutes` | int ≥ 1, default 90 |
| `operator` | login, optional |

Profiles are checked against launch.py's `_PROFILE` regex at load time, but only when
enabled. The Codagent `config/codagent.toml` gains:

```toml
[watch]
enabled = true
repository = "Codagent-AI/agent-factory"
agent = "claude:claude-sonnet-5-5:medium"
operator = "pacaplan"
```

## Decisions

- **D24: No schema version bump; the table is created idempotently.** `_migrate()`
  refuses a database whose `user_version` is newer than the code. A bump to 5 would break
  two things. Rolling back to the previous release, which deploy does automatically when
  doctor fails, would leave a service that cannot open its database. And supervisors
  still running from the previous release reopen the store. `CREATE TABLE IF NOT EXISTS`
  outside `user_version` is ignored by older code. *Alternative:* a v5 migration with a
  backup. It was rejected for the rollback break.
- **D25: Dispatch comments use delivery owned by the dispatch, not `record_event`.**
  `record_event`/`deliver_reports` only run for claims that the per-card loop reports on,
  and they only target the claim's issue. One protocol for issue and PR targets keeps
  exactly-once delivery independent of card state. The observable behavior is unchanged.
  This revises the mechanism in D4 and D14.
- **D26: Agent Runner workflow, not `claude -p`.** A headless Agent Runner session can
  start subagents; the factory's own define step runs that way. Its `run-metrics.json`
  gives tokens, cost, and coverage, and the existing host audit reaches the Sheet. This
  resolves the deferred usage scenario and confirms D9.
- **D27: Sessions check out `[watch] repository` from its bare mirror.** This reuses the
  fix workspace's mirror and fetch code and the installation token. `repository` is a new
  required setting, added to the operations spec, because the factory cannot otherwise
  name its own repository.
- **D28: Triage fixes are made in the dispatch's own clone.** The interactive skill uses a
  worktree of the operator's checkout. Headless triage uses the throwaway clone on a new
  `fix/` branch, which honors "never in the operator's checkout", and pushes before the
  session ends.
- **D29: Sessions use the operator's `gh` login.** No token is injected, so the reviewer
  posts as a writer and starts a review round, as today. Everything the factory itself
  posts uses the App client, so it is a bot comment and never a gesture.
- **D30: Supervision is per cycle, with no watcher process.** A session needs no progress
  or quota tracking, so the resident probes `{pid, start}` each cycle. The timeout is
  enforced at the first cycle past the deadline, at most one poll interval late (5
  minutes).
- **D31: The watch step runs in `finally` at the end of the cycle.** Triage still runs
  when the cycle's GitHub work fails, and a `FAILURE` brief sees the recovery attempt
  launched in the same cycle.
- **D32: `now` is taken inside the detection transaction, with a 2-minute overlap.**
  Serialized `BEGIN IMMEDIATE` writers make every earlier stamp visible. The overlap plus
  `INSERT OR IGNORE` covers stragglers without duplicates.
- **D37: `FAILURE` uses an eligibility scan, not the shifted cursor window** (approach
  review RA-1). A single cursor with the current grace can skip a failure when the grace
  shrinks between cycles. Scanning every still-failed run past its current grace, inside
  a 7-day horizon and after enablement, with unique keys, cannot skip or duplicate one.
- **D38: Headless reviewer instructions, and PR code from the mirror** (RA-2). The
  reviewer agent's interactive rules use the operator's checkout and
  `releases/current`. Its headless mode uses a read-only clone of the PR repository's
  mirror in the dispatch scratch directory, and the `factory-assign` helper from `C`
  under the resident's own interpreter.
- **D39: The budget is checked before readiness, the one-per-PR rule, and the cap**
  (RA-3). A zero or spent budget must post its notice even when sessions could not launch
  anyway.
- **D40: A launch lease for crashes around the spawn** (RA-4). The wrapper writes `E/pid`
  first. A `launched` row with no identity and no adoptable pid after 2 minutes, observed
  under the cycle lock, becomes `launch-failed` with an alert, and it frees its cap slot.

## Risks / Trade-offs

- **Review loop.** A posted review starts a round, the round's run is `PR-READY`, and
  that starts another review. This is today's behavior, bounded by the reviewer posting
  only Blocking or Should-fix items, by the kind's round limits, and by the daily budget.
- **Unattended pause.** Triage may pause the factory and not resume it. The triage
  comment and status both show the pause state, and pause keeps watching running.
- **Timed-out sessions skip the audit.** Their usage is partial, or unavailable, and
  their audit is `missing`. Status lists them.
- **The 7-day failure horizon.** If no cycle runs for 7 days after a failure's grace
  period, that failure is never triaged. A factory that stays down that long has bigger
  problems.
- **Clock and identity.** An `unknown` probe, for example when `ps` fails, leaves the row
  `launched` and holds a cap slot until it resolves. Status shows it.
- **Operator login dependence.** If the operator logs `gh` out, doctor's watch group
  fails and dispatches wait. Detection and alerts continue, because they use the App
  client.
- **Skill drift.** Sessions use `origin/main` skills, while the result schema comes from
  the release's workflow and brief. A skill that writes an old schema produces an
  `interrupted` alert, not a silent loss.

## Migration Plan

1. Merge. `scripts/deploy.sh` builds the release. On first open, the new code creates the
   table. Doctor runs the watch group because `config/codagent.toml` enables watching.
   A failing watch group fails doctor, as a failing kind group does, so deploy's doctor
   gate rolls back. Before merging, confirm on the Mac that `gh auth status` is the
   operator's writer login on the service PATH.
2. The first enabled cycle sets the cursor to now. The operator stops any interactive
   watcher session. The documentation says so.
3. **Rollback:** point the service at the previous release. It ignores `watch_dispatch`
   and the `watch` setting. Sessions already running finish on their own, and the
   wrapper's trap removes their clones. Their results are processed when a release with
   watching runs again. To stop watching without a rollback, set `enabled = false` through
   a PR, per the configuration-pin rule.

## Open Questions

None. Every choice above follows from the specs and the repository's existing patterns.
