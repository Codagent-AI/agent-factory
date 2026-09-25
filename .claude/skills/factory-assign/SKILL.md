---
name: factory-assign
description: Hand one GitHub issue to the live Agent Factory on Paul's Mac. Sets what admission needs (issue type, board Owner=factory and Status=Ready, a default Priority, the eval label), runs one tick, and confirms the factory claimed the issue. Use when asked to assign an issue to the factory, have the factory pick up, fix, or run issue X, queue a Bug or an Eval for the factory, or make sure the factory picks it up.
---

# Factory assign

Give one issue to the live factory, then confirm the factory claimed it. Read `AGENTS.md` first. It describes the release the service runs and its configuration.

## Rules

- Change only what admission needs. Preserve the issue's body, title, other labels, assignees, and other board fields. Never overwrite an existing Priority.
- Never invent eval inputs. If the request body is invalid, stop and report the parse error.
- Never print tokens. `assign.py` keeps both tokens in memory.
- Never edit a release or the service clone.

## What admission requires

The tick admits a card when all of the following hold (`work_kinds/*/handler.py` `snapshot`, `runtime.cycle`):

- **Fix**: an open issue in a `[fix] targets` repository of the live shared config, with native type **Bug**, no `needs-input` label, and an author who has write, maintain, or admin permission. The card needs Owner=factory and Status=Ready.
- **Eval**: an open issue in `[routing] eval_source` (`agent-evals`) with native type **Eval** and an author who has write permission. The card needs Owner=factory and Status=Ready, and the body must parse: exactly one fenced eval TOML block with supported keys. The `eval-request` label is routing's trigger, not an admission gate. Still add it, because it is the documented convention.
- **Both**: the factory is not paused, the admission window is open (evals use `[schedule]`; fixes are always open unless `[fix] schedule` is set), the kind's slot is free, and the kind's readiness checks pass (disk floor, credentials, Fly). The tick admits at most one card. It takes cards in order of Priority, then newest created, so a higher-ranked Ready card of the same kind goes first.
- **Earlier claims**: an active claim is reused. A settled fix starts again when its card returns to Ready. A settled eval starts again when its request body changes, or, with the same body, only once its Verdict is cleared. Ask Paul before clearing a Verdict.

## 1. Choose the kind

- An Eval in `agent-evals` is an **eval**.
- A Bug in a fix target is a **fix**.
- If the type is unset, choose fix only when the issue describes a defect and the repository is a fix target.
- Otherwise ask Paul. For example, ask when the issue is a Feature or Task, or when it is an `agent-evals` issue without a type.

## 2. Check, then set

Run the helper with the release's interpreter, from this repository's root:

```sh
PY=~/.agent-factory/releases/current/.venv/bin/python
$PY .claude/skills/factory-assign/assign.py OWNER/REPO NUMBER                 # read-only check
$PY .claude/skills/factory-assign/assign.py OWNER/REPO NUMBER --apply fix     # or --apply eval
```

The check prints each requirement as `ok` or `MISSING`. It also prints what the factory's own `snapshot` makes of the card, the cards ranked ahead of it, any claims, and a `result:` line. It exits 0 when the issue is admissible or already claimed.

- If the result is `already claimed`, skip to the report.
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
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml status | grep -E '^(paused|eval slot|fix slot|readiness|quota|admission window)'
```

If the factory is paused, the kind's slot is busy, a `readiness:<kind>` line reports a failure, or the admission window is closed, say so. The card waits in Ready and is admitted once the condition clears. A tick will not help, so stop here and report.

## 4. Tick and confirm

Run one tick rather than waiting up to five minutes for the resident. A tick holds the cycle lock, so a manual tick is safe while the resident runs.

```sh
PATH=~/.agent-factory/releases/current/.venv/bin:$PATH agent-factory --config ~/.agent-factory/config.toml tick
```

Then rerun the check, or read the claim directly. Do not use `sqlite3 -readonly`. The database uses WAL, and a read-only open fails when its `-wal` and `-shm` files do not exist, which is the case whenever no other process has it open.

```sh
sqlite3 ~/.agent-factory/state.sqlite3 "SELECT id, kind, lifecycle, created_at FROM claim WHERE repository='OWNER/REPO' AND issue_number=NUMBER ORDER BY created_at"
```

It was admitted when a new claim appears and `status` shows it in its slot, for example `fix slot: Codagent-AI/agent-runner#150 fix (running)`.

If it was not admitted, run one more tick at most. Then diagnose from the check output and the requirements above, and report what is missing. Common causes are a higher-ranked card that took the slot, a failed readiness check, or `needs-input` added by the tick because the eval request was invalid. Do not loop.

## Report

- the issue URL and the kind;
- each field the helper set, and each one it left unchanged;
- the admission result: the claim id, its lifecycle, and the slot line; or what blocks admission, and whether the card will be picked up on its own once that clears.
