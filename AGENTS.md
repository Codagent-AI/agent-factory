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

## Code and models each kind of work uses

- Evals use Agent Evals `harness_ref` (`main`) and Agent Runner
  `agent_runner_ref` (`dev`). Fixes use Agent Runner and Skills from
  `[fix.branches]` (`main`). An Agent Runner change that evals need, such as a
  Fly Dockerfile fix, must reach `dev`, not only `main`.
- Role models are `[eval.defaults]` and `[fix.defaults]` in
  `config/codagent.toml`. Each claim freezes its revisions and roles at
  admission, so later edits affect only new claims.
- The eval judge model is not set here. Agent Evals uses the Codex CLI default
  (`codex-default`), so it changes with the CLI version.

## Fly eval images

- Each eval claim builds its own image on Fly's remote builder from its pinned
  Agent Runner worktree (`docker/dev/Dockerfile`), tagged
  `claim-<first 12 claim id characters>` and pinned by digest. The Dockerfile
  needs the `FACTORY_CLI_REFRESH` build argument so the model CLIs are
  reinstalled for each claim.
- The registry can take about a minute to serve a just-pushed image; Fly then
  answers a Machine create with HTTP 400 `failed to get manifest`.
- Old `claim-` tags are not removed yet (see
  https://github.com/Codagent-AI/agent-factory/issues/15).

## Disk space

Admission stops below `minimum_free_gib` (5 GiB). Space goes mainly to
`~/.agent-factory/artifacts` and `clones`, which are cleaned only after a card
reaches Done, and to Docker Desktop's disk image. Automated cleanup is tracked
in https://github.com/Codagent-AI/agent-factory/issues/15.

## Shell on this Mac

- There is no `timeout` command.
- The shell is zsh: `set -- $var` does not split words. Pass arguments
  explicitly or use arrays.

See `docs/operations.md` for model authentication, Fly Machines, and storage.
