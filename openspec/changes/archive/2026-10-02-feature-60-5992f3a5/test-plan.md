## Coverage Strategy

Specifications remain the source of unit-test requirements. This plan records only additional
integration and end-to-end obligations, the acceptance testing envelope, and exceptional human-only
obligations.

This change adds guards at four call sites behind `audit.AUDIT_ENABLED`:

- the host wrapper text;
- result settlement;
- status lines;
- the doctor readiness check.

The risky boundaries are three:

- the generated bash wrapper running under real `/bin/bash` with a stand-in Runner, where the
  exit status and config restore must survive dropping the audit line;
- result consumption writing events into the real SQLite `ClaimStore`;
- `doctor` and `status` composition in `operations.py`.

Each one is covered by an integration test in `tests/integration/test_post_run_audit.py`.

The enabled path stays covered by the existing tests in that file. An autouse fixture sets the
switch to `True`, as `design.md` describes. INT-005 records that this fixture is an obligation.

No new end-to-end test is warranted. The CLI `tick`, `status`, and `doctor` journeys in
`tests/e2e/` already run the same functions, and the switch only removes work at those call
sites. Every observable result is already proven with real components at the integration layer:

- no replay subprocess;
- no `audit.json`;
- no `post-run-audit` event;
- no status line;
- a passing diagnostic.

A whole-CLI journey would duplicate those assertions at higher cost. The existing e2e suite must
still pass with the switch at its shipped value (`False`).

All tests run through the validator's `test` check (`uv run pytest`), next to `ruff` and
`pyright`.

## Integration Tests

### INT-001: Host wrapper runs no audit replay and keeps the workflow's exit status
- Covers: factory-operations "Govern post-run audits with one switch". Scenario: "A host
  attempt finishes with audits off".
- Boundary: `host_script()` output executed by real `/bin/bash`. A stand-in `agent-runner`
  executable records every call. This uses the `Built` fixture from
  `tests/integration/test_host_launch.py`.
- Setup:
  - Set `audit.AUDIT_ENABLED` to `False` before building the wrapper.
  - The stand-in Runner's `run` writes `agent-runner-session/run-metrics.json`, also writes an
    `agent-runner-session/audit.log` the way the real Runner does, and exits non-zero (3).
  - Any `audit` subcommand appends to the call log.
- Action: run the generated wrapper.
- Assertions:
  - The wrapper exits 3.
  - No recorded Runner call has `audit` as its first argument.
  - The wrapper text has no `agent_factory.audit host`.
  - `<evidence>/audit.json` does not exist.
  - The Runner's own `agent-runner-session/audit.log` is still present.
  - The wrapper still installs `trap restore_tracked_config EXIT` and still ends with
    `exit "$run_status"`.
  - If the clone fixture tracks `.agent-runner/config.yaml`, as in the tracked-config setup in
    `test_host_launch.py`, the file is back to its committed content after the run.
- Execution: `tests/integration/test_post_run_audit.py`, `uv run pytest`.

### INT-002: Result settlement posts no event and delivers nothing
- Covers: factory-operations "Govern post-run audits with one switch". Scenario: "The resident
  consumes an attempt with audits off".
- Boundary: `runtime._settle_audit` against a real `ClaimStore` (SQLite under `tmp_path`) and
  real evidence directories.
- Setup: `AUDIT_ENABLED` is `False`. Three runs:
  1. A host run with `agent-runner-session/run-metrics.json` and no `audit.json`.
  2. A host run with a pre-written failed `audit.json`.
  3. An eval run whose plan has `ownership_hints.suite = "and-scene"`, with collected metrics
     under `.runtime/agent-runner-projects/*/runs/*/run-metrics.json`.

  `shutil.which("agent-runner")` resolves to a stand-in that fails the test if executed.
- Action: settle each run twice.
- Assertions:
  - `store.pending_events(claim.id)` is empty for all three runs.
  - No `audit.json` is created where none existed.
  - The pre-written `audit.json` is unchanged.
  - The stand-in Runner is never executed.
- Execution: `tests/integration/test_post_run_audit.py`, `uv run pytest`.

### INT-003: Status lists only recorded audit outcomes while audits are off
- Covers: factory-operations "Govern post-run audits with one switch". Scenarios: "Status after a
  run with audits off" and "Status keeps earlier recorded outcomes".
- Boundary: `operations._audit_lines` over real `Run` records and evidence directories.
- Setup: `AUDIT_ENABLED` is `False`. Two runs finished within the 7-day window:
  - run A has `agent-runner-session/run-metrics.json` and no `audit.json`;
  - run B has a recorded `failed` `audit.json` with a reason.
- Action: compute the audit lines.
- Assertions:
  - There is no line for run A.
  - Run B has exactly its existing one-based line, `post-run audit: <repo>#<n> <unit> attempt
    <k>: failed — <reason>`.
  - The existing recorded-outcome test is parametrized over both switch values and passes under
    each.
- Execution: `tests/integration/test_post_run_audit.py`, `uv run pytest`.

### INT-004: Doctor's audit check passes without the Runner or the connection
- Covers: factory-operations "Govern post-run audits with one switch". Scenario: "Doctor without
  a reporting connection".
- Boundary:
  - `audit.readiness` with the injectable `run` probe and `connection` path.
  - The `post-run audit` `Diagnostic` composed by `operations.doctor(..., include_informational=True)`.
- Setup: `AUDIT_ENABLED` is `False`. There is no connection file, and the probe callable fails the
  test if invoked.
  - For the `doctor` composition, monkeypatch `operations.audit.readiness` inputs only as far as
    the existing doctor tests do.
  - Otherwise assert directly on `audit.readiness(None, ...)` and `audit.readiness("/bin/agent-runner", ...)`.
- Action: compute readiness.
- Assertions:
  - The result is `(True, detail, "")` with `detail` containing "disabled" and naming
    Codagent-AI/agent-factory#60.
  - The probe was never called.
  - Any `doctor` diagnostic named `post-run audit` is available.
- Execution: `tests/integration/test_post_run_audit.py`, `uv run pytest`.

### INT-005: Re-enabling restores the previous factory-owned audit behavior
- Covers: factory-operations "Govern post-run audits with one switch". Scenario: "Re-enabling
  audits".
- Boundary: the same boundaries as INT-001 to INT-004, with the switch set to `True`.
- Setup: a module-level autouse fixture in `test_post_run_audit.py` sets `AUDIT_ENABLED` to `True`.
  Disabled-state tests override it in their own body.
- Action: run the existing tests unchanged.
- Assertions: the existing tests pass without modification beyond the fixture and the INT-003
  parametrization:
  - `test_host_wrapper_audits_before_restoring_config_and_keeps_the_run_status`: replay runs
    with `--project`, without `GH_TOKEN`, under the factory profile, and records `delivered`.
  - `test_consumed_attempt_with_an_undelivered_audit_is_reported_once`.
  - Both readiness tests: probe failures and connection-permission failures are still reported.
  - The eval delivery and settle tests. These start from reports the sandbox's Runner already
    collected, so they prove only the factory's delivery of collected reports.
- Scope limit: this obligation does not claim that the factory switch alone restores eval
  audits. Producing eval reports depends on Agent Runner's automatic hook
  (agent-runner#191), which is outside this repository and is not tested here.
- Execution: `tests/integration/test_post_run_audit.py`, `uv run pytest`.

Watch-session behavior keeps its coverage in `tests/integration/test_watch_session.py`, which
already parametrizes the switch. No new obligation is needed there.

## End-to-End Tests

None. The Coverage Strategy explains why. The existing `tests/e2e/` suite must pass unchanged with
the shipped switch value.

## Acceptance Testing Envelope

- Environments and sandboxes:
  - This repository's feature clone and its `uv` virtualenv.
  - Temporary directories under the system temp root for evidence, clones, SQLite stores, and an
    isolated local config (`--config <tmp>/local.toml` with a temp `storage_root`).
  - A stand-in `agent-runner` script on a temp `PATH` for running a generated host wrapper.
  - `agent-factory doctor` and `agent-factory status` may be run against the isolated config.
- Credentials and secrets:
  - None are needed.
  - The real development-audit connection (`~/.agent-runner/development-audit-connection.json`)
    and any GitHub tokens must not be read, copied, or modified.
- Authorized effects: local files under temp directories only. Clean them up afterwards. No
  network calls, Sheet writes, GitHub writes, or Fly or Docker resources.
- Off limits:
  - The live service: `~/.agent-factory/` releases, state, config, artifacts, and clones other
    than this clone, the LaunchAgent, and `scripts/deploy.sh`. Deploying is factory-watcher-5's
    job after merge.
  - The real installed `agent-runner audit` commands.
  - Paul's checkout at `/Users/paul/codagent/agent-factory`.
  - Pausing or resuming the factory.
- Permitted substitutes:
  - A stand-in `agent-runner` executable for the real Runner.
  - An absent or temp connection path for the real reporting connection.
  - Monkeypatching `audit.AUDIT_ENABLED` to exercise the enabled path.
- Known risk areas:
  - Exit-status preservation and the tracked-config restore trap in the host wrapper after the
    audit line is dropped.
  - The eval path, where settlement previously delivered collected reports from the host.
  - Status must still show recorded outcomes from before the switch-off.
  - A missing Runner is reported by doctor checks other than the audit check.
  - Accepted limitations from `design.md` and `decisions.md`: watch dispatches still show
    `watch audit: <id> missing` status lines; eval reports are no longer delivered; an attempt
    launched by a pre-deploy release may still replay its audit, but its outcome is not posted;
    a claim pinned to a Runner revision from before agent-runner#191 may still produce
    Runner-side audits, but the resident neither delivers nor reports them.

## Human-Only Testing

None.

## Coverage Map

| Requirement or journey | INT | E2E | HT |
| --- | --- | --- | --- |
| Govern post-run audits with one switch: host attempt with audits off | INT-001 | — | — |
| Govern post-run audits with one switch: resident consumes an attempt with audits off | INT-002 | — | — |
| Govern post-run audits with one switch: status with audits off, recorded outcomes kept | INT-003 | — | — |
| Govern post-run audits with one switch: doctor without a reporting connection | INT-004 | — | — |
| Govern post-run audits with one switch: re-enabling audits | INT-005 | — | — |
