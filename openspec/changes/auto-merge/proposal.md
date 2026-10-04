## Why

Every pull request the factory opens waits for Paul to merge it, even a small, well-tested fix whose description raises no concern. The service watcher already starts a headless session for each ready pull request (`PR-READY`). That session can also judge how risky the change is, and the factory can merge the low-risk ones itself, so Paul's attention goes to the pull requests that need it.

## What Changes

- When auto-merge is on, the PR-READY check session also reads the pull request's diff and description and rates its risk `low`, `medium`, or `high`, with reasons, against a published bar: one for fixes, a matching one for tasks (with any behavior change covered by a test), and a stricter one for features. The session still files factory defects as today, and still never comments, approves, or merges.
- The resident, not the session, merges a pull request rated `low` after deterministic gates pass: auto-merge on, factory not paused, repository a configured fix target, pull request open, not a draft, conflict-free, head unchanged since the rating, every check green, every status check the repository's GitHub rulesets require on the base branch reported successful (no required checks means no auto-merge), no unresolved review thread or outstanding change request. The merge is a merge commit pinned to the rated head. CI still running, or a pause, is re-checked each cycle for up to 60 minutes.
- The factory bot posts one risk-verdict comment on the pull request: the rating, the reasons, and either "merged automatically" or the gate that blocked it. Status shows the same. This is the first watch comment on a pull request rather than an issue.
- The existing post-merge sync then updates the working clone and closes the issue, unchanged.
- New `[watch] auto_merge` boolean, default false, enabled in the committed Codagent configuration. Turning it off restores today's behavior exactly.
- Agent Factory gains a CI workflow (lint, typecheck, tests) so its own pull requests have a real check to require. Each fix target gets a ruleset requiring its CI checks on `main`, created by the operator through `gh` (Agent Factory's after its CI workflow is on `main`).
- The pr-check result gains risk fields, so the watch workflow contract becomes `factory-watch/3`.
- Skills and docs: the PR-READY procedure gains the risk bar; the "Paul merges PRs" rule and the watcher documentation allow the resident's auto-merge.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `factory-watch-dispatch`: the PR-READY check rates risk; the factory merges low-risk pull requests behind deterministic gates and posts a risk-verdict comment on the pull request.
- `factory-operations`: the `[watch] auto_merge` setting, auto-merge outcomes in status, and documentation of auto-merge.

## Out of Scope

- Auto-merging anything but factory fix, task, and feature pull requests (no eval, human, or triage work).
- A dry-run or shadow mode, per-repository allowlists, and per-kind switches.
- Approving pull requests, bypassing branch protection, or deleting branches.
- Retrying a merge that GitHub rejected, or re-rating a pull request whose head changed (a later `PR-READY` event rates the new head).
- Deploying after an Agent Factory pull request is auto-merged.

## Impact

- `src/agent_factory/watch/` (result schema, brief, session prompt, a new merge step in the cycle, delivery to a pull request, status), the watch store schema (merge state on the dispatch row), `src/agent_factory/github.py` (combined check status, ruleset-required checks, merge pinned to a head), `src/agent_factory/config.py`, `config/codagent.toml`.
- Workflow `factory-watch-v3.0.yaml` replaces `factory-watch-v2.0.yaml`; contract `factory-watch/3`.
- `.claude/skills/factory-triage/SKILL.md` (Headless PR-READY check, standing rules), `.claude/skills/factory-status`, `AGENTS.md`, `docs/operations.md`, and a new `.github/workflows/ci.yml`.
- The factory GitHub App must be able to merge in every fix-target repository; branch protection that requires an approving review blocks auto-merge there, which is reported, not bypassed.
