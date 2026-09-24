# Agent Factory: notes for coding agents

## The live service on Paul's Mac

- The LaunchAgent `com.codagent.agent-factory`
  (`~/Library/LaunchAgents/com.codagent.agent-factory.plist`) runs `resident`
  from the editable venv in the service clone,
  `~/.agent-factory/agent-factory`. `~/.agent-factory/config.toml` points
  `shared_config` at the clone's `config/codagent.toml`, which is reloaded
  every tick.
- The clone is kept detached at the deployed ref (normally `origin/main`).
  Change it only with `scripts/deploy.sh`: because the venv is editable, any
  switch or edit in the clone reaches newly spawned processes without a
  restart. Never edit it by hand.
- Paul's checkout (`/Users/paul/codagent/agent-factory`) is not live. It holds
  his in-progress branches and is the post-merge sync's working clone for this
  repository. Do fix work in a separate worktree.
- The `agent-factory.fly` and `agent-factory.run-locally` worktrees are retired.
  Never use them.
- Always compare against `origin/main` rather than local `main`, which falls
  behind.

## Deploying

Run `scripts/deploy.sh` from any checkout of this repository (optionally with a
ref; the default is `origin/main`). It refuses while the eval or fix slot is
busy, then:

1. pauses the factory, detaches the service clone at the ref (cloning it first
   if missing), and runs `uv sync --frozen`;
2. points the plist's executable and `PATH`, and `shared_config`, at the clone;
3. runs `doctor`, and stops with the factory paused if it fails;
4. runs `launchctl bootout`, waits until the service is gone, and runs
   `launchctl bootstrap` (`launchctl kickstart -k` does not re-read a changed
   plist);
5. resumes the factory unless it was already paused before the deploy, and
   runs one `tick`.

Apply any `packaging/launchd/` template change beyond the executable and `PATH`
by hand before deploying. Supervisors and Fly launchers run in their own
process groups and survive a restart. To run `tick` by hand from a shell, put
`~/.agent-factory/agent-factory/.venv/bin` first on `PATH`. `controller.log` is
stale because `resident` does not write to it.

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
