---
name: factory-watch
description: Watch the live Agent Factory on Paul's Mac for technical failures, new claims, and finished evals; triage each event, and fix genuine factory defects through a tested PR that is deployed only after Paul merges it. Use when asked to watch, monitor, or babysit the factory or a running eval, or to handle a factory failure.
---

# Factory watch

Watch the live factory service, wake on anything that needs a look, triage it, fix what is broken, then keep watching. Read `AGENTS.md` first: it describes the service clone, the deploy script, and configuration pins.

## Standing rules

- Never print or log tokens, credential files, or anything under `~/.agent-factory/private/`. Filter log output before showing it.
- Paul merges PRs. Never merge one yourself.
- The service runs a release under `~/.agent-factory/releases/` (see `AGENTS.md`). Never edit a release or the service clone `~/.agent-factory/agent-factory`; only `scripts/deploy.sh` changes them (see Deploy). Never switch branches in Paul's checkout, `/Users/paul/codagent/agent-factory`. Do all fix work in a separate worktree.
- Never run `agent-validator clean` to get around the validator's retry limit. Ask Paul instead.
- Never use bare `git stash`. Use a temporary WIP commit, or a stash with a unique tag that you apply by SHA.
- Every Fly Machine the factory or you created must end up destroyed. Check `fly machines list -a agent-factory-sandbox` after Fly work.
- Report solutions, not just problems: say what is wrong, what you did, and what Paul must decide.

## Start the watcher

Run it in the background and wait for it to exit:

```sh
.claude/skills/factory-watch/watch.sh               # all events after now
.claude/skills/factory-watch/watch.sh --no-claims   # ignore new admissions
```

Options: `--since <ISO8601 UTC>` starts from an earlier time. `--grace-minutes N` (default 7) and `--interval SECONDS` (default 90) tune it. It reads `$AGENT_FACTORY_ROOT/state.sqlite3` (default `~/.agent-factory`) read-only.

It exits after printing one or more events and a final `next: --since <time>` line. Handle the events, then restart it with exactly that `--since` value. Each event falls in one check window, so none is repeated or missed. To run it on a schedule, use `/loop` with this skill and carry the `--since` value between runs.

## Events

- **`CLAIM`**: the factory admitted work.
  - It is expected when someone moved a card to Ready, or a Bug was routed to Ready with Owner=factory.
  - Fix claims are admitted by Priority, then newest created.
  - A settled fix claim starts fresh when its card is moved back to Ready.
  - Confirm the cause from the issue timeline and the board fields. Human card moves do not appear in the issue timeline.
  - Report only a pickup nobody could have caused.
- **`EVAL-DONE`**: an eval run finished.
  - Wait one tick for the factory to consume it.
  - Then report: the verdict and gates, the automated score, cost and duration, the candidate PR, the results commit in `agent-evals`, and the human-review command.
  - Also check that the provenance recorded the image digest and the `claude`/`codex` versions, and that no Fly Machine is left.
- **`FAILURE`**: a run is still failed, interrupted, cancelled, or timed out after the grace period. Follow "Handling a failure" below.

If the user says they review bug cards themselves, do not summarize `needs-input` or `pull-request` outcomes. Report only factory failures and eval results.

## Known non-failures

Rule these out before calling something a factory defect:

- **Tick timing.** The resident ticks every `[schedule] poll_minutes` (5 minutes). Results are consumed at the next tick, not immediately.
  - Do not diagnose a stall until more than one full tick has passed.
  - To check liveness, look at the latest `updated_at` in the `settings` table.
  - `sample <resident pid> 2` shows `time.sleep` when it is idle between ticks.
  - To act sooner, run one manual `tick`.
- **Host fix runs pass through `interrupted`.** A host fix run shows `interrupted` ("owned process exited without durable result") until the next tick reads `attempt-N/fix-outcome.json` and marks it `completed`.
- **First Fly launch after an image build.** The first launch can get HTTP 400 `failed to get manifest … MANIFEST_UNKNOWN` because the image was pushed seconds earlier. #19 retries it; without #19, the pre-suite relaunch handles it.
- **Product results.** A fix outcome of `needs-input`, `pull-request`, or `failed` is a product result, not a factory failure.

## Handling a failure

### 1. Contain

If the claim still has an automatic retry (recovery or pre-suite relaunch) and the cause may still be present, pause the factory at once so the retry is not wasted:

```sh
~/.agent-factory/releases/current/.venv/bin/agent-factory --config ~/.agent-factory/config.toml pause
```

Pausing stops new admissions and launches. Running supervisors and Fly launchers continue. Resume as soon as the cause is gone.

### 2. Diagnose

1. Read the run row and its claim from the database the watcher reads: `$AGENT_FACTORY_ROOT/state.sqlite3` (default `~/.agent-factory/state.sqlite3`).
   - Run: `status`, `reason`, `attempt_number`, `result_json`, `evidence_path`, `plan_json`.
   - Claim: `lifecycle`, `outcome_json`, `frozen_spec_json`.
   - `agent-factory … status` shows the live view.
2. Read the artifacts under `evidence_path`:
   - Fix: `attempt-N/fix-outcome.json`, `logs/agent-runner.log`, `agent-runner-session/audit.log`.
   - Eval: `.factory/launch-stage.json`, `.factory/launcher.log`, `.factory/image-build.log`, `.factory/image-build.json`, `factory-suite.log`, `result.json`.
3. Read the code path that produced the status: `supervisor.py` for run status, `work_kinds/*/handler.py` for results and gestures, `fly/*` for Fly.
4. Reproduce when you can, not just by reading code.
   - Example: for an opaque Fly API error, send the same request yourself with the deploy token to see Fly's message.
   - Use `skip_launch: true` for Machine creates, and destroy anything you create.
5. State the cause with its evidence before changing anything.

### 3. Unblock the live work if the cause is gone

When the cause was transient (registry propagation, a network blip), `resume` and let the factory's own retry run on unchanged code. Confirm it succeeds. Still fix the underlying defect (step 5) so the next run does not spend its retry on it.

### 4. Decide who owns it

- **Factory code**: fix it yourself (steps 5–7).
- **Agent Runner, Agent Evals, or Skills**: send the evidence (file, line, log excerpt, proposed fix) to the owning Claude session, using `ListAgents` and `SendMessage`. If no session is reachable, write a handoff file and tell Paul. Do not change another repository's `main` or `dev` without Paul.
- **Environment** (disk floor, credentials, Fly app, image, LaunchAgent):
  - Apply the concrete fix yourself only when it is reversible or regenerates itself, for example npm or Go build caches, or your own scratch worktrees.
  - Deleting other data, Keychain items, remote branches, Docker volumes, or anything shared needs Paul's approval first.
  - Treat a temporary environment fix as temporary: note it and replace it with a proper fix.

### 5. Fix in a separate worktree, test first

```sh
cd /Users/paul/codagent/agent-factory
git fetch origin
git worktree add -b fix/<short-name> ../agent-factory.<short-name> origin/main
```

Work only in that worktree. Use the `codagent:implement-with-tdd` skill: write the failing test first, and watch it fail for the right reason. Test through the real boundary where practical; `tests/fixtures/fly/api.py` fakes the Fly API. Run `uv run pytest`.

### 6. Validate

Run `agent-validate run` in the worktree.

- Fix review findings that are correct.
- To skip a finding, set its `status` to `skipped` and put a concrete reason in `result` in the `validator_logs/review_*.json` file, then rerun.
- If a finding keeps coming back, check whether the reviewer is right. Gather evidence (for example, capture the real error text) rather than skipping again.
- Stop and ask Paul if the retry limit is reached.

Before committing, run `git status`: the validator can unstage files.

### 7. Open a PR, and do not deploy it

Commit with a message explaining the cause and the fix. Push the branch and open a PR whose body covers:

- the problem and the evidence;
- the change;
- review findings that were fixed or skipped, with reasons;
- testing.

Report only test counts you actually saw. Paul merges.

### 8. Deploy after Paul merges

1. Confirm the PR is in `origin/main`.
2. Deploying while jobs run is safe: each job keeps the release it started from. If a fix is running, the script skips the Agent Runner rebuild, so rerun it once the fix slot is free when the runner changed.
3. Use the `factory-deploy` skill (`scripts/deploy.sh`; see "Deploying" in `AGENTS.md`). It updates and builds Agent Runner `dev`, builds a release at `origin/main`, pauses, runs `doctor`, reloads the LaunchAgent on the release, restores the prior pause state, ticks, and removes old releases.
4. Check whether the PR changed the LaunchAgent template (`packaging/launchd/`) or local configuration; apply those too. The script handles dependencies. `doctor` must pass everything the next run needs.

### 9. Verify

Watch the next real run that exercises the fix, and confirm the specific behavior: for example, the retry happened, or the new provenance field is present. Clean up any temporary environment fix it replaces. Then restart the watcher.

## Hotfix exception

Deploying before merge is an exception, not the normal path. Use it only when the defect blocks work and Paul has agreed:

- Deploy the PR branch: `scripts/deploy.sh origin/<branch>`.
- Record in the PR that the service runs it.
- Run `scripts/deploy.sh` again once the PR merges.

Never deploy unreviewed or unvalidated code this way.

## Useful commands

- Manual tick or status: `PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml tick` (or `status`).
- Board and issue changes: use the `codagent-github-project` skill, which covers the Factory App token and board field IDs.
- Fly Machines: `fly machines list -a agent-factory-sandbox --json`.
- Disk: `df -h ~` and `du -sh ~/.agent-factory/*`. Admission needs `minimum_free_gib` (5 GiB).
