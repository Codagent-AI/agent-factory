# Operating Agent Factory

## Fly setup

Evals can run in Fly.io Machines instead of Docker. One-time setup:

1. Create a Fly organization and an app for the sandbox Machines, for example
   `flyctl apps create agent-factory-sandbox -o <org>`. The app runs no service
   of its own; the factory creates and destroys Machines in it.
2. Create a deploy token for that app (`flyctl tokens create deploy -a <app>`)
   and store only the token, on one line, in an owner-readable file such as
   `~/.agent-factory/credentials/fly-deploy-token`. The same token authenticates
   the Machines API, the image registry, and `flyctl ssh`.
3. Use an Agent Runner branch that includes the Fly sandbox Dockerfile from
   PR #117 and its `FACTORY_CLI_REFRESH` build argument. The checkout must also
   create `/eval-input`, `/agent-runner-source`, and `/agent-skills-source` and
   give them to the unprivileged user the job runs as. Under Docker those paths
   arrive as bind mounts the daemon creates, but a Fly guest has no binds and
   clones into them itself, so without that the first `git clone` fails with
   `could not create work tree dir: Permission denied`.

   The factory builds from that pinned checkout on Fly's remote builder once
   per claim. It tags `claim-<first 12 claim id characters>` and pins the
   digest for every attempt.
   `[fly] image` names the repository to push to; its configured tag is ignored.
   The launcher writes a temporary app config outside the checkout. Old
   `claim-` registry tags are not removed automatically.
4. Install `flyctl` where the LaunchAgent's PATH can find it.
5. In `local.toml` set `[eval] execution = "fly"` and a `[fly]` table (see
   `config/local.example.toml`). Defaults: region `ewr`, `shared` CPUs, 4 CPUs,
   8192 MiB, a 900-second collection grace, and a 20-second heartbeat.

Fly execution keeps Docker's isolation and the suite unchanged, but gives up
Cursor role profiles (the Cursor CLI is not reliable headless) and adds a per
attempt cost bounded by the deadline: about $0.75 at the default size and
limits. A lost Machine costs its repetition, which is settled as failed without
a retry. Human review always runs on the Mac against the collected directory.

## Fly eval operations

Fly evals pin Agent Validator from `[repositories] agent_validator`, which defaults
to a checkout next to Agent Runner. The image builds the recorded revision and
the report includes it. Docker evals still use the published npm release.
Validator changes can alter eval results; retain the recorded revision when
comparing runs.

With `eval.execution = "fly"`, `doctor` reports the mode-neutral `eval` group
and the `eval-fly` group (deploy token, app API, image repository, Claude login, and `flyctl`
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

## Model authentication

Factory never holds model API keys of its own. Every agent (Claude Code, Codex)
runs on the operator's subscription logins, which reach the agent in one of two
ways depending on where the work runs.

### Where the logins live on the Mac

| CLI | Live login | File copy |
| --- | --- | --- |
| Claude Code | macOS Keychain, service `Claude Code-credentials`, **account `$USER`** | `~/.claude/.credentials.json` (not maintained by Claude on macOS) |
| Codex | `~/.codex/auth.json` | same file |

Claude Code picks the Keychain item by the `USER` environment variable. When
`USER` is unset it silently reads and writes a *different* item under account
`unknown`, which is never refreshed by your terminal sessions and goes stale.
List the items with:

```sh
security dump-keychain | grep -B4 -A4 '"svce"<blob>="Claude Code-credentials"' | grep -E 'acct|mdat'
```

The one under your user name with a recent `mdat` is the live login. A stale
`acct=unknown` item is harmless once nothing reads it.

### Host bug fixes

Fix jobs run the host's `claude` and `codex` directly, so they use the live
Keychain login and `~/.codex/auth.json`. Two things must hold for that:

- The LaunchAgent sets `USER` and `LOGNAME` (in its `EnvironmentVariables`),
  so the resident controller runs as the real login identity.
- The supervisor passes `PATH`, `HOME`, `USER`, `LOGNAME`, `TMPDIR`, `LANG`, and
  `LC_ALL` (and nothing else from its environment) to each job.

Symptom when `USER` is missing: the fix fails at triage with `Failed to
authenticate: OAuth session expired and could not be refreshed` while `claude`
works in a terminal. Reproduce with
`env -i HOME=$HOME PATH=/opt/homebrew/bin:/usr/bin:/bin:$HOME/.local/bin claude -p ok`
(fails) against the same command with `USER=$USER` added (works).

### Fly evals

A Machine has no Keychain. The Fly launcher delivers Codex's
`~/.codex/auth.json` and resolves Claude in this order: a nonempty
`CLAUDE_CODE_OAUTH_TOKEN` in the suite environment, the Mac Keychain item
`Claude Code-credentials` for the service's `USER`, then
`~/.claude/.credentials.json`. A Keychain login is streamed directly to the
Machine and is never written to a file on the Mac. The LaunchAgent must set
`USER` and `LOGNAME`. Doctor and admission check that the login can be
delivered before a Machine is created. A blocked Keychain read times out.

### Quick diagnosis

| Symptom | Where | Fix |
| --- | --- | --- |
| Fly Claude login unavailable | Fly eval | Check the suite token, then the service user's Keychain login with `claude auth status`; run `claude auth login` if needed |
| `OAuth session expired and could not be refreshed`, `claude` works in a terminal | Host fix | Confirm the resident has `USER` (`ps -E -o command= -p <pid>`); add it to the LaunchAgent and restart |
| Same error on a Fly eval | Fly eval | Refresh the suite token or the service user's Keychain login, then rerun `doctor` |
| Same error everywhere, including a terminal | Both | Run `claude auth login`, then rerun `doctor` |

## Fly Machine lifecycle

A Machine is created without Fly's auto-destroy, because on Fly that setting also
destroys a Machine on an API stop, which a quota hold relies on. At its deadline a
Machine stops itself, which ends compute billing; the controller's next cycle
destroys it. A stopped Machine keeps only its disk, billed as storage, so if the
controller is off for a long time, check `flyctl machine list -a <app>`.

`stand-in` runs a script of yours through the same create, verify, deliver,
observe, collect, and destroy path an eval uses, without touching the claim
store. It creates a real, billed Machine and destroys it on exit unless `--keep`
is given. Add `--mount-codex-auth` or `--mount-claude-auth` to deliver those
logins under `/host-home` exactly as an eval would.

```sh
agent-factory-fly-launcher stand-in --config /absolute/path/to/local.toml \
  --run-dir /absolute/path/to/scratch-run --script ./check.sh --deadline-seconds 300
```

The collected files, `launcher.log`, and the Machine record land under the run
directory. A second `stand-in` against the same directory runs another job in
the kept Machine. `attach --run-dir DIR` reconnects to a job already running in
the Machine that directory records.

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

With `[feature]` configured, `doctor` adds `feature-host`: it checks the feature
roles, including `crosscheck` CLI authentication, the packaged feature and
define workflows, the installed Runner's `core/verify-change`, the shared fix
credential, and the feature disk floor. A target without `openspec/` is listed
as informational. `status` always shows `feature slot: free` or its holder,
and lists feature claims even if `[feature]` is later removed. Removing that
section stops new handoffs and admissions while existing claims continue to
be reported, synced, cleaned up, and pruned.

### Selecting an and-scene fixture for an eval

Add `fixture_ref = "<and-scene branch, tag, or commit>"` to the request's
fenced `eval` TOML block to test a fixture change. Push the commit to a branch
or tag on `https://github.com/Codagent-AI/and-scene.git` first. The factory
uses `[repositories] and_scene` to fetch and resolve it at admission, requiring
the commit to be published on that origin. The checkout defaults to the
`and-scene` sibling of `[repositories] agent_runner`; `doctor` reports its
condition informationally. If the key is omitted, the frozen agent-evals
harness pin supplies the fixture and the checkout is not used.

The frozen-inputs comment identifies the requested ref and full fixture SHA;
each repetition comment includes the SHA, and the Project `Refs` field ends in
`fixture@<first seven characters>` for pinned claims. Results from a
non-default fixture are not comparable with results using the agent-evals pin.
If the commit's only published branch is deleted before the claim finishes,
later repetitions can fail at fixture checkout.

Before rolling back to a release without fixture support, run
`agent-factory --config <local.toml> pinned-claims --revision fixture` to find
unfinished pinned claims. `scripts/deploy.sh` refuses that rollback while any
are present. Pause the factory, let each claim settle or cancel it, then deploy
the older release. The older release cannot accept `fixture_ref`; if a pinned
evaluation is still needed, stay on a fixture-capable release. A new request
without the key evaluates only the default fixture. A hand rollback or a
deploy with an older script bypasses the refusal.

### Feature pull requests

Move a writer-authored Feature issue in a configured fix target to Ready to
request a host feature attempt. Factory verifies the author's repository
permission before taking ownership. The first attempt starts fresh and freezes
the target, Runner, and Skills refs and the four role profiles. A successful
attempt links its pull request, reports red, orange, and yellow review counts,
and moves the card to Review with `pending-human-review`. A failed product
outcome moves it to Review with `failed`; exhausted technical recovery uses
`infra-error`. Feature attempts run on the host with the installed Runner and
Skills plugin, so the recorded Runner and Skills commits are provenance rather
than the executed versions. After a human merges the PR, the normal pull
request sync updates the operator's working clone and closes the issue; Done
then releases the claim's clones and credential copy.

When definition needs a decision, Factory leaves the card in Running with
`needs-input` and comments with the questions, drafted direction, and branch
link. Answer in a new issue comment as a repository writer, or drag the card
from Running to Ready. The same claim resumes at the stopped definition step;
a preflight stop for a repository without `openspec/` starts fresh after it is
initialized. Bot and non-writer comments do not resume work. A technical
failure resumes from the latest pushed checkpoint when one is available.
Each feature resume and continuation merges the current configured target branch
into the claim branch before work continues. The admission target, Runner, and
Skills revisions remain frozen for that claim. A merge conflict is resolved
within the attempt when possible; otherwise Factory asks a question naming the
conflicting files and keeps the pushed branch. Answer in a new writer comment.
You can fix the cause on the target branch or commit directly to the claim
branch; the next attempt fetches and merges the target again. Feature pull
request review rounds also merge the current target before triage. A rollback
to a release without first-parent checkpoint reading can select a checkpoint
from merged target history, so inspect claims resumed since deployment before
rolling back and re-admit affected claims.

To continue a settled failed feature, drag its card from Review to Ready.
Factory creates a new claim and, when the earlier branch has a plan checkpoint,
continues from that branch at implementation using newly resolved target,
Runner, and Skills commits. If the feature has an open factory pull request,
the card returns to Review and no new claim starts. Comment on that pull
request as a repository writer to request a review round through the feature
slot. A review round can still run when new feature admissions have been
disabled by removing `[feature]`; it does not repeat acceptance.

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

## The task work kind

To queue a maintenance chore, file a native Task in a `[fix]` target and move
its card to Ready, or run `factory-assign` with `--apply task`. It needs a writer
author, Owner=factory, and no `needs-input` label. Moving a Task to Ready is the
handoff; routing does not queue Tasks automatically. `[routing] task_type`
defaults to `Task`. Shared `[task]` enables intake, selects the
`factory-task/1` contract and three role profiles, and uses the fix targets,
branches, and credential. Local `[task]` is optional; only `execution = "host"`
is supported. Its default limits are 900 seconds inactivity, 7200 seconds
execution, and 10800 seconds total. Its window is always open unless a local
schedule is supplied, and `minimum_free_gib` can override the shared floor.
`doctor` shows `task-host`; `status` shows the task slot, blocked claims, and
claims waiting for review.

Triage declines behavior, public API or CLI, persisted data, OpenSpec specs,
credentials, release/deploy configuration, branch protection, cross-repository
work, oversized changes, and any product, design, compatibility, or other
decision the issue leaves open. Decline any decision the issue leaves open with
`needs-input`, naming that decision. Development tools and dependencies, CI, docs,
behavior-preserving refactors and cleanups are in scope. Publishing, versioning,
signing, tagging, and deploying are release configuration and out of scope.
The pre-push and post-finalize scope guards check the complete diff.
Task commits and PR
titles use `chore:`; review rounds stop for out-of-scope feedback.

Before enabling `[task]`, audit Ready Task cards in every fix target and move
any that are not approved for factory admission to Backlog.
Before rollback to a release lacking the task kind, settle or cancel open task
claims: older releases cannot supervise, report, or sync them.

## The fix work kind

Filing a Bug-typed issue only adds it to Backlog. A writer hands it to the
factory by moving its card to `Status=Ready` (or with the `factory-assign`
skill) in a configured source repository; the next factory poll sets
`Owner=factory`.
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
trusted-writer admission (the factory admits only writers' bugs) and
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

## Post-run audits

Post-run audits are temporarily disabled by `AUDIT_ENABLED = False` in
`src/agent_factory/audit.py` (Codagent-AI/agent-factory#60). While disabled,
host attempts run no audit replay, no `post-run-audit` events are posted,
`status` lists only previously recorded outcomes, and `doctor` reports audits
as disabled. To re-enable factory audits, set the constant to `True`, merge,
and deploy. Eval audits also require Agent Runner's automatic hook
(Codagent-AI/agent-runner#191).

When enabled, the following behavior applies.

Every factory run is audited, and its step-value observations go to the metrics Sheet
configured by `agent-runner audit setup`. Agent Runner audits only `openspec/` and
`spec-driven/` workflows by itself, so the factory starts the audit for its own runs:

- A host fix or review attempt runs `python -m agent_factory.audit host` in its launch
  wrapper after the workflow ends, whatever the result. It replays the audit with
  `agent-runner audit replay <session-dir> --session <id> --project <clone>` while the
  factory profile is still staged, waits for the audit, and retries delivery once.
- An eval audits inside its sandbox, which has no reporting connection. When the
  resident consumes the attempt, it delivers the collected reports from the Mac with
  `agent-runner audit retry`, using the Mac's Sheet as the destination.

Each attempt records `audit.json` in its evidence. An audit that did not deliver never
changes the attempt's result. It is posted as a `post-run-audit` issue event and listed by
`agent-factory status` for seven days. `doctor` checks that the installed Runner has
development audits and `audit replay --project`, and that the connection file is private.

To recover an attempt, run this from an Agent Runner checkout:

```sh
scripts/recover-development-audits.sh --execute \
  --session <evidence>/agent-runner-session:<execution-session-id>:<clone>
```

## Service management and storage

You can restart the controller at any time, including while fixes, features,
and evals run, without pausing first: supervisors, Fly launchers, and host
attempts are independent of it, and it adopts them when it starts again.
`kickstart -k` does not re-read a changed plist, so unload and load it instead:

```sh
launchctl bootout gui/$(id -u)/com.codagent.agent-factory
# wait until `launchctl print gui/$(id -u)/com.codagent.agent-factory` fails
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codagent.agent-factory.plist
```

On Paul's Mac, `scripts/deploy.sh` does all of this, deploying each version as an
immutable release. It also updates and builds Agent Validator from `origin/main`
when the checkout is safe and no host fix, feature, or task attempt runs. Use
`--no-validator` to skip that step. A host installation needs a one-time link
from `agent-validator` on the LaunchAgent PATH to the checkout's `dist/index.js`;
`doctor` checks it and reports whether the build is behind `origin/main`.
If the Validator build fails, the deploy stops with the factory paused and puts
the checkout's previous commit and `dist` back, but `node_modules` may already
match the new lockfile. If putting `dist` back fails, the deploy says where it
kept the previous build. Before resuming, rerun the deploy, or rebuild in the
checkout with `bun install --frozen-lockfile && bun run build:local`.
Each release is immutable and running jobs keep using it; see `AGENTS.md`.

`resident` does not write `controller.log`; use `status` and the per-run logs.

The root contains `state.sqlite3`, controller and per-run logs, factory-owned
worktrees and clones, mirrors, and artifacts. Inspect disk use with
`du -sh <root>/*` and inspect SQLite only while respecting active writers.
Suite evidence, candidate outputs, and factory logs are separate and retained
through human review. Fix attempts add growth beyond evals: a fresh clone of
the target repository, Runner, and Skills per attempt, plus that attempt's
per-run Docker image.

**Slimming.** On every tick, the factory removes the bulk that finished
attempts leave behind and that a later attempt rebuilds. It does not wait for
release or retention.

- **Fix, feature, and task claims.** Once no run of the claim is active, the
  factory removes:
  - `<root>/clones/<claim>/<N>` for every attempt whose run was reserved;
  - each attempt's `attempt-*/audit-*/snapshot/runner-source`.

  This applies even while the claim is blocked or in Review, because the next
  attempt or review round clones afresh.

  Before removing a clone, the factory copies the target clone's
  `validator_logs` and a `clone-state.patch` (status and uncommitted diff)
  into `attempt-<N+1>/`, where retention prunes them with the rest of the
  evidence.
- **Eval claims.** Once the claim is settled, cancelled, or superseded, the
  factory removes only `node_modules` from each repetition's
  `.runtime/candidate-worktree`, at the top level and one level down. The
  rest of the checkout and `.runtime/agent-runner-projects` stay until
  retention, because `run.sh --rescore-from` hashes the acceptance artifacts
  they hold, and the human-review command serves `dist`.

Slimming never follows a link out of the artifact or clone directory.
`claim.cleanup.slimmed` records the slimmed runs and any failures.

Once a reviewed card reaches Done, Factory releases its
recorded owned worktrees, clones, images, and credential copies. Cancelled and
superseded claims are released as soon as their runs stop and reporting is
delivered. Settled claims still outside Done are released after `[limits]
unreviewed_retention_days` (default 30) from their terminal transition.
If a settled claim reaches Done without a recorded Review observation, Factory
releases it on the first quiescent poll after recording the Done observation,
without posting a human-review expiry report.
Release also covers claims whose cards have left the Project. A merged PR with
an incomplete post-merge sync holds its files. Mirrors persist with history.
Factory removes only its recorded owned worktrees, clones, and images;
it never deletes candidate branches, PRs, mirrors, or shared checkouts, and it
only prunes evidence under the rule below.

**Evidence retention.** `[limits] evidence_retention_days` (default 3) starts
from the first durable Done observation for a settled claim. Leaving Done
resets that observation. For cancelled and superseded claims it starts from
the recorded terminal transition, even if the card never reaches Done.
Settled claims outside Done use `unreviewed_retention_days` from that
transition. A new run reopens release and retention. Once the applicable
period has elapsed, release has completed, and no run, reporting, or merged
PR sync is pending, the next tick prunes that
claim's evidence: logs, Runner and agent session state, and agent output
under each attempt's artifact directory (and, for a host attempt, its
recorded Runner session directory), plus an eval's remaining candidate checkout. It keeps the fix outcome or eval result
and provenance records, the attempt's issue input, and never touches
candidate branches, PRs, mirrors, SQLite history, or the operator's working
clones. Pruning is
retried on later polls if a removal fails; `claim.cleanup.retention` records
what was removed and any failures — inspect it with `status --all` or by
reading the claim's row in `state.sqlite3` directly. This check runs from the
store-driven terminal sweep on every tick, including off-board claims. Old
terminal claims receive a one-time conservative terminal time from their
last update. Old Done claims first record their Done observation after upgrade.

Finished Fly eval images are removed by digest only when the claim's own
`claim-` tag still resolves to that digest and no other tag shares it. A
registry error or ownership skip remains in `status` and is retried. Registry
cleanup is independent of Fly Machine disposal; `base`, `deployment-`, and
unrecorded tags are never deletion targets.

For a ready-for-human-review result, use the absolute, quoted command in the
Factory report on the Mac holding its retained artifacts and harness worktree.
That command is available until the item moves to Done or the unreviewed
period expires. Factory posts an expiry comment before releasing a published
command's worktree. Factory does
not run human ratings, assign an official pass, close the issue, merge a PR, or
claim that a static plist proves live launchd acceptance.

## Service-driven watch dispatch

The watcher makes sure the factory itself works. It does not review the code the factory builds. The resident runs the watch step once per cycle, including while admissions are paused or the main cycle fails, and dispatches one fresh headless session (the packaged `factory-watch` workflow, contract `factory-watch/3`, in a throwaway checkout of `[watch] repository`) for each of two events:

- `PR-READY`: a fix, feature, or task run completed with a pull request (initial, recovery, or review round). The session mines the PR description's red and orange attention items, and the run's evidence as needed, for defects in the factory stack: Agent Factory, the Runner workflows, Agent Skills, and Agent Validator as the factory uses it. For each one it searches open issues, adds evidence to a matching issue or files a Bug in the owning repository, and assigns new issues in `[fix] targets` repositories to the factory (Owner=factory, Status=Ready, Priority Low unless the defect blocks work). When `auto_merge` is on, it also rates risk from the diff and description. The session posts nothing on the pull request; the resident posts a risk verdict after its merge decision.
- `FAILURE`: an attempt stayed `failed`, `interrupted`, `cancelled`, or `timed_out`, or a fix, feature, or task attempt completed with outcome `failed`. In either case, its result was consumed and `grace_minutes` has passed. `needs-input` outcomes are not triaged. Triage runs after the claim's own automatic retry has had its chance and never holds that retry. The session diagnoses the cause, may pause or resume the factory for containment, and files or updates an issue for a factory defect. The factory posts its cause, evidence, owner, actions, issues, pause state, and next step as one factory-bot comment on the claim's issue. For a transient or environment cause it files no issue unless there is a real defect, and says what the operator must do.

Neither session fixes anything: no branches, commits, pushes, or pull requests. Neither deploys, merges, touches a release, the service clone, or the operator's checkout, or fetches into the factory's mirrors. Both follow the `factory-triage` skill ("Headless PR-READY check", "Headless triage").

Configure `[watch]` in shared TOML with `enabled`, `repository`, `agent`, optional `agents.PR-READY` and `agents.FAILURE` (for example, set `agents.FAILURE` to an Opus profile to escalate triage), `max_sessions` (default 2), `grace_minutes` (default 7), `timeout_minutes` (default 90), and `auto_merge` (default false). The profile syntax is `cli:model:effort`. At most `max_sessions` sessions run at once, and one at a time per pull request. Every eligible event can get a session. Older `budget-exhausted` dispatches remain visible and can be redispatched. A session that ends without a valid result is alerted on the claim's issue.

### Auto-merge

With `auto_merge = true`, each PR-READY session also rates the pull request `low`, `medium`, or `high`, and the resident merges `low` ones itself; the session never merges. The bars, in the `factory-triage` skill's "Headless PR-READY check":

- Any kind: a red item, or a change to auth or credentials, CI or deploy and release scripts, workflow definitions, database schema or migrations, pinned refs in committed configuration, a public CLI or API interface, or dependencies, prevents `low`.
- Fix: every change serves the reported defect, a test fails without the fix and passes with it, at most 300 changed non-test lines (added plus deleted, excluding generated lockfiles), and each orange item judged harmless.
- Task: every change serves the task, every behavior change is covered by an added or updated test (documentation-only changes need none), at most 300 changed non-test lines, and each orange item judged harmless.
- Feature: the specification delta only adds requirements and existing behavior is unchanged, at most 150 changed non-test lines, no orange items, and every added scenario tested.

The resident merges a `low` pull request with a merge commit pinned to the rated head when the factory is unpaused; the repository is a `[fix] targets` entry and the base is its branch; the URL matches the dispatch; the pull request is open, not a draft, and conflict-free; the head is unchanged; every reported check and status passed; every status check that the repository's GitHub rulesets require on the base branch reported success; no review thread is unresolved; and no writer's standing review (their latest approve, request-changes, or dismissed review; comment-only reviews do not count) requests changes. It waits up to 60 minutes for running checks, required checks to report, mergeability, a reviewer's permission lookup, or a pause to clear. After an uncertain merge response it reads GitHub on later cycles without sending another request, and ends not merged if the pull request is still open five minutes after the request.

A repository auto-merges only when a ruleset on its base branch requires status checks (Settings, Rules, Rulesets, "Require status checks to pass"); without one, every `low` pull request there ends "no required checks on main". The required checks gate human merges too. Branch protection that requires an approving review makes GitHub reject the merge; the rejection appears in the verdict. Agent Factory's own checks come from `.github/workflows/ci.yml`.

The factory bot posts one risk-verdict comment on the pull request with the rating, reasons, head, and either "merged automatically" or the gate that stopped it; `status` shows the same. To stop auto-merging, set `auto_merge = false` in committed configuration: sessions launched afterwards do not rate, and waiting merges end "auto-merge off".

## Factory job cap

`[job_cap]` in shared TOML limits attempts started across all work kinds. `attempts` defaults to 100 and `window_hours` to 24; both must be positive integers. Initial attempts, retries, recoveries, unblocks, review rounds, and eval repetitions count. Watch sessions and post-run work do not. The cap applies even when the section is absent.

When the rolling count reaches the cap, new work waits without consuming a retry or changing its claim lifecycle. A held claim receives one issue comment per cap episode. An eligible Ready card that has not been claimed receives one waiting comment per episode. `status` shows the count, earliest clear time, held claims, and waiting cards. Attempts leave the count when the window passes; `pause` and `resume` do not reset it. Run `agent-factory --config <local.toml> job-cap reset` to exclude earlier attempts and admit held work on the next cycle. Raising `[job_cap] attempts` requires a committed configuration change.

Doctor includes a `watch` group when enabled. It checks the Runner, `git`, `gh`, each profile's CLI, the packaged workflow's contract, the watch repository mirror, and the `gh` login sessions file issues with. That login must differ from the factory bot and have write access, because the factory admits only issues written by writers.

`status` shows whether auto-merge is on, each rated PR and its merge state, the last detection time, today's session count and known cost, running and pending work, ended dispatches, undelivered comments, audits, and the factory issues each check or triage filed or updated (`watch factory issues:`). Each dispatch's evidence directory, `<storage_root>/artifacts/watch/<dispatch>/`, holds its brief, `watch-result.json`, Runner log, and session; its usage is in the `watch_dispatch` row. Run `agent-factory --config <local.toml> watch redispatch <id>` for an ended check or triage that needs another attempt. To stop auto-merging, commit `auto_merge = false`; waiting merges end on the next cycle with a risk-verdict comment. To stop new detection and launches, set `enabled = false` through a committed configuration change. Existing sessions are still supervised and comments are still delivered. No interactive watcher session is needed: for an on-demand summary, use the `factory-status` skill; to investigate or fix a failure by hand, use `factory-triage`; to review a factory PR, use `factory-pr-review`.

A failed run can be missed when no cycle runs for seven days after its grace period. Timeouts are enforced at cycle granularity. A timeout can leave usage partial and audit missing. An unknown process identity keeps its concurrency slot until the probe resolves.

Eval revision inputs are declared in `src/agent_factory/suites/and_scene/inputs.py`. An ordinary input is an optional request-settable ref resolved in a local checkout and passed to the suite through argv. Inputs that need Fly guest cloning, claim-image changes, a new `[repositories]` key, or a rollback guard still require explicit code.

## Session notifications

`codagent-github-project` stamps the creating Claude Code session's UUID into the issue body. `factory-assign` replaces that marker when it hands the issue to the factory. Only one session is tracked per issue. The marker does not affect eval request parsing. A marked issue needs no separate `factory-watch` session when its recording session only needs to know when work stops; use `factory-watch` for unmarked issues or when following from another session.

The resident checks finished claims each cycle, including while paused. It reports pull requests, `needs-input`, failures, settled evals, cancellations, and cards that are no longer queued. A queued card or pending review round still counts as progressing. By default, a stop must remain stable for 360 seconds. For runs the service watcher handles, notification waits for its dispatch to end or for the 25-minute watch window to pass. The message only notifies: it contains the issue and claim, the stop kind in plain language, issue and pull request links when available, and a `Details:` link only for the claim's own recorded explanation comment. It contains no issue title or body.

The optional shared `[notify]` section has `enabled = false` by default and requires an `agent` profile in `claude:model:effort` form when enabled. `settle_seconds = 360`, `watch_wait_minutes = 25`, `daily_sessions = 30`, and `timeout_minutes = 5` are defaults; the first three accept zero and the timeout must be at least one minute. The checked-in shared configuration enables `claude:claude-haiku-4-5-20251001:low`. The daily cap counts launched notifier sessions. Turning notifications off drops unsettled stops and the enablement cursor, while already launched sessions remain supervised. Recent ended records and their evidence are pruned after at least eight days.

`doctor` adds a `notify` group when enabled. It checks `claude`, `ps`, Claude authentication and needed CLI flags, and the readable `~/.claude/sessions` registry; an empty registry is informational. `status` shows enabled state, running sessions, today's launch count and known cost, and the last 24 hours' visible outcomes (`sent`, `no-session`, `failed`, or `budget-exhausted`). `unmarked` stops are stored but hidden. A failed readiness check blocks only notifier launch, and the stop records `failed`.

Delivery depends on Claude Code's local, undocumented `~/.claude/sessions/<pid>.json` layout and cross-session messaging. The registry's adjacent `.key` files are never read. An ended, renamed-and-reused, or unresolvable session can receive nothing; the seconds-long name reuse race remains even though the sender resolves twice and confirms one matching name through `ListAgents`. A receiver in another permission mode may hold the message. The notifier uses the Claude CLI directly with only `ListAgents` and `SendMessage`, a minimal environment, and no GitHub token. Its result reflects the sender's `SendMessage` call, not whether the recipient consumed a held message.
