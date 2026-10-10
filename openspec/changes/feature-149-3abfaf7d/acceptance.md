# Implementation acceptance

Date: 2026-10-10. Environment: macOS, Python 3.12.13, installed Agent Runner
`/Users/paul/.local/bin/agent-runner` (version `dev`). Binary SHA-256:
`1d509b32b222256964a8411c186f3cf271d619cc5c7b0a6af91b19399a8a118e`.

## Required Runner proof

Command:

```sh
FEATURE_REQUIRE_RUNNER=1 uv run pytest tests/integration/test_feature_classification_order.py tests/integration/test_feature_workflow_catalog.py
```

Result: **19 passed in 35.45s**, no skips. Full output: [acceptance-run.txt](acceptance-run.txt).
The eight INT-002 cases use the installed Runner, real classification verification and inline
repair, and a stub `claude` behind a project lead profile. Finalization adds a local CI-fix commit;
classification sees and names it with diff size and a `Tests:` sentence. No model CLI, real GitHub,
Fly, Docker, or live factory state is used.

## Other checks

- `uv run pytest`: **1606 passed in 142.06s**. Output: [full-suite-run.txt](full-suite-run.txt).
- `uv run pytest tests/integration/test_feature_workflow_scripts.py -n 4 -q`:
  **156 passed in 47.97s**, including INT-003's malformed records, rename failures, CI combinations,
  and empty-reasons regressions.
- `uv run ruff format --check .`: passed.
- `uv run ruff check .`: passed.
- `uv run pyright`: zero errors and warnings.
- `openspec validate --specs --strict`: all 21 specifications passed.
- `openspec validate feature-149-3abfaf7d --strict`: passed.
- `git diff --check`: passed.
- Compared with `origin/main`: classify prompt and inline repair unchanged; fix and task workflows
  unchanged. Doctor stages the shared catalog, so no extra enumerated script list needs updating.

The initial full-suite run overlapped other test jobs and timed out in two existing fixture-admission
E2E subprocesses (15-second limit): `test_fixture_request_runs_and_reports_through_tick` and
`test_unpublished_fixture_waits_then_admits_after_push`. Both passed in isolation (2 passed in
13.06s); the subsequent full run without overlapping test jobs passed all 1606 tests. No unrelated
test or timeout changes were made.

Agent Validator was deliberately not run, as instructed; it belongs to the later workflow step.

## PR handoff

[pr-description.md](pr-description.md) includes the required orange item: CI does not run INT-002
or the Runner-validated catalog test; the recorded `FEATURE_REQUIRE_RUNNER=1` acceptance run is
their proof.
