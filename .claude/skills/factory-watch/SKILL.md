---
name: factory-watch
description: Watch the live Agent Factory on Paul's Mac for technical failures, new claims, and finished evals; triage each event and fix genuine factory defects. Use when asked to watch, monitor, or babysit the factory or a running eval.
---

# Factory watch

Watch the live factory service (see `AGENTS.md`), wake on anything that needs a look, triage it, then keep watching.

## Start

Run the watcher in the background and wait for it to exit:

```sh
.claude/skills/factory-watch/watch.sh            # all events after now
.claude/skills/factory-watch/watch.sh --no-claims   # ignore new admissions
```

Options: `--since <ISO8601 UTC>` to start from an earlier time, `--grace-minutes N` (default 7), `--interval SECONDS` (default 90). It reads `$AGENT_FACTORY_ROOT/state.sqlite3` (default `~/.agent-factory`) read-only.

The watcher exits after printing one or more events and a final `next: --since <time>` line. Handle the events, then start it again with exactly that `--since` value. Each event falls in one check window, so none is repeated or missed. To run it on a schedule instead, use `/loop` with this skill and carry the `--since` value between runs.

## Events

- **`CLAIM`**: the factory admitted work. It is expected when a card was moved to Ready, or a Bug was routed there. Confirm the reason from the issue timeline and board fields; human card moves do not appear in the timeline. Report only a pickup nobody could have caused.
- **`EVAL-DONE`**: an eval run finished. Read the claim's outcome and `factory status`, then report the verdict and where its results were saved.
- **`FAILURE`**: a run is still failed, interrupted, cancelled, or timed out after the grace period. It is matched on when the run finished, not when it started. Investigate it (below).

If the user says they review bug cards themselves, do not summarize `needs-input` or `pull-request` outcomes. Report only factory failures and eval results.

## Known non-failures

Check these before calling something a factory defect:

- The resident ticks every `[schedule] poll_minutes` (5). Results are consumed at the next tick, not immediately. Do not diagnose a stall until more than one full tick has passed. Look at the latest `settings.updated_at`, and `sample <pid>` to confirm it is sleeping.
- A host fix run is `interrupted` ("owned process exited without durable result") until the next tick reads `attempt-N/fix-outcome.json` and marks it `completed`. The grace period covers this.
- A Fly eval's first launch can fail pre-suite with an HTTP 400 `failed to get manifest`: the image was pushed seconds earlier. The factory relaunches once (fixed by agent-factory#19).
- A fix run whose outcome is `needs-input` is a product result, not a failure.

## Investigating a failure

1. Read the run row (`status`, `reason`, `attempt_number`, `result_json`, `evidence_path`) and its claim (`lifecycle`, `outcome_json`) from `~/.agent-factory/state.sqlite3`.
2. Read the attempt's artifacts under `evidence_path`:
   - fix: `attempt-N/fix-outcome.json`, `logs/agent-runner.log`, `agent-runner-session/audit.log`.
   - eval: `.factory/launch-stage.json`, `.factory/launcher.log`, `.factory/image-build.log`, `factory-suite.log`.
   Never print tokens, credential files, or `private/` contents; filter log output.
3. Decide where the defect is:
   - Factory: fix it in a new worktree off `origin/main` using TDD, run `agent-validate run`, and open a PR. Never edit the live `agent-factory.fly` worktree, and never deploy while an eval is running.
   - Agent Runner, Agent Evals, or Skills: report it to the owning session or the user with the evidence.
   - Environment (disk floor, credentials, Fly): report the concrete fix. Free only caches that regenerate themselves; anything else needs the user's approval.
4. If the claim has an automatic retry left and the cause is still present, `pause` the factory so the retry is not wasted, then `resume` once it is fixed.
5. Restart the watcher.
