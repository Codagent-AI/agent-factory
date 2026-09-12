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

`status` is also read-only. It reports saved pause state, one block per work
kind (`eval slot: ...` / `fix slot: free`) naming that kind's holder or
reporting it free, why a kind is waiting (window, pause, per-kind readiness, a
provider quota hold naming which kinds it blocks, memory, or disk), blocked fix
claims with their decline reason, pending merge syncs with their last failure
reason, unfinished reporting, and cleanup errors. It prints a next permitted
start only for a known schedule boundary; a missing credential, Docker, or
other operator action has no invented recovery date. A quota hold on a
provider that no configured role for a kind uses is reported as not blocking
that kind's admission.

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

## The fix work kind

A writer files or drags a Bug-typed issue to `Owner=factory` / `Status=Ready`
in a configured source repository. Factory admits it in board order, comments
the admission notice with `Refs` recording the frozen target/Runner/Skills
commits, and runs the companion fix workflow in the same Docker sandbox used
for evals, under its own slot and limits. The workflow produces one of three
outcomes: a pull request against the target repository (moves the card to
Review with `pending-human-review`); a `needs-input` decline with reasons,
when the agent judges the bug unsafe to fix autonomously (moves the claim to
`blocked` and applies `needs-input`); or a typed failure.

**The blocked-bug loop.** A blocked claim stays out of admission until a
writer comment newer than the decline is posted, or the card is dragged back
to Ready — the `needs-input` label distinguishes it from work genuinely
waiting in Ready. Comments from the bot itself, from non-writers, or older
than the decline do not re-admit it. Re-admission starts a fresh attempt that
includes the eligible comments as additional input.

**Reviewing a factory fix PR.** Treat it like any other contributor PR: read
the description and diff, check it against the linked issue, and merge or
request changes normally. The factory never merges its own fix PRs.

**The merge sync.** After a fix PR merges, Factory fast-forwards the
operator's configured working clone for that target repository to the merged
commit, so the operator's local checkout stays current without manual
fetching. `status` reports a pending sync and its last failure reason — for
example, the working clone has uncommitted changes, is missing entirely, or
the fast-forward itself failed — until the operator resolves it (commit or
stash local changes, restore the clone, or fetch and fast-forward it by hand)
and Factory's next pass retries.

**Evidence.** Each fix attempt gets its own artifact directory,
`<storage_root>/artifacts/<claim>-fix/attempt-<n>/`, mounted at `/artifacts`
inside the sandbox. It holds the issue input the factory wrote
(`input/issue.json`: title, body, attempt number, prior factory PR, and the
eligible writer comments), the sandbox log (`factory-suite.log`), the Runner
session (`agent-runner/projects/.../runs/<id>/` with `state.json`, `audit.log`,
and step output), and the structured `fix-outcome.json`. Attempts never share
a directory, so a recovery retry cannot read a stale outcome. Evidence is
retained until manual cleanup. The single-line copy of the fix credential the
sandbox loads lives outside the artifacts, under `<storage_root>/private/<run>/`,
owner-readable only.

## Service management and storage

Restart the controller without touching independent supervisors:

```sh
launchctl kickstart -k gui/$(id -u)/com.codagent.agent-factory
tail -f /absolute/path/to/.agent-factory/logs/controller.log
```

The root contains `state.sqlite3`, controller and per-run logs, factory-owned
worktrees and clones, mirrors, and artifacts. Inspect disk use with
`du -sh <root>/*` and inspect SQLite only while respecting active writers.
Suite evidence, candidate outputs, and factory logs are separate and retained
through human review. Fix attempts add growth beyond evals: a fresh clone of
the target repository, Runner, and Skills per attempt, plus that attempt's
per-run Docker image; both are cleaned up once the claim reaches Done, but
mirrors persist and grow slowly with history. When a reviewed card moves to
Done, Factory removes only its recorded owned worktrees, clones, and images; it
never automatically deletes evidence, candidate branches, PRs, mirrors, or
shared checkouts.

For a ready-for-human-review result, use the absolute, quoted command in the
Factory report on the Mac holding its retained artifacts and harness worktree.
That command is available until the reviewed item moves to Done. Factory does
not run human ratings, assign an official pass, close the issue, merge a PR, or
claim that a static plist proves live launchd acceptance.
