# Agent Factory: notes for coding agents

## The live service on Paul's Mac

- The LaunchAgent `com.codagent.agent-factory`
  (`~/Library/LaunchAgents/com.codagent.agent-factory.plist`) runs `resident`
  from a release: an immutable, detached worktree of the service clone
  (`~/.agent-factory/agent-factory`) at `~/.agent-factory/releases/<commit>`,
  with its own venv. `releases/current` links to the newest release.
  `~/.agent-factory/config.toml` points `shared_config` at that release's
  `config/codagent.toml`, which is reloaded every tick.
- Releases are created and removed only by `scripts/deploy.sh`. Never edit a
  release, and never switch or edit the service clone by hand. A process keeps
  the release it started from, so a running job is not affected by a deploy.
- Paul's checkout (`/Users/paul/codagent/agent-factory`) is not live. It holds
  his in-progress branches and is the post-merge sync's working clone for this
  repository. Do fix work in a separate worktree.
- The `agent-factory.fly` and `agent-factory.run-locally` worktrees are retired.
  Never use them.
- Always compare against `origin/main` rather than local `main`, which falls
  behind.

## Deploying

Use the `factory-deploy` skill, or run `scripts/deploy.sh` from any checkout of
this repository (optionally `--no-runner`, or a factory ref; the default is
`origin/main`). Deploying while jobs run is safe: running jobs keep their
release, and supervisors and Fly launchers survive the resident's restart.
The script:

1. brings the Agent Runner checkout (`[repositories] agent_runner`, on `dev`)
   up to `origin/dev`, merges `origin/main` into `dev` (fast-forward or clean
   merge), and pushes it, because evals build from `dev`. It skips the merge
   with a warning if it would conflict. It skips the whole runner step with a
   warning if the checkout is not on `dev`, has uncommitted changes, or a fix
   is running (the host runner is still rebuilt in place; see #23);
2. builds the release for the ref (a worktree plus `uv sync --frozen`), unless
   it already exists;
3. pauses the factory and runs `make build` in the runner checkout, which
   updates the host runner fix runs use;
4. points the plist's executable and `PATH`, and `shared_config`, at the
   release;
5. runs `doctor`. If it fails, it points them back at the previous release and
   stops with the factory paused;
6. runs `launchctl bootout`, waits until the service is gone, and runs
   `launchctl bootstrap` (`launchctl kickstart -k` does not re-read a changed
   plist), confirms the resident is running, and moves `releases/current`;
7. resumes the factory unless it was already paused before the deploy, and
   runs one `tick`;
8. removes releases beyond the newest two (`AGENT_FACTORY_KEEP_RELEASES`), but
   only while both slots are free, never the live one, and never one a process
   still uses.

Agent Evals needs no deploy: each eval admission fetches `harness_ref`.

Apply any `packaging/launchd/` template change beyond the executable and `PATH`
by hand before deploying. To run `tick` by hand from a shell, put
`~/.agent-factory/releases/current/.venv/bin` first on `PATH`.
`controller.log` is stale because `resident` does not write to it.

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
