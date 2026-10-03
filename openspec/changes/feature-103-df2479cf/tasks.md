- [ ] Notify the originating Claude session when the factory stops progressing on an issue, end to end

## Task: Session notifications for factory stops (#103)

Implement the whole change described in these files in the change directory:

- `proposal.md`. The notifier is a direct Claude CLI session, not an Agent Runner session; see the
  design decision.
- The delta specs under `specs/`:
  - `factory-session-notification` (new): every ADDED requirement and scenario.
  - `factory-operations`: ADDED "Configure session notifications", "Diagnose notification
    readiness", "Report session notifications in status", and "Document session notifications".
- `design.md`, which is authoritative for module layout, function names, the marker format, the
  `notify_stop` schema, the classification table, the notifier command line, the prompt and result
  schema, the message format, and pruning.
- `decisions.md`. Where entries differ, later entries override earlier ones. In particular,
  approach-review AR-1 to AR-4 override the design-stage text they revise:
  - `notify.begin` runs before the cycle body;
  - `get_issue_presence` is used for absent cards, together with `restart_settle`;
  - the details link comes only from the claim's own reporting receipts;
  - the cap counts launched sessions only.
- The automated obligations in `test-plan.md`: INT-001 to INT-007 and E2E-001.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, or anything under `/Users/paul/codagent/*`. Never run `scripts/deploy.sh`
or `launchctl`.

Automated tests must never run the real `claude` CLI or send a cross-session message. Use the fake
`claude` executable the test plan describes. Never read or print `~/.claude/sessions/*.key`, and
never message any of Paul's existing Claude sessions.

### Scope

1. **Marker** (`src/agent_factory/notify/marker.py`, design "Marker"):
   - `parse`, `render`, `stamp`, and `carry`, with the exact single-line
     `<!-- codagent-session: {...} -->` form. The name is reduced to `[A-Za-z0-9._-]` with no `--`,
     and the last valid marker wins.
   - The `python -m agent_factory.notify.marker stamp FILE` / `carry OLD NEW` CLI. `stamp` without
     `CLAUDE_CODE_SESSION_ID` is a no-op that exits 0.
2. **Registry** (`notify/registry.py`, design "Registry resolution"):
   - read only `*.json` files with a single `.` in the name, and only the fields `sessionId`,
     `name`, `pid`, and `procStart`;
   - check the start time with `TZ=UTC ps -o lstart= -p <pid>`;
   - return `None` for no match, more than one match, a dead or reused pid, or an unreadable
     directory.
3. **Store** (`ClaimStore._ensure_notify_schema`, `notify/store.py`, design "Store"):
   - the `notify_stop` table, including `restart_settle`, created outside the versioned schema with
     `SCHEMA_VERSION` unchanged;
   - forward-only conditional state transitions;
   - the `settings('notify','cursor')` cursor;
   - a launched-session daily count.
4. **Cycle wiring** (`runtime.py`, design "Cycle wiring"):
   - `_watch_finally` yields a `CycleView`;
   - `notify.begin` runs before the body and persists `enabled_at` on first enable;
   - the body sets `view.cards` after `list_project_items`;
   - `notify.step` runs in `finally` after `watch.step`, with every sub-step wrapped in `_safe`;
   - notify runs while the factory is paused, and supervision runs even when the body raises.
5. **Detection** (`notify/detect.py`, design "Detection"):
   - candidates come from the store, with the 7-day horizon and `enabled_at`;
   - absent issues are verified through a new `GitHubClient.get_issue_presence(repository,
     number)` GraphQL method that returns state, labels, body, and membership of
     `shared.project.id`;
   - unknown state sets `restart_settle`;
   - `classify` follows the design table exactly, including `waiting_review` and the queued-card
     rule;
   - the settle rows and the watch gate (`watch_wait_minutes`, with `watch event missed` and
     `watch dispatch waiting` notes).
6. **Delivery and supervision** (`notify/deliver.py`, `notify/supervise.py`, design "Delivery",
   "Supervision", "Message"):
   - the marker comes from the snapshot card or the detection read; delivery makes no GitHub call;
   - outcomes are `unmarked` or `no-session`, then the budget check on launched sessions, then a
     lazy readiness check that writes `runtime/readiness:notify`;
   - the message is rendered with a `Details:` link only from the run's own reporting receipt;
   - resolve again, then launch;
   - the `/bin/bash` wrapper runs `claude -p` with exactly the design's flags, from an
     `agent-factory-notify` working directory, with `inherited_environment()` and no
     `--permission-mode`;
   - supervision reuses `process_identity_status` and `terminate_owned_process`, parses
     `structured_output` and `total_cost_usd`, and enforces the launch lease and the timeout.
7. **Pruning**: delete ended rows and their evidence after `max(evidence_retention_days, 8)` days.
   When notifications are disabled, clear the cursor and drop `settling` rows.
8. **Configuration** (`config.py`, `config/codagent.toml`, design "Configuration"):
   - `NotifyConfig` and `_notify_config` with the design's keys, defaults, and error messages
     (`notify.agent must be a claude:model:effort profile`);
   - `SharedConfig.notify`;
   - add `[notify] enabled = true`, `agent = "claude:claude-haiku-4-5-20251001:low"` to
     `config/codagent.toml`.
9. **Doctor and status** (`notify/readiness.py`, `notify/status.py`, `operations.py`, design
   "Doctor and status"):
   - the `notify` group, appended to `_GROUP_ORDER`, checking:
     - `claude` and `ps` on the service PATH;
     - Claude authentication, without the plugin requirement;
     - the `claude --help` flags;
     - the registry directory, where empty is informational;
   - the status section: launched rows, sessions launched today against the cap with cost, and
     the last 24 hours' ended rows with `unmarked` hidden.
10. **Skills**:
    - `.claude/skills/codagent-github-project/SKILL.md`: `stamp` before `gh issue create`, `carry`
      around every body edit, and continue without a marker if the command fails.
    - `.claude/skills/factory-assign/assign.py` and `SKILL.md`:
      - write the marker with `marker.stamp` through Paul's `gh` after every refusal check passes
        and before the Owner and Status writes;
      - make no write when the issue is refused or `CLAUDE_CODE_SESSION_ID` is unset;
      - `report` prints a `session:` line.
    - `.claude/skills/factory-watch/SKILL.md`: when a watch is unnecessary.
11. **Documentation**: `AGENTS.md` (a short "Session notifications" section) and
    `docs/operations.md`. Cover every item in the operations "Document session notifications"
    requirement, including the registry and Claude CLI dependency, the reused-name race, and held
    messages.
12. **Tests**:
    - implement INT-001 to INT-007 and E2E-001 at the locations and with the setup `test-plan.md`
      gives;
    - add the unit tests listed in its "Coverage Strategy";
    - existing watch, cycle, and assign tests must pass unchanged, apart from the `_watch_finally`
      holder plumbing.
13. **Pull request description**: include an orange attention item. It says that live delivery
    depends on Claude Code's undocumented `~/.claude/sessions` layout and its cross-session
    messaging, and that the acceptance pass is the only live check.

### Done when

- Every requirement and scenario in both delta specs is implemented.
- A marked issue's claim that stops (pull request, needs-input, failed, eval settled, cancelled, or
  not queued) produces exactly one notifier launch per `(claim, run, stop kind)`. This happens
  after the settle period and after the watcher's dispatch for that run has ended (or the watch
  wait has passed). Nothing is resent across cycles, restarts, or pauses.
- Unmarked issues, never-claimed issues, runs from before enablement, active evals between
  repetitions, queued cards, and pending review rounds never notify.
- Unknown GitHub state never completes a settle period.
- The notifier runs only with `ListAgents` and `SendMessage`, a minimal environment, the
  configured profile, and the timeout. The rendered message holds only the issue, kind, what
  happened, the issue, pull request, and claim links, and an optional own-receipt `Details:` link.
- Outcomes `sent`, `no-session`, `failed`, `budget-exhausted`, and `unmarked` are recorded. None of
  them changes a claim, run, card, label, or comment, or stops the cycle.
- `codagent-github-project` and `factory-assign` write and replace exactly one marker as specified,
  and an eval request with a marker still parses.
- `doctor` shows the `notify` group. `status` shows the notify section. The configuration
  validates and `config/codagent.toml` enables notifications.
- `notify_stop` is additive, and `SCHEMA_VERSION` is unchanged.
- INT-001 to INT-007 and E2E-001 pass, and the full suite passes under `uv run pytest`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build` pass, and
  `agent-validate run` passes.
- No changes are made outside this repository.
