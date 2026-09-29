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
`origin/main`). You can deploy, or restart the factory, at any time, including
while fixes, features, and evals run. There is no need to wait for a slot to
free up or to pause first. Running jobs keep their release and the Agent
Runner binary they started with, and supervisors and Fly launchers survive the
resident's restart. The script:

1. fast-forwards the Agent Runner checkout (`[repositories] agent_runner`, on
   `main`) to `origin/main` (`scripts/update-runner.sh`). It never pushes. It
   skips the runner step with a warning if the checkout is not on `main`, has
   uncommitted changes, or has commits not on `origin/main`;
2. builds the release for the ref (a worktree plus `uv sync --frozen`), unless
   it already exists;
3. pauses the factory and runs `make build` in the runner checkout, which
   updates the host runner that fix and feature runs use. `go build` renames
   the new binary over the old one, so a running attempt keeps its binary and
   later launches use the new one. Unless `--no-validator` is set, deploy also
   fast-forwards and builds the sibling Agent Validator checkout from
   `origin/main` when no host attempt is running;
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
   only while every slot is free, never the live one, and never one a process
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
  `agent_runner_ref` (`main`). Fly evals also pin Agent Validator
  `agent_validator_ref` (`main`) in the claim image; Docker evals still use the
  published npm release. Validator changes can change eval results, so compare
  results with their recorded Validator revisions. Fixes clone Agent Runner and Skills from
  `[fix.branches]` (`main`), but run the `agent-runner` installed on `PATH`,
  which `make build` last built from the Agent Runner checkout.
  `scripts/deploy.sh` keeps that checkout on `origin/main` and rebuilds it on
  every deploy.
- `[repositories] agent_validator` defaults to a sibling of `agent_runner`.
  Host fixes and features use the checkout's local build through a one-time
  `agent-validator` link on the LaunchAgent PATH. Deploy skips that build while
  a host attempt is running; `--no-validator` skips the step explicitly.
- Role models are `[eval.defaults]` and `[fix.defaults]` in
  `config/codagent.toml`. Each claim freezes its revisions and roles at
  admission, so later edits affect only new claims.
  Every feature resume merges the current target branch while its admission
  target, Runner, and Skills revisions stay frozen. A conflict that cannot be
  resolved stops with `needs-input` and names the files; answer in a writer
  comment after fixing the target branch or committing to the claim branch.
  Rolling back to a release without first-parent checkpoint reading may choose
  a checkpoint from merged target history; inspect recently resumed claims and
  re-admit affected ones before rollback.
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
- A finished eval claim's own `claim-` manifest is deleted by digest once the
  claim uniquely owns it and no Machine holds it. Skips and errors show in
  `status`.

## Disk space

Admission stops below `minimum_free_gib` (5 GiB). Space goes mainly to
`~/.agent-factory/artifacts` and `clones`, and to Docker Desktop's disk image.
The terminal sweep releases cancelled and superseded claims as soon as they are
quiescent and prunes their evidence after `evidence_retention_days`. Settled
claims outside Done are released and pruned after `unreviewed_retention_days`.
Done claims are cleaned after the Done observation and pruned after
`evidence_retention_days` from that observation, including settled claims that
reached Done without a recorded Review observation.

## Shell on this Mac

- There is no `timeout` command.
- The shell is zsh: `set -- $var` does not split words. Pass arguments
  explicitly or use arrays.

See `docs/operations.md` for model authentication, Fly Machines, and storage.

## Service-driven watcher

The resident detects factory events and dispatches headless PR reviews and failure triage when `[watch] enabled` is true in shared configuration. Check `agent-factory --config <local.toml> doctor` for the `watch` group and `status` for its cursor, budget, sessions, costs, comments, and audit. The operator's `gh` login must be a writer with push permission and must differ from the factory bot. To retry an ended review or triage, use `agent-factory --config <local.toml> watch redispatch <id>`. Disable watching through committed configuration; running sessions and comment delivery continue.

The service watcher has been live since 2026-09-29. Never run the interactive
`factory-watch` `watch.sh` loop while it is enabled: both would dispatch every
event, so PRs would get duplicate reviews. A `factory-watch` session monitors
the service's watch status and sessions and handles the decisions they surface.
Run `watch.sh` only when `[watch] enabled` is false.
