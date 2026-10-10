---
name: factory-status
description: Give Paul an on-demand update on the live Agent Factory — what is running, what waits on him (PRs and cards to review, blocked claims), finished evals, failures and factory defects the service filed, and anything undeployed or unhealthy. Use when asked for a factory update, status, or "what's going on with the factory". It reads once and reports; it never watches or loops.
---

# Factory status

This skill is also linked into `~/.claude/skills`, so it can start from another project. Run every command from the Agent Factory checkout, `/Users/paul/codagent/agent-factory` (read its `AGENTS.md`), unless you are already in a checkout of this repository. Never switch that checkout's branch.

Take one snapshot of the factory and report it. Do not start a watcher, a `/loop`, or background polling (to follow specific issues until they stop, use `factory-watch`): the resident detects events and dispatches its own factory-defect checks of ready PRs and failure triage (`AGENTS.md`, "Service-driven watcher"). Read `AGENTS.md` first.

Never print tokens, credential files, or anything under `~/.agent-factory/private/`. Filter output before showing it.

## Gather

Run these from this repository's root. They are read-only.

```sh
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml status   # slots, readiness, watch cursor/sessions/cost/filed issues, blocked claims, audits
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml doctor   # only if status shows a readiness failure or the watch group looks wrong
gh project item-list 1 --owner Codagent-AI --limit 300 --format json \
  --jq '.items[] | select(.status=="Review" or .status=="Running") | [.status, .content.repository, (.content.number|tostring), .content.title, (.owner // "")] | @tsv'
readlink ~/.agent-factory/releases/current; git fetch -q origin; git log --oneline "$(basename "$(readlink ~/.agent-factory/releases/current)")"..origin/main
fly machines list -a agent-factory-sandbox --json | jq length
df -h ~ | tail -1
```

`status` prints long stderr excerpts for old post-run audit failures. Summarize them; do not paste them.

For each Review card, find its pull request and state:

```sh
gh issue view N -R OWNER/REPO --json state,closedByPullRequestsReferences \
  --jq '{state, prs:[.closedByPullRequestsReferences[]|.url]}'
gh pr view URL --json state,isDraft,mergeable,reviewDecision,statusCheckRollup
```

For recent activity, read the database (not with `sqlite3 -readonly`; see `factory-assign`). Pick the window from Paul's question, or default to the last 24 hours:

```sh
sqlite3 ~/.agent-factory/state.sqlite3 "SELECT repository, issue_number, kind, lifecycle, updated_at FROM claim WHERE updated_at > strftime('%Y-%m-%dT%H:%M:%S','now','-1 day') ORDER BY updated_at"
sqlite3 ~/.agent-factory/state.sqlite3 "SELECT event_kind, repository, issue_number, pr_url, state, detail, evidence_path, result_json FROM watch_dispatch WHERE updated_at > strftime('%Y-%m-%dT%H:%M:%S','now','-1 day') ORDER BY updated_at"
```

A dispatch's evidence directory holds `watch-result.json`: for a PR-READY check, a summary and the factory issues it filed or updated; for a triage, the cause, owner, actions, issues, and next step. For a finished eval, read its `result.json` (see `AGENTS.md`) for the verdict, gates, and automated score.

## Report

Lead with what needs Paul, then the rest. Keep each item to one line with a link. Omit empty sections.

1. **Waiting on you**
   - PRs ready to merge: Review cards whose PR is open, not draft, and mergeable. Note any conflict or failing check. The service may auto-merge low-risk PRs; report its risk and merge status. Offer `factory-pr-review` when Paul wants one.
   - Triage next steps that need Paul (from each triage result's `next_step`).
   - Blocked (`needs-input`) claims: the question in one line.
   - Finished evals awaiting a verdict: the score and failed gates.
2. **Running**: each busy eval, fix, feature, and task lane's claim, and how long it has run. Include blocked task claims and task PRs waiting for review under **Waiting on you**.
3. **Since last time**: finished claims, factory PRs merged automatically, factory issues the service filed or updated (`watch factory issues:` in `status`, with their PR or claim), triage (cause and what was done), and failures nobody has handled.
4. **Health**, only when something is off:
   - paused, a readiness failure, or a closed admission window;
   - a reached factory job cap (report the earliest clear time and reset command), undelivered comments, or missing audits for recent sessions;
   - commits on `origin/main` not in the live release (offer to deploy with `factory-deploy`);
   - a Fly Machine left over, or disk near `minimum_free_gib`;
   - a Review card whose issue is closed and PR merged, which the board has not moved to Done.

Do not repeat what the board already shows plainly (card titles in columns); add what it lacks: PR state, which PR or failure each factory issue came from, costs, and failures.

PR updates: when the report covers several pull requests, group them under **Merged**, **Ready for you to merge**, and **Other status**, with the full PR URL and one short clause on each line.

## Follow-ups

Act only when Paul asks:

- Reviewing a PR yourself: the `factory-pr-review` skill.
- Looking into a failure: the `factory-triage` skill.
- Retrying an ended service check or triage: `agent-factory … watch redispatch <id>`.
- Deploying merged changes: the `factory-deploy` skill.
- Assigning an issue to the factory: the `factory-assign` skill.

## Priority lanes

Priority lanes allow one unfinished attempt per kind at each level: Urgent, High,
Medium, and Low. Unset or unknown Priority uses Low for occupancy, but sorts after
explicit Low. Higher-priority starts can run beside lower work; new lower work
waits while a higher lane of the same kind is busy. Running attempts are never
preempted or moved when Priority changes. The next attempt uses the card's current
Priority. Repetitions and retries of a claim that actually started in its current
episode are continuations and need only their own lane. Admissions, fresh claims,
unblocks, and review rounds are new starts. A claim that never launched is also a
new start.

Status keeps `<kind> slot: free` for idle kinds, or prints
`<kind> slot: busy (high, medium, low)` with one `<kind> lane <priority>:` holder
and progress line per attempt. Lane wait lines name the requested lane, the
blocking holder, and the cause (busy lane, higher lane, legacy holder, or kind
mode). Pre-upgrade attempts have no lane and hold every lane of their kind until
they finish. `lanes: off` means the per-kind guard is restored. Only resident
startup or `agent-factory --config <local.toml> lanes enable` enables lanes;
`tick`, `doctor`, and `status` never change mode.

Size host disk and memory for up to four concurrent attempts **per host kind**
(fix, feature, task), including each attempt's clones, artifacts, model clients,
and build tools. The existing disk floor, memory, quota, readiness, and job-cap
holds still bound admission; memory is re-sampled before each sandbox admission.
Concurrent eval lanes can increase Fly image builds, Machines, and spend.

Rollback past lanes goes through `scripts/deploy.sh`. It refuses while any kind
has more than one unfinished attempt. Pause, let attempts settle or cancel claims
until every kind has at most one unfinished attempt, then deploy the older
release. The script restores the per-kind index after pausing. Failure before the
old resident's removal is confirmed restores the live pointers and re-enables
lanes; failure after removal keeps the per-kind guard. A hand rollback, or an
older deploy script, bypasses both the refusal and guard restoration.
