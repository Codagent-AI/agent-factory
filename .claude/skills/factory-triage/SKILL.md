---
name: factory-triage
description: Diagnose a failed, interrupted, or timed-out Agent Factory run on Paul's Mac, and fix genuine factory defects through a tested PR that is deployed only after it merges. Use when asked to look into a factory failure or a stuck claim. The service's headless watch sessions follow its Headless PR-READY check and Headless triage sections, which file issues for factory defects and never fix them.
---

# Factory triage

This skill is also linked into `~/.claude/skills`, so it can start from another project. Run every command from the Agent Factory checkout, `/Users/paul/codagent/agent-factory` (read its `AGENTS.md`), unless you are already in a checkout of this repository. Never switch that checkout's branch.

The resident's watcher makes sure the factory itself works. It is not code review of what the factory builds. For each failed run it dispatches a headless triage session (see "Headless triage"), and for each pull request a fix, feature, or task run opens it dispatches a headless check of the run for factory defects (see "Headless PR-READY check"). Both only diagnose and file issues; neither fixes anything. Use this skill on demand when Paul asks about a failure, or when `factory-status` shows one nobody has handled; then the fix steps below apply. Read `AGENTS.md` first: it describes the service clone, the deploy script, and configuration pins.

## Standing rules

- Never print or log tokens, credential files, or anything under `~/.agent-factory/private/`. Filter log output before showing it.
- Paul merges PRs. Never merge one yourself.
- The service runs a release under `~/.agent-factory/releases/` (see `AGENTS.md`). Never edit a release or the service clone `~/.agent-factory/agent-factory`; only `scripts/deploy.sh` changes them (see Deploy). Never switch branches in Paul's checkout, `/Users/paul/codagent/agent-factory`. Do all fix work in a separate worktree.
- Never run `agent-validator clean` to get around the validator's retry limit. Ask Paul instead.
- Never use bare `git stash`. Use a temporary WIP commit, or a stash with a unique tag that you apply by SHA.
- Every Fly Machine the factory or you created must end up destroyed. Check `fly machines list -a agent-factory-sandbox` after Fly work.
- Report solutions, not just problems: say what is wrong, what you did, and what Paul must decide.

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

1. Read the run row and its claim from the factory database: `$AGENT_FACTORY_ROOT/state.sqlite3` (default `~/.agent-factory/state.sqlite3`).
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

- Fix review findings that are correct, then mark each one with `agent-validate update-review fix <id> "<what changed>"` (`agent-validate update-review list` shows the ids).
- To skip a finding, run `agent-validate update-review skip <id> "<concrete reason>"`, then rerun. Never edit `validator_logs` files directly.
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
2. Deploy at any time, including while jobs run: each job keeps the release and Agent Runner binary it started from. Do not wait for slots to free up or pause first.
3. Use the `factory-deploy` skill (`scripts/deploy.sh`; see "Deploying" in `AGENTS.md`). It fast-forwards the Agent Runner checkout on `main` and builds a release at `origin/main`, then pauses, runs `make build` for Agent Runner, and runs `doctor`. If `doctor` passes, it reloads the LaunchAgent on the release, restores the prior pause state, ticks, and removes old releases. If `doctor` fails, it points the service back at the previous release and leaves the factory paused.
4. Check whether the PR changed the LaunchAgent template (`packaging/launchd/`) or local configuration; apply those too. The script handles dependencies. `doctor` must pass everything the next run needs.

### 9. Verify

Watch the next real run that exercises the fix, and confirm the specific behavior: for example, the retry happened, or the new provenance field is present. Clean up any temporary environment fix it replaces.

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

## Headless sessions

The service dispatches these through its `factory-watch` workflow. Read the brief at `brief_file` and use its `paths`, `fix_targets`, and `result_file`. Do not ask questions, and do not message other sessions. Use `paths.clone` (a throwaway checkout of this repository) as the working directory, `paths.scratch` for every temporary file, `paths.factory_python` as Python, and `paths.config` as the local configuration. Never use an operator checkout or the live release. Honor every item in the brief's `forbidden` list. A headless session never deploys or merges, and never fixes anything: it does not create branches, commit, push, or open a pull request, in any repository.

A factory defect is a defect in the factory stack: Agent Factory, the workflows and Agent Runner it runs, Agent Skills, and Agent Validator as the factory uses it. A defect in the product code a pull request changes is not one.

### Filing a factory defect

For each genuine factory defect:

1. Pick the repository that owns it: Agent Factory (factory code and its packaged workflows), Agent Runner, Agent Skills, Agent Validator, or Agent Evals.
2. Search its open issues first: `gh issue list -R OWNER/REPO --state open --search "<key terms>"`. When one already covers the defect, add the new evidence as a comment on it (`gh issue comment`) and record its URL in `issues_updated`. Do not file a duplicate.
3. Otherwise write the body to a file in `paths.scratch` and run `gh issue create -R OWNER/REPO --title "<defect>" --body-file <file>`. The body gives concrete evidence: the pull request or run (link, run id, evidence path), the red or orange item or failure it came from, and a file, line, or log excerpt; then the cause as far as known and a proposed fix. Record its URL in `issues_filed`.
4. Assign it to the factory when the owning repository is in the brief's `fix_targets`. From `paths.clone`, run `AGENT_FACTORY_CONFIG=<paths.config> <paths.factory_python> .claude/skills/factory-assign/assign.py OWNER/REPO N --apply fix`. It sets the native type Bug, Priority Low when Priority is empty, Owner=factory, and Status=Ready. Leave Priority Low unless the defect blocks work; then set High with `gh api graphql -f query='mutation($i:ID!){updateIssueFieldValue(input:{issueId:$i, issueField:{fieldId:"IFSS_kgDOAmcJrg", singleSelectOptionId:"IFSSO_kgDOBDQ5Cw"}}){issue{id}}}' -f i=<issue node id>`. When the repository is not a fix target, file the issue without assigning it and say so in the result.

## Headless PR-READY check

A fix, feature, or task run finished with a pull request. Check what the run exposed about the factory; the pull request's own change is not your concern. Do not review the pull request's code, and do not post a review or comment on the pull request. Do not commit, push, or open a pull request.

1. Read the pull request description: `gh pr view <number> -R <repository> --json title,body,url`. Factory descriptions mark attention items red (needs attention) or orange (worth a look).
2. For each red and orange item, decide whether it points to a factory defect, for example a workflow step that misfired, a wrong resume, a validator run that misbehaved, or a misleading annotation. Items about the product change, and false alarms, are not factory defects; leave them.
3. As needed, confirm from the run's evidence (`run.evidence_path` in the brief: logs, the Runner session's `audit.log`, the outcome file) and the code in `paths.clone`.
4. File or update an issue for each genuine factory defect (see "Filing a factory defect").

Write exactly one JSON object to `result_file` with `procedure: "pr-check"`, `summary` (one or two sentences: what you checked and what you found), `issues_filed` (array of issue URLs you created), and `issues_updated` (array of existing issue URLs you added evidence to). Leave both arrays empty when you found no factory defect. The resident posts nothing about this check; `status` lists the issues.

## Headless triage

The factory has already had an opportunity to start automatic recovery in this cycle. Follow steps 2 through 4 of Handling a failure, with these limits:

- Diagnose from the brief's run and claim records, the evidence under `run.evidence_path`, and the code in `paths.clone`; reproduce when you safely can.
- You may pause or resume the factory through `paths.agent_factory` with `--config <paths.config>` when the evidence justifies it, as in step 1: pause to protect later retries and other claims while the cause persists, and resume when you paused it and the cause is gone.
- Do not fix anything. Do not commit, push, or open a pull request, and do not apply environment fixes. For a factory defect, file or update an issue (see "Filing a factory defect"). For a transient or environment cause, file no issue unless it exposed a real defect; say in `next_step` what Paul must do.

Write exactly one JSON object to `result_file` with `procedure: "triage"`, `cause`, `evidence` (array of strings), `owner` (`factory code`, `Agent Runner`, `Agent Evals`, `Agent Validator`, `Skills`, `environment`, or `transient`), `retry`, `actions` (array of strings: what you did, such as pausing), `issues_filed` and `issues_updated` (arrays of issue URLs), `paused_by_session`, `resumed_by_session` (booleans), and `next_step`. State the concrete evidence and the recommended next step. The resident posts the result on the claim's issue as the factory bot.
