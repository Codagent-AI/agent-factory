## Context

The factory starts or reports post-run development audits in four places:

| Place | Code | Behavior today |
| --- | --- | --- |
| Host attempt wrapper | `src/agent_factory/work_kinds/pull_request/launch.py`, `host_script()` | After `run_command` and `run_status=$?`, it appends `env -u GH_TOKEN … python -P -m agent_factory.audit host --runner … --session-dir … --project … --evidence … \|\| true`. This replays the audit with `agent-runner audit replay` and writes `audit.json`. It is used by fix, feature, and review attempts. |
| Result consumption | `src/agent_factory/runtime.py`, `_settle_audit()` | Called for every consumed run. It calls `audit.settle(...)`, which delivers an eval's collected reports and records `audit.json`. When the outcome is not `delivered`, it records a `<unit>:attempt-<n>:post-run-audit` issue event. |
| Status | `src/agent_factory/operations.py`, `_audit_lines()` | For runs that finished in the last 7 days, it lists an undelivered outcome recorded in `audit.json`. When there is no `audit.json` but `agent-runner-session/run-metrics.json` exists, it synthesizes `missing — the attempt recorded no post-run audit`. |
| Doctor | `src/agent_factory/operations.py` (around line 174) → `audit.readiness()` | Informational diagnostic `post-run audit`. It probes `agent-runner audit` for `audit replay` and `--project`, and checks that the reporting connection file is mode 0600 inside a 0700 directory. |

`src/agent_factory/audit.py:29-30` already has the switch:

```python
# Codagent-AI/agent-factory#60 will own the audit switch when it merges.
AUDIT_ENABLED = False
```

Today only `src/agent_factory/watch/session.py` (around line 190) reads it, as `audit.AUDIT_ENABLED`, to omit the watch-session audit line. `tests/integration/test_watch_session.py` toggles it with `monkeypatch.setattr(audit, "AUDIT_ENABLED", ...)`.

`launch.py` does not import `agent_factory.audit` yet. `audit.py` imports only the standard library, so the new import cannot create a cycle.

Agent Runner writes its own execution `audit.log` under `agent-runner-session/` during every run, and the feature workflow's `record-archive-block.sh` reads it. That log is not a post-run audit artifact and is outside this change.

## Goals / Non-Goals

**Goals:**
- With `AUDIT_ENABLED = False`, which ships:
  - no host audit replay runs;
  - the resident records no `audit.json` and posts no `post-run-audit` events;
  - `status` lists no synthesized missing audits;
  - `doctor` passes the audit check without probing anything.
- Setting `AUDIT_ENABLED = True` restores today's factory-owned behavior exactly: host
  replay, settlement and events, status fallback lines, and the doctor probe. That is a
  one-line revert. Eval audits and their Sheet delivery also need Agent Runner's automatic
  audit hook back on (agent-runner#191), because the factory only delivers reports that the
  sandbox's Runner collected.
- Existing audit tests keep covering the enabled path.

**Non-Goals:**
- Removing or refactoring audit code, `audit.settle`, `audit.host_audit`, or the progress globs that match `audit.log`.
- Changing watch-session audit behavior or the `watch audit:` status lines.
- Agent Runner changes (agent-runner#191), and audits inside Fly guests or eval sandboxes.
- A config-file or environment-variable toggle.

## Approach

Each place reads `audit.AUDIT_ENABLED` at call time, as a module attribute and never through `from agent_factory.audit import AUDIT_ENABLED`, so tests can monkeypatch it. This is the pattern `watch/session.py` already uses.

1. **`audit.py`**
   - Update the switch comment. It is the single factory-wide switch for post-run audits, temporarily off per Codagent-AI/agent-factory#60, and setting it to `True` re-enables host replay, settlement and events, status fallback lines, and the doctor probe.
   - Update the module docstring to say audits run only when the switch is on.
   - In `readiness()`, add a first statement:
     ```python
     if not AUDIT_ENABLED:
         return True, "post-run audits are disabled (Codagent-AI/agent-factory#60)", ""
     ```
     This runs before the `runner is None` check, so the result needs neither the Runner nor the connection. Callers are unchanged. `operations.py` still appends `Diagnostic("post-run audit", ...)`, which now passes.

2. **`launch.py` `host_script()`**
   - Add `from agent_factory import audit`.
   - Build the audit command as a list, as `watch/session.py` does: `audit_lines = [<existing joined command>] if audit.AUDIT_ENABLED else []`. Splice it into `lines` with `*audit_lines` between `"run_status=$?"` and `'exit "$run_status"'`.
   - Keep the existing comment above it, prefixed with "When post-run audits are enabled, …".
   - Everything else in the wrapper stays byte-identical: `set +e`, `run_status`, the restore trap, and the exit status.

3. **`runtime.py` `_settle_audit()`**
   - Add an early `if not audit.AUDIT_ENABLED: return` as the first statement, and say so in the docstring. `audit.settle` itself is unchanged, so its tests keep their meaning.
   - The call site in result consumption is unchanged. `_dispose_result` and `report_events` run as before.

4. **`operations.py` `_audit_lines()`**
   - In the `summary is None` branch, skip the run when the switch is off, before the `run-metrics.json` check:
     ```python
     if summary is None:
         if not audit.AUDIT_ENABLED or not (evidence / audit.HOST_SESSION_DIR / audit.METRICS_FILE).is_file():
             continue
     ```
   - A recorded summary is still read and listed whether the switch is on or off. That covers attempts audited before the switch was turned off, within the 7-day window.

5. **`docs/operations.md` "Post-run audits"**
   - Add a short lead paragraph: post-run audits are temporarily disabled by `AUDIT_ENABLED = False` in `src/agent_factory/audit.py` (issue #60). While they are off:
     - host attempts run no replay;
     - no `post-run-audit` events are posted;
     - `status` lists only previously recorded outcomes;
     - `doctor` reports audits as disabled.
   - Say that re-enabling means setting the constant to `True`, merging, and deploying, and that Agent Runner's own hook is governed separately (agent-runner#191).
   - Keep the rest of the section, which describes the enabled behavior.

Failure behavior: with the switch off nothing new can fail. With it on, paths are unchanged.

## Decisions

- **One existing constant, read at call time.** It already exists and is documented in the watch-dispatch spec as #60's switch. A second flag or a config key would break the "one-line revert" property and widen the change. A read at call time is needed for monkeypatch-based tests.
- **Guard in callers, not in `audit.settle` or `audit.host_audit`.** The audit functions keep their semantics, so the enabled-path tests stay valid unchanged and re-enabling restores tested code.
- **Readiness short-circuits before the `runner is None` check.** The spec requires that `doctor` not depend on Runner audit support or the connection, and that a missing `agent-runner` on PATH not be reported as an audit problem. Missing `agent-runner` is reported by other doctor checks.
- **Status suppresses only the synthesized fallback.** Recorded outcomes are real history and stay visible. This follows the spec scenario "Status keeps earlier recorded outcomes".
- **Progress globs for `audit.log` stay.** That file is Agent Runner's execution log and is still written.

## Testing

All in `tests/integration/test_post_run_audit.py` unless noted.

**Keep the enabled path covered.** Add a module-level autouse fixture:

```python
@pytest.fixture(autouse=True)
def _audits_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "AUDIT_ENABLED", True)
```

Every existing test then exercises today's behavior unchanged. That includes `test_host_wrapper_audits_before_restoring_config_and_keeps_the_run_status`, whose wrapper is built inside the test after the fixture applies; `test_consumed_attempt_with_an_undelivered_audit_is_reported_once`; and the readiness tests. Disabled-state tests override the fixture by calling `monkeypatch.setattr(audit, "AUDIT_ENABLED", False)` in their body.

**New disabled-state tests:**
- *Host wrapper without replay.* Use the stand-in Runner from the existing host-wrapper test, which records calls, writes `run-metrics.json`, and exits 3. Run the wrapper and assert:
  - its exit code is 3;
  - no recorded call starts with `["audit", ...]`;
  - `evidence/audit.json` does not exist;
  - the wrapper text has no `-m agent_factory.audit host`.
- *Settlement is a no-op.* Use a run with `agent-runner-session/run-metrics.json` and no `audit.json`, plus a run with a pre-written failed summary. After `runtime._settle_audit`, `store.pending_events(claim.id)` is empty and no `audit.json` was created for the first run. Add an eval-shaped case: plan `ownership_hints.suite == "and-scene"` with a collected `run-metrics.json` under `.runtime/agent-runner-projects/*/runs/*/`. Assert that no Runner is invoked; `shutil.which` can be monkeypatched to a path that would fail if executed. Also assert that no `audit.json` is written.
- *Readiness passes without anything.* Call `audit.readiness(None, connection=tmp_path / "absent.json", run=<callable that fails the test>)` and get `(True, <detail containing "disabled">, "")`, with the probe never called.
- *Status.* Add a finished run within the window with `run-metrics.json` and no `audit.json`. `_audit_lines` returns `[]`. The existing recorded-failure status test also runs with the switch off and still returns its line. Parametrize it over both switch values.

**Unchanged:** `tests/integration/test_watch_session.py` already parametrizes the switch. Other tests that mention `audit.log` refer to Agent Runner's execution log and need no change.

Run the full suite (`uv run pytest`) plus the repository's lint and type checks.

## Risks / Trade-offs

- **Eval reports are no longer delivered from the host.** This is intended: all factory post-run audits are off. Reports collected before the switch was turned off are not re-delivered. Recovery is out of scope.
- **The enabled path rots while unused.** Mitigation: the autouse fixture keeps its tests running on every CI run.
- **Watch status noise remains.** `watch audit: <id> missing` lines still appear per dispatch. This is existing, separately specified behavior, logged in `decisions.md` as a follow-up candidate.

## Migration Plan

- **Dependency on agent-runner#191:** the factory switch and the Runner hook are
  independent.
  - Until #191 is merged and deployed, the host Runner (rebuilt by `scripts/deploy.sh`
    from `origin/main`) and eval claims (which pin `agent_runner_ref` at admission) may
    still start Runner-side audits.
  - Claims admitted before #191 keep their pinned Runner revision for their whole life.
  - With the switch off, the resident delivers and reports none of those audits. They cost
    Runner time but post no events and add no status lines.
- **Rollout:** merge, then factory-watcher-5 deploys with `scripts/deploy.sh`. Running attempts keep their release and wrapper, so an attempt launched before the deploy may still replay its audit. Its outcome is then settled by the new resident, which skips settlement. Its `audit.json`, if written, is still listed in `status`.
- **Rollback / re-enable:** set `AUDIT_ENABLED = True` in a PR, merge, and deploy. That restores the factory-owned audits. Eval audits also need the Agent Runner automatic hook re-enabled, by reverting agent-runner#191, with Runner revisions that include the revert pinned for new eval claims. No data migration is needed in either direction. `audit.json` and event formats are unchanged.
