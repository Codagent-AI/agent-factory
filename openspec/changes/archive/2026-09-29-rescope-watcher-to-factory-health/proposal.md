## Why

The service watcher (#61) reviewed every factory pull request, posted operator decisions on it, let triage open fix pull requests, and logged claim and eval-completion events. Paul's scope for the watcher is narrower: it makes sure the factory itself works. Reviewing the code the factory builds is not its job, and neither is fixing defects it finds; the factory fixes them once they are filed and assigned to it. The CLAIM and EVAL-DONE log lines serve nobody, since on-demand status reads the claim table directly.

## What Changes

- Detect only `FAILURE` and `PR-READY`. Stop detecting `CLAIM` and `EVAL-DONE`, and drop the cursor window and claim index that existed only for them. Pending rows of those kinds left by an earlier release are recorded `logged`.
- Replace the PR-READY review with a factory-defect check: the session mines the PR description's red and orange items, and the run's evidence, for defects in the factory stack, and files or updates issues assigned to the factory. It posts nothing on the pull request; status lists the filed issues.
- Triage diagnoses, may pause or resume, and files or updates an issue for a factory defect. It never branches, commits, pushes, or opens pull requests.
- New result schemas (`pr-check`, and `triage` with `issues_filed`/`issues_updated` in place of `pull_request`/`handoff`), so the workflow contract becomes `factory-watch/2` (`factory-watch-v2.0.yaml`).
- Remove the decisions comment and the `[watch] operator` setting. Rename the doctor check to `watch issue login`: the login files issues, so it must have write access (admission requires a writer author) and must not be the factory bot.
- Move both headless procedures into `factory-triage`; remove the headless modes from `factory-pr-review` and `factory-pr-reviewer`.

## Impact

- `src/agent_factory/watch/`, `src/agent_factory/config.py`, `src/agent_factory/store.py`, `config/codagent.toml`.
- `.claude/skills/factory-triage`, `factory-pr-review`, `factory-status`, `.claude/agents/factory-pr-reviewer.md`, `docs/operations.md`, `AGENTS.md`.
- Specs: `factory-watch-dispatch`, `factory-operations`.
