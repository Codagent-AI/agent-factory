# Operating Agent Factory

## Fly eval operations

With `eval.execution = "fly"`, `doctor` reports the mode-neutral `eval` group
and the `eval-fly` group (deploy token, app API, image manifest, and `flyctl`
transport). `status` shows the backing Machine ID, state, and deadline for an
active repetition; a quota hold shows `stopped (quota hold)` and its retained
Machine deadline. It also reports reconciliation findings for unknown Machines,
cleanup failures, and ownership mismatches.

An ownership mismatch blocks new Fly eval launches. Do not destroy a Machine
whose identity is uncertain: inspect it, correct the recorded state if the
cause is understood, or let the deadline reconciler remove it. A lost Machine
costs that repetition and is never automatically rerun or presented as a
product result. If every repetition is lost, the claim is `infra-error`.

On a recognized Codex quota hold Factory stops, rather than destroys, the
Machine and refreshes its deadline from the reset time and admission window.
When execution becomes eligible it starts the same Machine and verifies the
checkpoint before resuming. Human review always runs on the Mac against the
collected artifact directory, not against a Fly Machine. For diagnosis the
launcher supports `stand-in` and `attach` modes; neither is a normal execution
path.

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

`doctor` groups every check under one heading per readiness class, in this
order: `shared` (storage root and free space with both kinds' floors, the
shared configuration file and Project mappings, GitHub App key and access, the
Agent Runner and Agent Skills repository checks both kinds clone, Docker's
reclaimable space, the PATH `doctor` resolved executables against, and the
installed LaunchAgent's PATH check); `eval-sandbox` (the Docker daemon, the
memory allowance against the reservation, eval role model authentication, the
harness branch, the `agent-evals` repository, the suite entry point and
launcher, the suite environment file, and suite prerequisites); and, for the
configured `[fix] execution` mode only, either `fix-sandbox` (Docker-mode fix
checks) or `fix-host` (host-mode fix checks). A passing line never prints an
`action:`. Admission mirrors this: a kind is held only by a failure in
`shared` or in the group applicable to that kind under its configured mode, so
a Docker outage holds evals but not a fix kind configured for host execution,
and a disk floor below the eval minimum but above the (optionally lower) fix
minimum holds evals only. When Docker is running, `doctor` also reports the
space `docker system prune` / `docker builder prune` would reclaim; it never
runs either command.

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

By default `status` lists only claims that are running, waiting, blocked,
held, in Review, or pending a merge sync; a claim whose card is Done with
nothing left pending, and any superseded claim, is hidden, and the header
reports how many were hidden. `agent-factory ... status --all` lists every
saved claim, including settled and superseded ones.

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
cancels only Factory-owned work; a cancelled fix claim's clones and credential
copy are released as soon as its attempt has stopped, and its evidence stays
until retention removes it. A Running item dragged to Ready, Review, or
Done while its execution is verified is corrected back to Running; its worktrees
are retained.

## The fix work kind

A writer files a Bug-typed issue, or drags a tracked Bug to `Status=Ready` in a
configured source repository; the next factory poll sets `Owner=factory`.
Factory admits it in board order, comments
the admission notice with `Refs` recording the frozen target/Runner/Skills
commits, and runs the factory's packaged fix workflow in the same Docker sandbox used
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

**Host execution.** When `[fix] execution = "host"`, fix attempts run through
the installed Agent Runner directly on the Mac, as the operator's own user,
rather than in the Docker sandbox. In host mode, on a machine where the
operator's own GitHub login is available, the separate fix credential and the
target repositories' PR-only rulesets are conventions the launched process
follows, not boundaries an autonomous agent cannot cross — a host attempt runs
yolo as the operator's user with no filesystem boundary. Neither the recorded
Runner commit nor the recorded Skills commit executes in host mode; the
installed `agent-runner` binary runs instead, and its resolved path and
`-version` output are recorded on the attempt. The enforceable controls are
trusted-writer admission (only writers can route a bug to the factory) and
human merge (the factory never merges its own fix PRs). See
[installation](installation.md#host-execution-for-fixes) for the doctor checks
host mode requires.

A host attempt leaves these traces and nothing else: the packaged workflow and
the role profiles staged into the attempt's own clone under `.agent-runner/`
(git-ignored there and deleted with the clone at Done); the wrapper
`host-run.sh`, a per-attempt global git config, and the askpass helper next to
the credential copy under `<storage_root>/private/<run>/` (deleted at Done);
`host-provenance.json` in the attempt's artifact directory, written before
launch so it exists however the attempt ends; and the Runner session under
`<artifact directory>/agent-runner-session/`, which the wrapper passes with
`--session-dir` so nothing accumulates under `~/.agent-runner/projects/`. The
persisted plan holds those paths only; the token is read by the wrapper at
exec time and never enters SQLite, the argv, or the logs. Outcome comments for
a host attempt say it ran on the host with the installed Runner and Skills.

**Evidence.** Each fix attempt gets its own artifact directory,
`<storage_root>/artifacts/<claim>-fix/attempt-<n>/`, mounted at `/artifacts`
inside the sandbox. It holds the issue input the factory wrote
(`input/issue.json`: title, body, attempt number, prior factory PR, and the
eligible writer comments), the sandbox log (`factory-suite.log`), the Runner
session (`agent-runner/projects/.../runs/<id>/` with `state.json`, `audit.log`,
and step output), and the structured `fix-outcome.json`. Attempts never share
a directory, so a recovery retry cannot read a stale outcome. Evidence is
retained until the evidence retention rule below removes it. The single-line
copy of the fix credential the
sandbox loads lives outside the artifacts, under `<storage_root>/private/<run>/`,
owner-readable only, and is deleted with the clones and images when the card
reaches Done.

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
Done, Factory removes only its recorded owned worktrees, clones, and images;
it never deletes candidate branches, PRs, mirrors, or shared checkouts, and it
only prunes evidence under the rule below.

**Evidence retention.** A configurable retention period, `[limits]
evidence_retention_days` (default 14), bounds how long a settled claim's
evidence is kept. The clock starts the first time Factory durably observes a
claim's card as Done; observing any other status resets it, so moving a card
back out of Done and later returning it to Done restarts the period from that
later observation. Once the period has elapsed since that observation, and
the claim has no non-terminal or unverified run, no unfinished reporting, no
pending post-merge sync, and (for a non-superseded claim) its worktree, clone,
image, and credential cleanup has completed, the next tick prunes that
claim's evidence: logs, Runner and agent session state, and agent output
under each attempt's artifact directory (and, for a host attempt, its
recorded Runner session directory). It keeps the fix outcome or eval result
and provenance records, the attempt's issue input, and never touches
candidate branches, PRs, mirrors, SQLite history, or the operator's working
clones. A superseded claim is pruned on the same conditions judged on its own
runs and reporting, without waiting on cleanup it never performs. Pruning is
retried on later polls if a removal fails; `claim.cleanup.retention` records
what was removed and any failures — inspect it with `status --all` or by
reading the claim's row in `state.sqlite3` directly. This check runs from the
per-claim loop on every tick, so history predating this rule is covered
automatically: the first tick after upgrading records the Done observation
for old claims and prunes them only after the retention period from that
observation, not retroactively.

For a ready-for-human-review result, use the absolute, quoted command in the
Factory report on the Mac holding its retained artifacts and harness worktree.
That command is available until the reviewed item moves to Done. Factory does
not run human ratings, assign an official pass, close the issue, merge a PR, or
claim that a static plist proves live launchd acceptance.
