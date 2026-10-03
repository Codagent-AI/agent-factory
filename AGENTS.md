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

`fixture_ref` in an eval request selects an and-scene branch, tag, or commit.
Without it, the frozen agent-evals harness pin remains the fixture. Admission
uses `[repositories] and_scene` (default: the `and-scene` sibling of
`agent_runner`), requires the commit to be published on the and-scene origin,
and freezes its SHA. Push a fixture branch before requesting it. Deleting its
only branch before the claim finishes can fail later repetitions at fixture
checkout. The frozen-inputs comment and `Refs` field show the selected fixture;
results from a non-default fixture are not comparable with default-pin results.

The deploy script refuses rollback past fixture support while any unfinished
claim has a frozen fixture revision. Check them with `agent-factory --config
<local.toml> pinned-claims --revision fixture`; pause, let each claim settle or
cancel it, then deploy the older release. That release cannot accept
`fixture_ref`. Stay on a fixture-capable release if the pinned evaluation is
still needed; a new request without the key evaluates only the default fixture.
A hand rollback or an older deploy script bypasses this guard.

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
A deploy is needed only for Agent Factory changes. Changes merged to `main` of
Agent Runner, Agent Validator, Agent Skills, or Agent Evals reach the next eval
admission with no deploy and no pin change: admission resolves each ref to a
commit and freezes it, and the claim's `frozen-inputs` comment lists them. A
claim already admitted keeps its frozen revisions. The live service runs evals
on Fly (`execution` in the local config). The Validator reviewer an eval uses
comes from the and-scene fixture's `.validator/config.yml`, which Agent Evals
pins (see its `AGENTS.md`), not from this repository.

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

## Task pull requests

File low-risk maintenance as a native Task in a configured `[fix]` target. A Task
reaches the factory only when a writer moves its card to Ready (or uses the
`factory-assign` skill); routing does not queue it automatically. Shared
`[task]` enables new admissions, sets `contract = "factory-task/1"`, and provides
lead, implementor, and tester profiles. Local `[task]` is optional: host-only
execution, an always-open schedule by default, an optional disk floor, and
inactivity/execution/total limits of 900/7200/10800 seconds. `doctor` reports
`task-host`, while `status` reports the task slot and task claims.

Task triage declines runtime behavior or public interface changes, persisted
data and OpenSpec specification changes, credentials, release/deploy settings,
branch protection, work outside the target, oversized work, or any product,
design, compatibility, or other decision the issue leaves open. Development tools, development
dependencies, CI, docs, behavior-preserving refactors and cleanups are in scope;
release, publish, version, sign, tag, and deploy configuration is not. Decline
any decision the issue leaves open with `needs-input`, naming that decision.
Task commits and PR
titles use `chore:`. Task review rounds stop with `needs-input` when requested
changes cross the same boundary.

Before enabling `[task]` on a live release, audit all Ready Task cards across
every fix target. Move any that are not approved for factory admission to
Backlog. Before rolling back to a release without the task
kind, settle or cancel open task claims; that release cannot handle them.

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

The watcher's only job is to make sure the factory itself works; it does not review the code the factory builds. When `[watch] enabled` is true in shared configuration, the resident dispatches a fresh headless session for two events. On `PR-READY` (a fix, feature, or task run opened or updated a pull request) the session mines the PR description's red and orange items, and the run's evidence as needed, for defects in the factory stack, and files or updates a Bug issue assigned to the factory for each one. It posts nothing on the PR. On `FAILURE` (a failed attempt, including a fix, feature, or task run that completed with outcome `failed`), the session diagnoses the run, may pause or resume the factory for containment, files or updates an issue for a factory defect, and its result is posted on the claim's issue. `needs-input` outcomes are not triaged. Neither session fixes anything: no branches, commits, pushes, or PRs. Both follow `factory-triage` ("Headless PR-READY check", "Headless triage"). Check `agent-factory --config <local.toml> doctor` for the `watch` group and `status` for its cursor, sessions, costs, comments, filed issues, and audit. No watch event is skipped for volume; `[job_cap]` bounds factory attempts instead. The operator's `gh` login files the issues, so it must have write access (the factory admits only writers' issues) and must differ from the factory bot. To retry an ended check or triage, use `agent-factory --config <local.toml> watch redispatch <id>`. Disable watching through committed configuration; running sessions and comment delivery continue.

Do not start a general long-running watcher, `/loop`, or polling session: it
duplicates the service's work and costs a session per poll. To follow specific
issues until the factory stops progressing on them, use the `factory-watch`
skill, which polls with a script and reports once. For an update on demand, use
the `factory-status` skill. To investigate or fix a failure by hand, use
`factory-triage`. To review a factory PR when Paul asks, use `factory-pr-review`.
