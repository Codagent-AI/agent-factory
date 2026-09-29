- [x] Run factory watching inside the service, and dispatch short headless review and triage sessions only on events

## Task: Service-driven watching with event dispatch

Implement the whole change described in these files, in the change directory:

- `proposal.md`;
- the delta specs under `specs/`: `factory-watch-dispatch` (new) and `factory-operations`;
- `design.md`;
- the decision log `decisions.md` (D1–D40). Where they differ, later decisions override
  earlier ones. In particular, D24/D25 override the mechanism in D4/D14, and D37–D40 override
  the matching design text before the approach review;
- the automated obligations in `test-plan.md`: INT-001 to INT-007 and E2E-001 to E2E-005.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or `/Users/paul/codagent/*`. Never deploy.

### Scope

1. **Configuration** (`src/agent_factory/config.py`; design "Configuration", D20, D27):
   - Add `WatchConfig` and `SharedConfig.watch`, parsed from `[watch]` with these settings:
     `enabled`, `repository`, `agent`, `agents` (keys `PR-READY` and `FAILURE`),
     `max_sessions`, `daily_sessions`, `grace_minutes`, `timeout_minutes`, and `operator`.
     The defaults and ranges are in the design.
   - Validation runs only when `enabled` is true. It checks profiles against the
     `cli:model:effort` regex in `launch.py` and fails with a `ConfigurationError` naming the
     setting.
   - Add the Codagent `[watch]` block from the design to `config/codagent.toml`.
2. **Store** (`src/agent_factory/store.py` plus `watch/store.py`; design "Store", D24):
   - Create the `watch_dispatch` table and its index with `CREATE ... IF NOT EXISTS` right
     after `_migrate()`. Do **not** change `SCHEMA_VERSION`.
   - Add accessors for the `("watch", "cursor")` setting, which holds `handled_up_to` and
     `enabled_at`. Clear it when watching is disabled, and initialize it to now on the first
     enabled cycle.
   - Add the compare-and-set that moves a row from `pending` to `launched`.
   - Add the per-day count by `launched_at` in `local.schedule.timezone`.
   - Add queries for the one-review-per-PR rule and the cap.
   - Add the redispatch insert, which sets `attempt = max + 1` and `redispatch_of`.
3. **Detection** (`watch/detect.py`; design "Detection", D32, D37). This is one
   `BEGIN IMMEDIATE` transaction:
   - take `now` inside the transaction;
   - for `EVAL-DONE`, `PR-READY`, and `CLAIM`, detect in the cursor window with a 2-minute
     overlap, bounded by `enabled_at`;
   - for `FAILURE`, run the eligibility scan: a failure status, finished after
     `max(enabled_at, now − 7 days)`, finished at or before `now − current grace`, and not
     yet queued;
   - compare with `datetime.fromisoformat`, not as strings;
   - `INSERT OR IGNORE` the rows as `pending` with `attempt = 1`, and advance
     `handled_up_to`, in the same transaction;
   - parse the PR URL and number from `result.pr.url`, or else `claim.outcome.pr.url`.
4. **Dispatch** (`watch/dispatch.py`; design "Dispatch", D39). Process pending rows ordered
   by `(event_at, event_key, attempt)`:
   - `CLAIM` and `EVAL-DONE` rows are logged with `logger.info` and set to `logged`.
   - For `PR-READY` and `FAILURE` rows, apply these steps in this order:
     1. the budget, which gives `budget-exhausted` and a `budget` delivery;
     2. the lazy readiness check, saved as the `runtime`/`readiness:watch` setting. On
        failure nothing launches, but the budget check still runs;
     3. the one-review-per-PR rule;
     4. the cap. When it is reached nothing launches, but the budget check still runs;
     5. the compare-and-set to `launched` with `launched_at`, `deadline_at`, `profile`, and
        `evidence_path`;
     6. the session start. Any exception gives `launch-failed`, an `alert` delivery, and
        clone removal.
   - The profile is `agents[event_kind]`, or else `agent`, frozen on the row.
5. **Session** (`watch/session.py`; design "Session start", D26–D29, D38, D40):
   - Fetch the mirror of `[watch] repository` with `PullRequestWorkspace.fetch_mirror`. Run
     `git clone --local` into `<root>/clones/watch/<id>/repo` at `main`, and set `origin`
     to the GitHub URL.
   - For `PR-READY`, also fetch the mirror of the PR's repository, and pass its path as
     `pr_source`.
   - Stage the workflow, `check-contract.sh`, and the check-result script, and a
     `factory`/`watcher` profile, through `staged_config_text`. Reuse
     `_refuse_symlinked_staging`, `_refuse_tracked_workflow_files` (extended to the watch
     file names), and `_exclude_from_git`.
   - Write `E/input/brief.json` with exactly the fields the design lists, including
     `factory_python`, `pr_source`, the config path, `operator_login`, `result_file`,
     `procedure`, and `forbidden`. Leave out `allowed_environment`.
   - Write the wrapper `E/private/watch-run.sh`:
     - its first command is `echo $$ > E/pid`;
     - it writes `started-at`;
     - it runs `agent-runner run factory-watch --profile factory --session-dir … --param …`;
     - it always runs the host audit;
     - it writes `exit.json`;
     - its trap removes the clone.
   - Spawn with `Popen(start_new_session=True)`, using only the inherited environment subset.
     Inject no `GH_TOKEN`, `GITHUB_TOKEN`, or `GIT_CONFIG_GLOBAL`. Record `{pid, start}`.
6. **Supervision** (`watch/supervise.py`; design "Supervision", D30, D40):
   - Each cycle, adopt the identity from `E/pid` when it is missing. Apply the 2-minute
     launch lease, which gives `launch-failed` and an alert.
   - Handle each probe result:
     - `alive` past the deadline: `terminate_owned_process`, `timed-out`, and an alert;
     - `missing`: read `exit.json` and the result. The outcome is `completed` with its
       delivery, or else `interrupted` with a detail and an alert;
     - `unknown`: leave the row as it is.
   - On every end state:
     - record usage from `agent-runner-session/run-metrics.json` `totals`, storing `null`
       plus the coverage when coverage is not complete;
     - record `audit_json` from `E/audit.json`, or `missing`;
     - remove the clone.
7. **Results, comments, and delivery** (`watch/result.py`, `watch/deliver.py`; design
   "Result schema and comments" and "Delivery", D19, D25, D29):
   - Validate the two result forms, with the `owner` enum, at most 10 decisions, 4,000-character
     caps, and stripping of `<!-- agent-factory:`.
   - Render the triage, decisions, budget, and alert comments deterministically. Triage
     comments state the pause state from `store.is_paused()`. Decisions comments mention
     `@<operator>`, and are posted only when there are decisions. Budget and alert comments
     carry the redispatch command.
   - Delivery owned by the dispatch, through the App client:
     - the marker is `<!-- agent-factory:watch:<id>:<purpose> -->`;
     - list the target's comments with `list_comment_records`, and adopt a bot-authored
       comment that carries the marker;
     - otherwise `create_comment` and record the id;
     - on failure, record it and retry next cycle.
8. **Cycle wiring and retention** (`runtime.py`, `watch/__init__.py`; design "Cycle
   integration", "Evidence retention", D31, D33):
   - `cycle` calls `watch.step` in a `finally` inside the lock. The step runs supervise,
     detect, dispatch, deliver, and prune, each in its own `try`, and never raises.
   - Supervision and delivery also run while watching is disabled.
   - Prune the `E` directories of rows that ended more than `evidence_retention_days` ago.
9. **Workflow** (`watch/workflow/factory-watch-v1.0.yaml`; design "The workflow and skills"):
   - Contract `factory-watch/1`, with the params `brief_file`, `artifact_dir`, and
     `contract_version`, and the session `watcher`.
   - The steps are `check-contract`, the autonomous `watch` step, and `check-result`.
   - Package it like the existing workflow directory. Do **not** add a tracked copy under
     `.agent-runner/workflows/`.
10. **Doctor, status, and CLI** (`watch/readiness.py`, `operations.py`, `cli.py`; design
    "Doctor `watch` group", "Status and CLI", D21):
    - The `watch` group is built from the existing host helpers, regrouped. It adds the
      writer-login check: `gh api user` is not the bot, and `permissions.push` is true.
    - The same function gates dispatch. Add `watch` to the group order. Doctor exits 1 on a
      failing `watch` group.
    - Add the status watch block with the lines listed in the design, including the pending
      reasons, the day's count and cost, ended dispatches, undelivered comments, audits not
      delivered, and open decisions.
    - Add `agent-factory watch redispatch <id>`, with state checks, exit 2 on refusal, and
      the "waits until watching is enabled" note.
11. **Skills, agent, and docs** (design "The workflow and skills", D28, D38; spec "Document
    the service-driven watcher"):
    - `.claude/skills/factory-watch/SKILL.md` covers the service mode, "Headless triage",
      and `watch redispatch`. Keep `watch.sh` for debugging.
    - `.claude/skills/factory-pr-review/SKILL.md` gains "Headless mode".
    - `.claude/agents/factory-pr-reviewer.md` gains "Headless (dispatched) mode":
      - read docs from `C`;
      - clone the PR code from `pr_source` into scratch;
      - run `assign.py` from `C` with `factory_python` and `AGENT_FACTORY_CONFIG`;
      - never use `/Users/paul/codagent/*` or `releases/current`.
    - Update `AGENTS.md` and `docs/operations.md`: the settings, the doctor group, status,
      redispatch, stopping the interactive watcher, the writer `gh` login, and the accepted
      limitations.
12. **Tests**:
    - Unit tests for everything the test plan's Coverage Strategy lists. This includes a
      check that the headless reviewer section and the brief name no `/Users/paul/codagent/`
      path and no `releases/current` path.
    - `tests/integration/test_watch_{detection,store,session,supervision,delivery,readiness,workflow}.py`
      for INT-001 to INT-007. `session` and `supervision` are marked `darwin`. `workflow`
      is skipped with the existing `SKIP` pattern when the installed Runner lacks
      `--session-dir`.
    - `tests/e2e/test_watch_cycle.py` for E2E-001 to E2E-005, reusing the `_setup` and `_cli`
      fixtures and extending the `gh` stub with comment recording and the writer-login
      answers.
    - The tests make no model calls. They use a stand-in `agent-runner` on `PATH`.

### Done when

- Every requirement and scenario in both delta specs is implemented.
- INT-001 to INT-007 and E2E-001 to E2E-005 pass, and the full suite passes under
  `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build`
  pass. `agent-validate run` passes.
- `PRAGMA user_version` stays 4.
- With `[watch]` absent, or `enabled = false`, behavior is unchanged.
- HT-001 is left for Paul after he merges and deploys.
