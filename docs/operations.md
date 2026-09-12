# Operating Agent Factory

Use the installed command with its explicit local configuration:

```sh
agent-factory --config /absolute/path/to/config.toml doctor
agent-factory --config /absolute/path/to/config.toml status
agent-factory --config /absolute/path/to/config.toml tick
agent-factory --config /absolute/path/to/config.toml pause
agent-factory --config /absolute/path/to/config.toml resume
```

`doctor` is read-only. It checks shared configuration and mappings, private
credential files, source repositories, selected suite entry point/launcher,
candidate token file, Docker, model authentication, and the configured
free-space floor. It distinguishes each failing prerequisite and operator
action; it neither starts an evaluation nor repairs credentials or configuration.
Run it again after a repair—ordinary readiness rechecks clear an available
prerequisite without consuming an execution retry.

`doctor` reports the configured `agent-evals` harness branch and the commit it
currently resolves to as `harness branch <ref> → <sha>`, resolved locally
without fetching. This is not proof that revision carries the suite behavior
Factory depends on (see [suite integration](suite-integration.md)); each claim
resolves and records its own harness commit at admission, independent of what
`doctor` last reported.

`status` is also read-only. It reports saved pause state, current issue/unit and
attempt, progress, readiness/quota holds, admission window, unfinished
reporting, and cleanup errors. It prints a next permitted start only for a known
schedule boundary; a missing credential, Docker, or other operator action has
no invented recovery date.

`tick` runs the resident service's normal immediate reconciliation path. It is
not a preview or force option: pause, window, quota/readiness holds, free-space
checks, and the one-execution guard still apply. A running supervisor continues
after `tick` exits. `pause` is stored in SQLite and survives command or
controller restarts; it permits the already-running unit to finish but blocks
the next repetition/recovery. `resume` clears only pause and leaves quota and
prerequisite holds in place.

The default local admission window is 00:00 through (but excluding) 15:00 in
the configured timezone. Defaults are 30 minutes without progress, six hours of
execution excluding recognized quota waits, 12 hours total, and a five-hour
recognized-Codex fallback; all are local TOML values. Closing a tracked issue
cancels only Factory-owned work. A Running item dragged to Ready, Review, or
Done while its execution is verified is corrected back to Running; its worktrees
are retained.

## Service management and storage

Restart the controller without touching independent supervisors:

```sh
launchctl kickstart -k gui/$(id -u)/com.codagent.agent-factory
tail -f /absolute/path/to/.agent-factory/logs/controller.log
```

The root contains `state.sqlite3`, controller and per-run logs, factory-owned
worktrees, and artifacts. Inspect disk use with `du -sh <root>/*` and inspect
SQLite only while respecting active writers. Suite evidence, candidate outputs,
and factory logs are separate and retained through human review. When a reviewed
card moves to Done, Factory removes only its recorded owned worktrees; it never
automatically deletes evidence, candidate branches, PRs, or shared checkouts.

For a ready-for-human-review result, use the absolute, quoted command in the
Factory report on the Mac holding its retained artifacts and harness worktree.
That command is available until the reviewed item moves to Done. Factory does
not run human ratings, assign an official pass, close the issue, merge a PR, or
claim that a static plist proves live launchd acceptance.
