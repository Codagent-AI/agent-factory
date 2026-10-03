## ADDED Requirements

### Requirement: Configure session notifications

The shared configuration SHALL accept an optional `[notify]` section with these settings:

- `enabled` (default false);
- the delivery `agent` profile in `cli:model:effort` form, whose CLI SHALL be `claude`, required when notifications are enabled;
- `settle_seconds`, the settle period (default 360, zero or more);
- `watch_wait_minutes`, the watch wait (default 25, zero or more);
- `daily_sessions`, the per-day delivery cap (default 30, zero or more);
- `timeout_minutes`, the delivery timeout (default 5, at least 1).

When notifications are enabled, configuration loading SHALL fail on a missing profile, a profile that is not in `cli:model:effort` form, a CLI other than `claude`, or a value out of range, and the failure SHALL name the setting. A missing section, or `enabled = false`, SHALL keep today's behavior. The Codagent shared configuration SHALL enable notifications with the profile `claude:claude-haiku-4-5-20251001:low`. Each cycle SHALL read the notify settings from the configuration it loads, so a change applies to stops detected after it. A delivery session that is already running SHALL keep its profile and timeout.

#### Scenario: Reject a non-Claude profile

- **WHEN** notifications are enabled with the agent `codex:gpt-5:low`
- **THEN** configuration loading fails and names the `[notify]` agent setting

#### Scenario: Leave notifications unconfigured

- **WHEN** the shared configuration has no `[notify]` section
- **THEN** no stop is detected, no delivery session starts, and status shows notifications as disabled

### Requirement: Diagnose notification readiness

When notifications are enabled, `agent-factory doctor` SHALL run a `notify` group. The group SHALL verify:

- that the `claude` CLI and `ps` are executable on the service PATH;
- that the notify profile is a valid `claude` profile and the Claude CLI is authenticated;
- that the Claude CLI supports the options the notifier needs (`--tools`, `--allowedTools`, `--json-schema`, and `--strict-mcp-config`);
- that the local Claude session registry directory exists and is readable.

It SHALL report an empty registry as informational, not as a failure. A failing notify group SHALL affect only the start of delivery sessions: a stop detected while it fails SHALL start no session and SHALL be recorded `failed` with the doctor reason. Detection, the watcher, and every kind's admission SHALL continue. Doctor SHALL NOT read or print registry key files or tokens.

#### Scenario: Claude is not authenticated

- **WHEN** notifications are enabled and the Claude CLI on the service PATH is not logged in
- **THEN** doctor reports the notify group as failing and names the authentication problem, and the other groups report independently

### Requirement: Report session notifications in status

When notifications are enabled, `agent-factory status` SHALL show a notify section with:

- whether notifications are enabled;
- each running delivery session with its issue, stop kind, and elapsed time;
- the number of Claude notifier sessions launched today against the cap, and today's known estimated cost;
- the recent notification records (from the last 24 hours), each with its issue, stop kind, outcome, the target session's recorded name, and any `watch event missed` or `watch dispatch waiting` note.

When notifications are disabled, status SHALL show one line saying so and SHALL still list running delivery sessions. Status SHALL NOT start or change any delivery.

#### Scenario: Inspect today's notifications

- **WHEN** three stops were notified today, two `sent` and one `no-session`
- **THEN** status lists the three with their issues, stop kinds, and outcomes, and shows 2 sessions against the daily cap, because the `no-session` stop launched none

### Requirement: Document session notifications

`AGENTS.md` and `docs/operations.md` SHALL describe session notifications:

- that `codagent-github-project` records the creating session and `factory-assign` replaces it, and that one session is tracked per issue;
- the stop kinds, the settle period, and the wait for the service watcher;
- that a message only notifies and carries the issue, kind, what happened, and links;
- that delivery is best effort: an ended, renamed-and-reused, or unresolvable session gets nothing; a session in another permission mode may hold the message; the name race is a known limitation;
- the `[notify]` settings and defaults, the notify doctor group, and the notify section of status.

The `codagent-github-project` and `factory-assign` skills SHALL state that they record the session. The `factory-watch` skill SHALL state that marked issues notify their recording session on their own, so `factory-watch` is needed only for issues without a marker or to follow issues from another session.

#### Scenario: Decide whether to start factory-watch

- **WHEN** an agent that just assigned an issue with `factory-assign` reads the `factory-watch` skill
- **THEN** it learns that its session will be notified when the factory stops progressing on that issue, and that it need not start a watch for it
