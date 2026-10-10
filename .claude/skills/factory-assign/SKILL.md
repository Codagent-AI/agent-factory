---
name: factory-assign
description: Hand one GitHub issue to the live Agent Factory on Paul's Mac. Sets what admission needs (issue type, board Owner=factory and Status=Ready, a default Priority, the eval label), runs one tick, and confirms the factory claimed the issue. Use when asked to assign an issue to the factory, have the factory pick up, fix, or run issue X, queue a Bug, Feature, Task, or Eval for the factory, or make sure the factory picks it up.
---

# Factory assign

This skill is also linked into `~/.claude/skills`, so it can start from another project. Run every command from the Agent Factory checkout, `/Users/paul/codagent/agent-factory` (read its `AGENTS.md`), unless you are already in a checkout of this repository. Never switch that checkout's branch.

Give one issue to the live factory, then confirm the factory claimed it. Read `AGENTS.md` first. It describes the release the service runs and its configuration.

## Rules

- Change only what admission needs. Preserve the issue's body, title, other labels, assignees, and other board fields. Never overwrite an existing Priority.
- Never invent eval inputs. If the request body is invalid, stop and report the parse error.
- Never print tokens. `assign.py` keeps both tokens in memory.
- Never edit a release or the service clone.
- After refusal checks pass, `assign.py --apply` records this Claude session in the issue body before setting Owner and Status. The read-back includes `session:`. With no Claude session, it makes no body write.

## What admission requires

The tick admits a card when all of the following hold (`work_kinds/*/handler.py` `snapshot`, `runtime.cycle`):

- **Fix**: an open issue in a `[fix] targets` repository of the live shared config, with native type **Bug**, no `needs-input` label, and an author who has write, maintain, or admin permission. The card needs Owner=factory and Status=Ready.
- **Eval**: an open issue in `[routing] eval_source` (`agent-evals`) with native type **Eval** and an author who has write permission. The card needs Owner=factory and Status=Ready, and the body must parse: exactly one fenced eval TOML block with supported keys. The `eval-request` label is routing's trigger, not an admission gate. Still add it, because it is the documented convention.
- **Feature**: an open issue in a configured feature target repository, with native type **Feature**, no `needs-input` label, an author with write, maintain, or admin permission, and Owner=factory and Status=Ready. Check the live release's feature targets and handler before changing a card. The `blocked` label and GitHub blocked-by relationship do not veto admission; keep a dependent feature in Backlog until its prerequisite has landed, even if Owner=factory.
- **Task**: an open writer-authored issue in a fix target with native type **Task**, no `needs-input` label, Owner=factory and Status=Ready. The shared `[task]` section must enable admission. A Task only reaches the factory when moved to Ready; it is not routed automatically.
- **All kinds**: the factory is not paused, the admission window is open (evals use `[schedule]`; fixes are always open unless `[fix] schedule` is set), the card's Priority lane and every higher lane of its kind are free, and the kind's readiness checks pass (disk floor, credentials, Fly). Each tick reserves at most one attempt per kind. It takes cards in order of Priority, then newest created, so a higher-ranked Ready card of the same kind goes first.
- **Earlier claims**: an active claim is reused. A settled fix starts again when its card returns to Ready. A settled eval starts again when its parsed eval settings change (edits to prose or formatting do not count), even with its Verdict still set; with unchanged settings, only once its Verdict is cleared. Ask Paul before clearing a Verdict.

## 1. Choose the kind

- An Eval in `agent-evals` is an **eval**.
- A Bug in a fix target is a **fix**.
- A Feature in a configured feature target is a **feature**. Do not `--apply fix` to a Feature.
- A Task in a configured fix target is a **task**. Never change a Bug or Feature to Task, or Task to Bug or Feature, with `--apply`.
- If the type is unset, choose fix only when the issue describes a defect and the repository is a fix target.
- Otherwise ask Paul when the kind genuinely cannot be inferred, for example a Task or an `agent-evals` issue without a type.

## 2. Check, then set

Run the helper with the release's interpreter, from this repository's root:

```sh
PY=~/.agent-factory/releases/current/.venv/bin/python
$PY .claude/skills/factory-assign/assign.py OWNER/REPO NUMBER                 # read-only check
$PY .claude/skills/factory-assign/assign.py OWNER/REPO NUMBER --apply task # or fix / feature / eval
```

The check prints each requirement as `ok` or `MISSING`. It also prints what the factory's own `snapshot` makes of the card, the cards ranked ahead of it, any claims, and a `result:` line. It exits 0 when the issue is admissible or already claimed. A dependent Feature may deliberately have Owner=factory and Status=Backlog; it is assigned but not queued for admission. Do not use `--apply feature` to bypass that dependency: GitHub blocked-by relationships and the `blocked` label do not prevent Factory admission.

- If the result is `already claimed`, skip to the report. `--apply` refuses such an issue: moving a blocked claim's card to Ready resumes that claim, so do it only when Paul asks.
- If the check shows a requirement Paul must resolve (closed issue, wrong repository, author without write permission, `needs-input`, or an invalid eval body), stop and report it. `--apply` refuses these too.

`--apply` then does the following, and prints the check again as a read-back:

- sets the native type, but only if it is unset (`--retype` replaces another type; pass it only after Paul confirms);
- adds `eval-request` to an eval;
- sets Priority to Low only if it is empty;
- adds the issue to the board if it is missing;
- sets Owner=factory, then Status=Ready.

It uses Paul's `gh` login for the type, labels, and Priority, and the Factory App token for the board. Confirm the read-back shows `factory view: admissible`.

## 3. Check capacity

```sh
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml status | grep -E '^(paused|lanes:|lane wait:|(eval|fix|feature|task) (slot|lane)|readiness|quota|admission window)'
```

If the factory is paused, the card's lane or a higher lane of its kind is busy, a `readiness:<kind>` line reports a failure, or the admission window is closed, say so. The card waits in Ready and is admitted once the condition clears. A tick will not help, so stop here and report.

## 4. Tick and confirm

Run one tick rather than waiting up to five minutes for the resident. A tick holds the cycle lock, so a manual tick is safe while the resident runs.

```sh
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml tick
```

Then rerun the check, or read the claim directly. Do not use `sqlite3 -readonly`. The database uses WAL, and a read-only open fails when its `-wal` and `-shm` files do not exist, which is the case whenever no other process has it open.

```sh
sqlite3 ~/.agent-factory/state.sqlite3 "SELECT id, kind, lifecycle, created_at FROM claim WHERE repository='OWNER/REPO' AND issue_number=NUMBER ORDER BY created_at"
```

It was admitted when a new claim appears and `status` shows it in its slot, for example `fix slot: busy (high)` followed by `fix lane high: Codagent-AI/agent-runner#150 fix (running)`.

If it was not admitted, run one more tick at most. Then diagnose from the check output and the requirements above, and report what is missing. Common causes are a higher-ranked card that took the slot, a failed readiness check, or `needs-input` added by the tick because the eval request was invalid. Do not loop.

## Report

- the issue URL and the kind;
- each field the helper set, and each one it left unchanged;
- the admission result: the claim id, its lifecycle, and the slot line; or what blocks admission, and whether the card will be picked up on its own once that clears.

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
