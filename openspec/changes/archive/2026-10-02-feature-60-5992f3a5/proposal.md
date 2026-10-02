## Why

Every factory run currently ends with a post-run development audit. Host fix, feature and
review attempts replay it with `agent-runner audit replay`, and the resident delivers an
eval's collected reports to the metrics Sheet. Each audit adds a model session's cost and
up to 45 minutes of wall-clock time per attempt. It also brings an operational dependency:
the Sheet connection, and a Runner built with `dev_audit`. We have not shown that the
step-value metrics justify that cost, so Paul wants audits off until he decides (issue #60).

Agent Runner is turning off its automatic audit hook in `dev_audit` builds
(Codagent-AI/agent-runner#191). Once that change ships, the factory's own audit hooks become
the only audits left. They would also start misreporting. `_settle_audit` would post an
"audit did not deliver" `post-run-audit` event on every run, and readiness would still
require a connection nobody needs. The factory side must be switched off too.

This is temporary. Turning the factory's audits back on must be a one-line revert, so the
code, tests and documentation of the audit path stay in place. The factory switch governs
only the triggers and settlement the factory owns. Eval audits run in their sandbox through
Agent Runner's automatic hook, and so does any audit a Runner starts by itself. That hook is
controlled by agent-runner#191. Fully restoring eval audits therefore also needs that hook
back on in the pinned Runner revision.

## What Changes

- The existing module switch `agent_factory.audit.AUDIT_ENABLED` becomes the single switch
  for all factory post-run audits. Today it gates only the watch session's audit. It stays
  `False`, with a comment that links issue #60 and states that the disable is temporary.
- When the switch is off:
  - The host launch wrapper used by fix, feature and review attempts no longer runs
    `python -m agent_factory.audit host ... || true`. It runs no `agent-runner audit
    replay` and writes no `audit.json` to the evidence. Agent Runner's own execution
    `audit.log` in `agent-runner-session/` is not a post-run audit artifact. It stays,
    because the feature workflow reads it (`record-archive-block.sh`). The issue's
    acceptance criterion "its evidence has no audit log" is read as: there is no post-run
    audit outcome (`audit.json`) and no replay run.
  - The resident's `_settle_audit` returns without settling. It delivers no eval reports,
    writes no `audit.json`, and posts no `post-run-audit` issue event.
  - `status` stops synthesizing a "missing" post-run audit line for a recent attempt
    that has `run-metrics.json` but no `audit.json` (`operations._audit_lines`). Without
    this, every new host run would be listed as unaudited for seven days. Outcomes already
    recorded in `audit.json` are still listed.
  - `audit.readiness` reports available without probing `agent-runner audit` or the
    reporting connection. The `doctor` "post-run audit" line says audits are disabled
    (#60) and does not depend on the connection.
- When the switch is on, the factory-owned behavior is exactly what it is today. Eval
  audits additionally depend on Agent Runner's automatic hook (agent-runner#191). While
  the switch is off, a claim pinned to an older Runner revision may still produce
  Runner-side audits, but the resident neither delivers nor reports them.
- `docs/operations.md` "Post-run audits" says that audits are temporarily disabled by
  `AUDIT_ENABLED` and describes how to re-enable them.

No public interface or persisted format changes. Existing `audit.json` files and recorded
events are untouched, and `status` keeps listing recorded outcomes.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `factory-operations`: add a requirement that one audit switch governs factory post-run
  audits. When the switch is off, host attempts run no audit replay, the resident records
  no audit outcome and posts no `post-run-audit` event, `status` lists no synthesized
  missing audits, and `doctor` readiness does not depend on the audit connection or Runner
  audit support.
- `factory-watch-dispatch`: the watch-session audit requirement names the same factory
  audit switch instead of "pending Codagent-AI/agent-factory#60". Its behavior does not
  change.

## Technical Approach

Route all three trigger points through `audit.AUDIT_ENABLED`, the pattern
`watch/session.py` already uses:

1. `work_kinds/pull_request/launch.py`: build the wrapper's audit line only when the switch
   is on, as `watch/session.py` builds `audit_command`. The rest of the wrapper is
   unchanged. `run_status` is still captured and returned, and the exit trap still restores
   the tracked config.
2. `runtime.py` `_settle_audit`: return immediately when the switch is off. The guard goes
   in the caller, not in `audit.settle`, so `settle` and its tests keep their meaning for
   re-enabling.
3. `audit.readiness`: when the switch is off, return
   `(True, "post-run audits are disabled (Codagent-AI/agent-factory#60)", "")` before any
   probe.
4. `operations._audit_lines`: when the switch is off, skip the missing-audit fallback for a
   run without `audit.json`. Recorded summaries are still read and listed.

Tests read the switch through `audit.AUDIT_ENABLED` and use `monkeypatch`, as
`test_watch_session.py` does. Existing audit tests run with the switch forced on, so they
keep covering the code that re-enabling would restore. New tests cover the off state: the
wrapper has no audit-replay line, `_settle_audit` records no event, readiness passes
without a connection, and `status` lists no missing audit for a finished host run that has
`run-metrics.json` and no `audit.json`. The tests check that no replay ran, not that
`audit.log` is absent. Re-enabling means setting `AUDIT_ENABLED = True`.

The evidence progress globs that match `audit.log` stay. Agent Runner still writes that
execution log, and the glob tracks progress, not post-run audits.

## Out of Scope

- Deleting audit code, tests or documentation, or changing how audits work when they are
  enabled.
- The Agent Runner side (Codagent-AI/agent-runner#191), including audits inside Fly guests
  and eval sandboxes, which only the Runner's hook triggers.
- A configuration-file or environment toggle. The issue asks for the smallest change, and a
  code constant keeps re-enabling a reviewed, one-line revert.
- Deciding whether audits come back, and recovering or replaying audits for past runs.
- Deploying. factory-watcher-5 deploys after merge.

## Impact

- Code: `src/agent_factory/audit.py` (switch comment, `readiness`),
  `src/agent_factory/work_kinds/pull_request/launch.py` (host wrapper),
  `src/agent_factory/runtime.py` (`_settle_audit`),
  `src/agent_factory/operations.py` (`_audit_lines`).
- Tests: `tests/integration/test_post_run_audit.py`, `tests/integration/test_host_launch.py`,
  status tests for unaudited attempts, and any doctor or readiness test that expects a
  probe.
- Docs: `docs/operations.md` "Post-run audits", and the `factory-operations` and
  `factory-watch-dispatch` specs.
- Operations: host attempts end sooner and cost less. No new `post-run-audit` issue events
  are posted, and `status` lists no new unaudited attempts. Doctor no longer flags a
  missing Sheet connection. Eval reports collected in a sandbox are no longer delivered to
  the Sheet.
