- [ ] Temporarily disable factory post-run audits behind the single `audit.AUDIT_ENABLED` switch

## Task: Disable factory post-run audits behind one switch

Implement the whole change described in these files, in the change directory:

- `proposal.md`;
- the delta specs under `specs/`: `factory-operations` (ADDED "Govern post-run audits with
  one switch") and `factory-watch-dispatch` (MODIFIED "Record each dispatched session's
  usage", wording only);
- `design.md`;
- the decision log `decisions.md`. Where entries differ, later ones override earlier ones,
  so PR-1, PR-2, and AR-1 take precedence;
- the automated obligations in `test-plan.md`: INT-001 to INT-005. It has no E2E
  obligations.

Always compare against `origin/main`. Never edit a release, the service clone, the live
`~/.agent-factory` state, the real `~/.agent-runner/development-audit-connection.json`, or
`/Users/paul/codagent/*`. Never deploy: factory-watcher-5 deploys after merge. Delete no
audit code or tests beyond what the switch requires.

### Scope

Every guard reads the switch as the module attribute `audit.AUDIT_ENABLED` at call time.
Never use `from agent_factory.audit import AUDIT_ENABLED`, so tests can monkeypatch it. The
switch stays `False`.

1. **`src/agent_factory/audit.py`**:
   - Replace the comment above `AUDIT_ENABLED = False`. The new comment says this is the
     single factory-wide switch for post-run audits, temporarily off per
     Codagent-AI/agent-factory#60. Setting it to `True` re-enables host replay, the
     resident's settlement and `post-run-audit` events, the status fallback lines, and the
     doctor probe. Eval audits also need Agent Runner's automatic hook
     (Codagent-AI/agent-runner#191).
   - Update the module docstring to say audits run only while the switch is on.
   - Add a first statement to `readiness()`, before the `runner is None` check:
     `if not AUDIT_ENABLED: return True, "post-run audits are disabled (Codagent-AI/agent-factory#60)", ""`.
     Wrap the line to the repository's line length.
2. **`src/agent_factory/work_kinds/pull_request/launch.py` `host_script()`**:
   - Add `from agent_factory import audit`.
   - Build the existing `env -u … -m agent_factory.audit host … || true` command into a list
     that is empty when `audit.AUDIT_ENABLED` is false, as `watch/session.py` builds
     `audit_command`. Splice it with `*` between `"run_status=$?"` and `'exit "$run_status"'`.
   - Keep the existing comment, adapted to begin "When post-run audits are enabled, …".
   - Leave everything else in the wrapper unchanged:
     - `set +e`, `run_status`, `_RESTORE_TRACKED_CONFIG_LINES` and its trap, and the exit
       line;
     - the `audit.log` progress globs.
3. **`src/agent_factory/runtime.py` `_settle_audit()`**:
   - Make `if not audit.AUDIT_ENABLED: return` the first statement, and say so in the
     docstring.
   - Do not change `audit.settle`.
4. **`src/agent_factory/operations.py` `_audit_lines()`**:
   - In the `summary is None` branch, `continue` when the switch is off, before the
     `run-metrics.json` check.
   - Recorded `audit.json` summaries are still read and listed whether the switch is on or
     off.
5. **`docs/operations.md` "Post-run audits"**:
   - Add a lead paragraph saying audits are temporarily disabled by `AUDIT_ENABLED = False`
     in `src/agent_factory/audit.py` (#60). While they are off:
     - host attempts run no replay;
     - no `post-run-audit` events are posted;
     - `status` lists only previously recorded outcomes;
     - `doctor` reports audits as disabled.
   - Say that re-enabling means setting the constant to `True`, merging, and deploying, and
     that eval audits also need Agent Runner's automatic hook (agent-runner#191).
   - Keep the rest of the section as the description of the enabled behavior.
6. **Tests** (`tests/integration/test_post_run_audit.py`):
   - Add a module-level autouse fixture that sets `audit.AUDIT_ENABLED` to `True`, so every
     existing test keeps covering the enabled path unchanged (INT-005).
   - Disabled-state tests set it to `False` in their own body.
   - INT-001: run the host wrapper under real bash with a stand-in Runner, reusing `Built`
     from `tests/integration/test_host_launch.py`. The Runner's `run` writes
     `run-metrics.json` and an `audit.log` and exits 3. Assert:
     - the exit code is 3;
     - no Runner call starts with `audit`;
     - the wrapper has no `agent_factory.audit host`;
     - there is no `<evidence>/audit.json`;
     - `agent-runner-session/audit.log` is still present;
     - the trap and `exit "$run_status"` lines are still present.
   - INT-002: call `_settle_audit` twice against a real `ClaimStore` for three runs:
     - a host run with metrics and no `audit.json`;
     - a host run with a failed `audit.json`;
     - an `and-scene` eval run with collected metrics, where `shutil.which` points at a
       Runner that fails if it is executed.

     Assert that there are no pending events, that no new `audit.json` was written, that
     the existing `audit.json` is unchanged, and that the Runner was never executed.
   - INT-003: `_audit_lines` returns nothing for a recent run that has metrics and no
     `audit.json`. Parametrize the existing recorded-failure status test over both switch
     values.
   - INT-004: `audit.readiness(None | "/bin/agent-runner", connection=<absent>, run=<fails if
     called>)` returns `(True, <"disabled" … #60>, "")`. Assert also that the `doctor` `post-run
     audit` diagnostic is available.
   - Leave `tests/integration/test_watch_session.py` and other tests that mention
     `audit.log` unchanged.

### Done when

- Every requirement and scenario in both delta specs is implemented.
- With the switch at its shipped value `False`:
  - a host fix, feature, or review attempt runs no audit replay and writes no `audit.json`;
  - no `post-run-audit` events are posted;
  - `status` lists no synthesized missing audits;
  - the `doctor` audit check passes without the Runner probe or the connection.
- Setting `AUDIT_ENABLED = True` is the only edit needed to restore the factory-owned
  behavior, and the existing audit tests prove that under the autouse fixture.
- INT-001 to INT-005 pass, and the full suite passes under `uv run pytest`, including the
  unchanged `tests/e2e/`.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run pyright`, and `uv build`
  pass. `agent-validate run` passes.
- No other behavior changes, and no audit code or tests are deleted.
