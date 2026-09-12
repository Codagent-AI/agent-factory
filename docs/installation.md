# Install Agent Factory on macOS

Agent Factory is a per-user Mac service. It supports one local SQLite-backed
worker, GitHub Projects, the `eval` and `fix` work kinds, and the `and-scene`
suite. Each work kind admits and runs independently, in its own slot, subject
to its own window and readiness. A different organization may replace
identities, mappings, paths, and credentials in configuration, but
configuration alone does not add another work kind or suite.

## Prerequisites

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then install a
released, pinned Factory version into a retained environment. For example:

```sh
uv tool install 'agent-factory==<released-version>'
```

Keep that environment in place while any attempt started by it is active. Clone
the configured `agent-evals`, Agent Runner, and Agent Skills source repositories
on the Mac. Start Docker Desktop, authenticate the selected model CLI, and
prevent the Mac from idle-sleeping while unattended work is expected (for
example, use a managed power policy or `caffeinate` under operator control).
Complete the selected suite's documented browser-proof prerequisite before
admission; a login LaunchAgent has no interactive browser session to repair it.

The Factory App key, board/routing credentials, and the suite candidate/draft-PR
token are separate authorities. Put the App key and suite environment file in
private files outside this repository; make the App key owner-readable only.
Never put an App key in the suite environment file. Configure the GitHub App
with the Project and repository permissions documented in
[GitHub setup](github-setup.md), and configure the corresponding Actions
secrets there.

### Fix-kind prerequisites

The `fix` work kind keeps a bare mirror of each configured target repository
under the storage root (`<storage_root>/mirrors/<owner>__<repo>.git`), created
with `git clone --mirror` on first admission and fetched with the App
installation token before every claim. Each attempt then runs in fresh clones
of the target (from the mirror) and of Agent Runner and Agent Skills (from the
configured shared checkouts) at the claim's recorded commits, under
`<storage_root>/clones/<claim>/<attempt>/{repo,runner,skills}`; clones are
never reused between attempts and are removed once the card reaches Done. It also needs an operator-maintained working clone of each
target repository outside the storage root — the merge sync fast-forwards that
clone after a fix PR merges, and the factory never creates it. Configure both
locations under `[repositories.working_clones]` in the local TOML, keyed by
`owner/repo`.

Create a private fix credential file containing exactly one line,
`GH_TOKEN=<token>`, owner-readable only, distinct from the App installation
token and from the suite candidate token. Point `credentials.fix_environment`
at it in the local TOML. This credential opens fix pull requests; it must have
Contents, Pull requests, and Issues access to the target repositories only —
no workflow, administration, or Project access — and is preferably a
non-admin machine user, since `doctor` warns (without failing) when it detects
organization-admin access.

The fix workflow runs in the same Docker sandbox as evals, so plan capacity for
both to run concurrently: free disk for two sets of clones and per-run images,
plus enough Docker memory allowance for one eval and one fix attempt at once
(`limits.memory_reservation_gib` in the local TOML gates admission on this;
raise it if you increase Docker's memory allocation). The companion fix
workflow lives in Agent Runner at `workflows/core/factory-fix-v1.0.yaml` on the
configured Runner branch and declares its contract version
(`# factory-contract: factory-fix/1`) on its first line; `doctor` checks that
line so an incompatible Runner revision is caught before a fix is admitted, not
after.

## Configuration

Copy `config/local.example.toml` to a private location, such as
`/Users/you/.agent-factory/config.toml`, and set these explicit paths:

- `shared_config`: the versioned Factory checkout's `config/codagent.toml` (or
  your organization's equivalent).
- `storage_root`: normally `~/.agent-factory`; it contains `state.sqlite3`,
  `logs/`, factory-owned worktrees and clones, mirrors, and artifacts.
- the three source checkouts, the App key file, and the separately managed suite
  environment file.
- `[repositories.working_clones]`, one entry per fix target keyed by
  `owner/repo`, pointing at the operator's own clone used by the merge sync.
- `credentials.fix_environment`, and `[fix]` settings for limits and the fix
  admission window (see the versioned `[fix]` table in the shared TOML for
  targets, branches, and role defaults).

The shared TOML is versioned deployment data: organization/repositories,
Project destination and logical field mappings, routing, defaults, and the
`agent-evals` harness branch. It must not contain local paths or secrets. The
harness value is a branch name (`eval.harness_ref`, default `main`), never a
commit SHA; Factory resolves it to a commit at each claim's admission and
records that commit on the claim, so the recorded commit — not the branch
name — is the comparability key across nights. The local TOML carries paths,
schedule, limits, and credentials. No command fetches either configuration at
runtime; only claim admission fetches the source repositories.

For the supported Codagent deployment, reuse the recorded App, Project #1,
native Eval type, board, and mappings in `config/codagent.toml`; do not
reprovision them. `doctor` reports the commit the configured harness branch
currently resolves to, but it does not fetch and it does not prove that
revision carries the suite behavior Factory depends on (see
[suite integration](suite-integration.md)); confirm that separately before
unpausing admission. Factory verifies the selected suite's local readiness; it
does not implement scoring policy or silently substitute a checkout's current
revision.

## Install the LaunchAgent

Create the root and log directory, then render
`packaging/launchd/com.codagent.agent-factory.plist` with real absolute paths.
The template tokens map as follows:

- `__EXECUTABLE__`: `/absolute/path/to/.venv/bin/agent-factory`
- `__CONFIG__`: `/absolute/path/to/config.toml`
- `__ROOT__`: `/absolute/path/to/.agent-factory`
- `__LOG__`: `/absolute/path/to/.agent-factory/logs/controller.log`
- `__CREDENTIAL__`: `/absolute/path/to/credentials/github-app.pem`

Keep those paths explicit: launchd does not inherit an interactive shell's PATH,
working directory, or credentials. Validate the rendered file and load it for
the logged-in user:

```sh
plutil -lint ~/Library/LaunchAgents/com.codagent.agent-factory.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codagent.agent-factory.plist
launchctl kickstart -k gui/$(id -u)/com.codagent.agent-factory
```

`RunAtLoad` starts it at login and `KeepAlive` restarts a crashed controller.
Only the controller is a LaunchAgent. Each accepted repetition has its own
independent supervisor/session and file-backed logs, so restarting the
controller does not stop an active suite process or replace its immutable Python
environment. The resident controller polls every five minutes while supervisors
observe attempts independently.

## Deploy, update, and roll back

Pause admission before installation or an update. Publish shared routing code at
the target pinned revision before updating callers on their default branches;
deploy labels, templates, and callers through the normal repository process.
Then update the local installed Factory and its shared TOML to that same intended
revision, run `doctor`, and resume only after the selected suite is ready.

For rollback, pause admission, reinstall the previous released Factory tag, and
restore the shared TOML's previous pin (`eval.harness_sha` on versions that
predate `eval.harness_ref`). Retain SQLite, credentials, evidence, frozen
worktrees, and active supervisor environments. Do not run an older executable
against a newer schema. If the schema migration itself must be undone, copy the
pre-migration `state.sqlite3.v3.bak` backup back over `state.sqlite3` while
paused; any claim created after the upgrade is lost by that copy, so prefer it
only when the upgrade itself is the problem. A migration requiring exclusive
access waits for active writers; there is no automatic configuration fetch,
harness update, destructive rollback, or evidence pruning.
