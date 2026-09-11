# Install Agent Factory on macOS

Agent Factory is a per-user Mac service. It supports one local SQLite-backed
worker, GitHub Projects, the `eval` work kind, and the `and-scene` suite. A
different organization may replace identities, mappings, paths, and credentials
in configuration, but configuration alone does not add another work kind or
suite.

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

## Configuration

Copy `config/local.example.toml` to a private location, such as
`/Users/you/.agent-factory/config.toml`, and set these explicit paths:

- `shared_config`: the versioned Factory checkout's `config/codagent.toml` (or
  your organization's equivalent).
- `storage_root`: normally `~/.agent-factory`; it contains `state.sqlite3`,
  `logs/`, factory-owned worktrees, and artifacts.
- the three source checkouts, the App key file, and the separately managed suite
  environment file.

The shared TOML is versioned deployment data: organization/repositories,
Project destination and logical field mappings, routing, defaults, and the full
harness SHA. It must not contain local paths or secrets. The harness value is a
full commit SHA, never `HEAD` or a branch. The local TOML carries paths,
schedule, limits, and credentials. No command fetches either configuration at
runtime.

For the supported Codagent deployment, reuse the recorded App, Project #1,
native Eval type, board, and mappings in `config/codagent.toml`; do not
reprovision them. Before accepting work, confirm the selected full harness pin
has the separately delivered score-failure contract and calibration-gate
removal. Factory verifies the selected suite's local readiness; it does not
implement scoring policy or silently substitute a checkout's current revision.

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

For rollback, pause admission and restore compatible Factory and workflow pins.
Retain SQLite, credentials, evidence, frozen worktrees, and active supervisor
environments. Do not run an older executable against a newer schema. A migration
requiring exclusive access waits for active writers; there is no automatic
configuration fetch, harness update, destructive rollback, or evidence pruning.
