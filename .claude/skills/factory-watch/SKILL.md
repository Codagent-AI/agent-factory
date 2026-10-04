---
name: factory-watch
description: Watch one or more specific issues in the live Agent Factory until the factory stops making progress on them, for any reason (PR opened, failure, needs-input, settled, not queued), then report what happened to them, including what the service watcher did. Use when Paul asks to watch, follow, or keep an eye on particular factory issues or runs and tell him when they finish or stall.
---

# Factory watch

This skill is also linked into `~/.claude/skills`, so it can start from another project. Run every command from the Agent Factory checkout, `/Users/paul/codagent/agent-factory` (read its `AGENTS.md`), unless you are already in a checkout of this repository. Never switch that checkout's branch.

Follow specific issues through the factory, and report once the factory stops moving them. Read `AGENTS.md` first.

This is not the general watcher. The resident already detects PR-READY and FAILURE events and dispatches its own headless sessions (`AGENTS.md`, "Service-driven watcher"). This skill only waits on the issues Paul names. It costs no model session while it waits, because `watch.py` polls with sqlite and `gh` reads.

Never print tokens, credential files, or anything under `~/.agent-factory/private/`. Filter output before showing it. The skill is read-only: it never changes cards, labels, claims, or the factory.

## 1. Start the watch

Take the issues as `OWNER/REPO#N` or issue URLs. Run the script from this repository's root as a **background Bash command** (`run_in_background: true`), not a Monitor or `/loop`. It exits once every issue has stopped, and you are notified then.

```sh
~/.agent-factory/releases/current/.venv/bin/python .claude/skills/factory-watch/watch.py Codagent-AI/agent-runner#186 Codagent-AI/agent-runner#187
```

Options: `--poll` (seconds, default 60), `--settle` (seconds an issue must stay idle before it counts as stopped, default 360, which covers the resident's tick interval), and `--max-hours` (default 12; exits 2 with the issues still moving).

An issue counts as progressing while any of these hold:

- a run of its latest claim has not finished;
- a watch dispatch for that run is pending or launched;
- that run ended with a PR-READY or FAILURE event (a failed status, or a completed run whose outcome is `failed`) and the watcher has not dispatched it yet (within 25 minutes, which covers FAILURE's grace period);
- its card is queued: open, Owner=factory, Status=Ready, and no `needs-input` label.
- someone (not a bot) reviewed or commented on its pull request after the last run finished, so a review round is due. It waits for a free slot like any other run.

Each state change prints one line, and each issue prints a `STOPPED` line when it stops. A `WATCH EVENT MISSING` part means the run ended with an event the watcher should have handled, but no dispatch appeared.

Tell Paul in one line which issues you are watching, then stop work on it until the background command finishes. Do not poll its output in the meantime.

## 2. Report

When the command finishes, read its output file. Then gather an update for the watched issues only, using the `factory-status` skill's commands and report format, narrowed to these issues. For each issue:

- **Where it stopped and why**: the `STOPPED` line, in words. For example: PR opened and waiting for review, blocked on `needs-input` (quote the question from the issue), failed, settled, or not queued (and why: not Ready, wrong owner, `needs-input`).
- **Pull request**: its URL, state, mergeability, and checks.
- **Service watcher**: for each dispatch, read `watch-result.json` in its evidence directory. Do not read its `private/` folder. For a PR-READY check, give the factory issues it filed or updated, or say it found none. For a triage, give its cause, owner, actions, issues, and next step. Confirm it fixed nothing (no branches, commits, or PRs from that session) and, for a PR-READY check, posted nothing on the PR. Flag any `WATCH EVENT MISSING`, any dispatch that ended other than `completed`, or a session that broke these rules.
- **What Paul needs to do**, if anything.

If the script timed out, report the issues that are still moving and what each one is doing, and ask whether to keep watching.

Lead with what needs Paul. Keep it short, and do not repeat what the board already shows plainly.

PR updates: when the report covers several pull requests, group them under **Merged**, **Ready for you to merge**, and **Other status**, with the full PR URL and one short clause on each line. Include only the watched issues' PRs.

## Follow-ups

Act only when Paul asks: `factory-pr-review` to review a PR, `factory-triage` to investigate a failure, `factory-assign` to queue an issue, and `agent-factory … watch redispatch <id>` to retry a watcher session.
