# Agent Factory: notes for coding agents

## The live service on Paul's Mac

- The LaunchAgent `com.codagent.agent-factory`
  (`~/Library/LaunchAgents/com.codagent.agent-factory.plist`) runs `resident`
  from the editable venv in the `/Users/paul/codagent/agent-factory.fly`
  worktree. `~/.agent-factory/config.toml` points `shared_config` at that
  worktree's `config/codagent.toml`, which is reloaded every tick.
- There is no build or deploy step: the service runs whatever source is checked
  out in that worktree. Source edits go live for newly spawned processes, so do
  not edit or switch that worktree while a run is in progress.
- The worktree is kept detached at `origin/main`. Paul's main checkout
  (`/Users/paul/codagent/agent-factory`) holds in-progress work on other
  branches; do not point the service at it unless Paul asks.
- The `agent-factory.run-locally` worktree is retired. Never use it.
- Always compare against `origin/main` rather than local `main`, which falls
  behind.

## Deploying new `main`

1. `git fetch origin && git switch --detach origin/main` in
   `agent-factory.fly`. Reinstall the venv if dependencies changed.
2. `agent-factory --config ~/.agent-factory/config.toml pause`, then `doctor`.
3. `launchctl bootout gui/$(id -u)/com.codagent.agent-factory`, and wait until
   `launchctl print gui/$(id -u)/com.codagent.agent-factory` fails.
4. `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codagent.agent-factory.plist`.
   `launchctl kickstart -k` does not re-read a changed plist.
5. `resume`, then one `tick`.

Supervisors and Fly launchers run in their own process groups and survive a
restart. To run `tick` by hand from a shell, put the worktree's `.venv/bin`
first on `PATH`. `controller.log` is stale because `resident` does not write
to it.

## Configuration pins

Do not leave uncommitted pins in `config/codagent.toml` (for example
`harness_ref = "dev"`). The service would use them, and they fail the
validator's end-to-end tests. Commit any pin through a PR.

See `docs/operations.md` for model authentication, Fly Machines, and storage.
