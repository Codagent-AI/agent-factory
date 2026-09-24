"""Every factory run is audited and an undelivered audit is reported, never hidden."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_factory import audit
from agent_factory.store import ClaimDraft, ClaimStore

SESSION = "exec-1"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _source(root: Path, *sessions: tuple[str, str]) -> Path:
    source = root / "agent-runner-session"
    _write(
        source / audit.METRICS_FILE,
        {"sessions": [{"execution_session_id": s, "status": st} for s, st in sessions]},
    )
    return source


def _link(
    source: Path,
    audit_id: str,
    *,
    state: str = "completed",
    delivery: str | None = None,
    warning: str = "",
    destination: str = "configured",
) -> Path:
    lifecycle_path = source / audit.LIFECYCLE_FILE
    links: list[dict[str, str]] = (
        json.loads(lifecycle_path.read_text())["links"] if lifecycle_path.exists() else []
    )
    links.append(
        {
            "audit_run_id": audit_id,
            "execution_session_id": SESSION,
            "trigger": "replay",
            "state": state,
            "warning": warning,
        }
    )
    _write(lifecycle_path, {"links": links})
    audit_dir = source.parent / audit_id
    audit_dir.mkdir(parents=True, exist_ok=True)
    if delivery is not None:
        _write(
            audit_dir / "local-report.json",
            {"delivery_state": delivery, "destination": {"state": destination}},
        )
    return audit_dir


class FakeRunner:
    """Records Runner calls; replay links a delivered audit, retry delivers a report."""

    def __init__(self, source: Path, *, replay_delivery: str = "delivered") -> None:
        self.source = source
        self.replay_delivery = replay_delivery
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        self.calls.append(args)
        if args[1:3] == ["audit", "replay"]:
            audit_id = f"audit-{len(self.calls):04d}"
            _link(self.source, audit_id, delivery=self.replay_delivery)
            return subprocess.CompletedProcess(args, 0, f"{audit_id}\n", "")
        if args[1:3] == ["audit", "retry"]:
            report = Path(args[3]) / "local-report.json"
            _write(report, {"delivery_state": "delivered", "destination": {"state": "configured"}})
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")


def test_finalized_session_prefers_the_only_or_last_closed_session(tmp_path: Path) -> None:
    only = _source(tmp_path / "a", ("one", "open"))
    several = _source(tmp_path / "b", ("first", "closed"), ("second", "closed"), ("third", "open"))
    assert audit.finalized_execution_session(only) == "one"
    assert audit.finalized_execution_session(several) == "second"
    assert audit.finalized_execution_session(tmp_path / "missing") is None


def test_completed_link_without_a_report_is_a_failed_audit(tmp_path: Path) -> None:
    source = _source(tmp_path, (SESSION, "closed"))
    _link(source, "audit-delivered", delivery="delivered")
    _link(source, "audit-pending", delivery="pending")
    _link(source, "audit-warning", warning="value-audit failed: argument list too long")
    _link(source, "audit-running", state="started")

    outcomes = {o.audit_run_id: (o.outcome, o.reason) for o in audit.link_outcomes(source)}

    assert outcomes["audit-delivered"] == (audit.DELIVERED, "")
    assert outcomes["audit-pending"][0] == audit.PENDING
    assert outcomes["audit-warning"] == (
        audit.FAILED,
        "value-audit failed: argument list too long",
    )
    assert outcomes["audit-running"] == (audit.ACTIVE, "")


def test_host_audit_replays_with_the_clone_and_records_delivery(tmp_path: Path) -> None:
    evidence = tmp_path / "attempt-1"
    source = _source(evidence, (SESSION, "closed"))
    runner = FakeRunner(source)

    summary = audit.host_audit(
        "/bin/agent-runner",
        source,
        tmp_path / "clone",
        evidence,
        run=runner,
        sleep=lambda _: None,
    )

    assert summary["outcome"] == audit.DELIVERED
    assert runner.calls == [
        [
            "/bin/agent-runner",
            "audit",
            "replay",
            str(source),
            "--session",
            SESSION,
            "--project",
            str(tmp_path / "clone"),
        ]
    ]
    recorded = json.loads((evidence / audit.AUDIT_FILE).read_text())
    assert recorded["outcome"] == audit.DELIVERED
    assert "factory-audit:" in (evidence / audit.SUITE_LOG).read_text()


def test_host_audit_skips_a_session_already_delivered(tmp_path: Path) -> None:
    evidence = tmp_path / "attempt-1"
    source = _source(evidence, (SESSION, "closed"))
    _link(source, "audit-done", delivery="delivered")
    runner = FakeRunner(source)

    summary = audit.host_audit(
        "/bin/agent-runner", source, tmp_path, evidence, run=runner, sleep=lambda _: None
    )

    assert summary["outcome"] == audit.DELIVERED
    assert runner.calls == []


def test_host_audit_retries_a_pending_delivery(tmp_path: Path) -> None:
    evidence = tmp_path / "attempt-1"
    source = _source(evidence, (SESSION, "closed"))
    runner = FakeRunner(source, replay_delivery="pending")

    summary = audit.host_audit(
        "/bin/agent-runner", source, tmp_path, evidence, run=runner, sleep=lambda _: None
    )

    assert summary["outcome"] == audit.DELIVERED
    assert [call[2] for call in runner.calls] == ["replay", "retry"]


def test_host_audit_reports_a_failed_replay_without_raising(tmp_path: Path) -> None:
    evidence = tmp_path / "attempt-1"
    source = _source(evidence, (SESSION, "closed"))

    def failing(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 1, "", "recorded source project is gone")

    summary = audit.host_audit(
        "/bin/agent-runner", source, tmp_path, evidence, run=failing, sleep=lambda _: None
    )

    assert summary["outcome"] == audit.MISSING
    assert "recorded source project is gone" in str(summary["reason"])
    assert json.loads((evidence / audit.AUDIT_FILE).read_text())["outcome"] == audit.MISSING


def test_host_audit_gives_up_at_its_deadline(tmp_path: Path) -> None:
    evidence = tmp_path / "attempt-1"
    source = _source(evidence, (SESSION, "closed"))

    def replay_active(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        _link(source, "audit-slow", state="started")
        return subprocess.CompletedProcess(list(argv), 0, "audit-slow\n", "")

    ticks = iter(range(0, 10_000, 600))
    summary = audit.host_audit(
        "/bin/agent-runner",
        source,
        tmp_path,
        evidence,
        timeout_seconds=1200,
        run=replay_active,
        sleep=lambda _: None,
        clock=lambda: float(next(ticks)),
    )

    assert summary["outcome"] == audit.ACTIVE
    assert "did not finish before the audit deadline" in str(summary["reason"])


def test_collected_eval_report_is_delivered_to_the_hosts_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = tmp_path / "home" / ".agent-runner" / "development-audit-connection.json"
    _write(connection, {"spreadsheet_id": "sheet-id", "tab": "step_value_v1"})
    monkeypatch.setattr(audit, "CONNECTION_FILE", connection)
    evidence = tmp_path / "rep-1"
    runs = evidence / ".runtime" / "agent-runner-projects" / "-artifacts-worktree" / "runs"
    source = runs / "implement-change-1"
    _write(source / audit.METRICS_FILE, {"sessions": [{"execution_session_id": SESSION}]})
    _link(source, "audit-1", delivery="pending", destination="unavailable")
    runner = FakeRunner(source)

    summary = audit.deliver_collected("/bin/agent-runner", evidence, run=runner)

    assert summary["outcome"] == audit.DELIVERED
    assert runner.calls == [
        [
            "/bin/agent-runner",
            "audit",
            "retry",
            str(runs / "audit-1"),
            "--migrate-spreadsheet",
            "sheet-id",
            "--migrate-tab",
            "step_value_v1",
        ]
    ]
    assert json.loads((evidence / audit.AUDIT_FILE).read_text())["outcome"] == audit.DELIVERED


def test_eval_whose_sandbox_never_linked_an_audit_is_flagged(tmp_path: Path) -> None:
    evidence = tmp_path / "rep-1"
    source = evidence / ".runtime" / "agent-runner-projects" / "p" / "runs" / "implement-1"
    _write(source / audit.METRICS_FILE, {"sessions": [{"execution_session_id": SESSION}]})

    summary = audit.deliver_collected("/bin/agent-runner", evidence, run=FakeRunner(source))

    assert summary["outcome"] == audit.MISSING
    assert "no linked audit" in str(summary["reason"])


def test_settle_ignores_attempts_that_never_started_a_workflow(tmp_path: Path) -> None:
    assert audit.settle(tmp_path, eval_suite=True, runner="/bin/agent-runner") is None
    assert audit.settle(tmp_path, eval_suite=False, runner=None) is None
    _source(tmp_path, (SESSION, "closed"))
    missing = audit.settle(tmp_path, eval_suite=False, runner=None)
    assert missing is not None and missing["outcome"] == audit.MISSING


def test_event_body_only_for_undelivered_audits(tmp_path: Path) -> None:
    assert audit.event_body({"outcome": audit.DELIVERED}, tmp_path) is None
    body = audit.event_body({"outcome": audit.FAILED, "reason": "value-audit failed"}, tmp_path)
    assert body is not None
    assert "value-audit failed" in body and "result is unaffected" in body


@pytest.mark.parametrize(
    ("usage", "available", "detail"),
    [
        ('workflow "audit" not found', False, "without development audits"),
        ("audit replay <session-dir> --session <id>", False, "--project"),
        ("audit replay <session-dir> --session <id> [--project <dir>]", True, "audits factory"),
    ],
)
def test_readiness_names_the_missing_audit_capability(
    tmp_path: Path, usage: str, available: bool, detail: str
) -> None:
    connection = tmp_path / "state" / "development-audit-connection.json"
    _write(connection, {})
    connection.parent.chmod(0o700)
    connection.chmod(0o600)

    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, usage, "")

    ok, text, _ = audit.readiness("/bin/agent-runner", connection=connection, run=runner)
    assert ok is available
    assert detail in text


def test_readiness_requires_a_private_reporting_connection(tmp_path: Path) -> None:
    def runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(argv), 0, "audit replay [--project <dir>]", "")

    connection = tmp_path / "state" / "development-audit-connection.json"
    ok, text, _ = audit.readiness("/bin/agent-runner", connection=connection, run=runner)
    assert not ok and "no reporting connection" in text
    _write(connection, {})
    connection.parent.chmod(0o755)
    connection.chmod(0o644)
    ok, text, _ = audit.readiness("/bin/agent-runner", connection=connection, run=runner)
    assert not ok and "permissions" in text


def test_consumed_attempt_with_an_undelivered_audit_is_reported_once(tmp_path: Path) -> None:
    from agent_factory import runtime

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft("example/work", 1, "I1", "P1", "ghost", "fp", {"version": 1})
    )
    run = store.reserve_run(
        claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "evidence")
    )
    evidence = Path(run.evidence_path)
    _source(evidence, (SESSION, "closed"))
    audit.write_summary(evidence, {"outcome": audit.FAILED, "reason": "value-audit failed"})

    runtime._settle_audit(store, claim, run)  # pyright: ignore[reportPrivateUsage]
    runtime._settle_audit(store, claim, run)  # pyright: ignore[reportPrivateUsage]

    events = [event.body for event in store.pending_events(claim.id)]
    assert len(events) == 1
    assert "value-audit failed" in events[0]
    store.close()


def test_host_wrapper_audits_before_restoring_config_and_keeps_the_run_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.integration.test_host_launch import Built

    built = Built(tmp_path, monkeypatch)
    calls = tmp_path / "calls.jsonl"
    # A stand-in Runner: "run" records a session and fails; "audit replay" records what it
    # saw and links a delivered audit.
    built.runner.write_text(
        f"""#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
with open({str(calls)!r}, "a") as log:
    log.write(json.dumps({{
        "args": args,
        "gh_token": os.environ.get("GH_TOKEN"),
        "factory_profile": "factory:" in pathlib.Path(".agent-runner/config.yaml").read_text(),
    }}) + "\\n")
if args[0] == "run":
    session = pathlib.Path(args[args.index("--session-dir") + 1])
    session.mkdir(parents=True, exist_ok=True)
    (session / "run-metrics.json").write_text(json.dumps(
        {{"sessions": [{{"execution_session_id": "{SESSION}", "status": "closed"}}]}}))
    sys.exit(3)
if args[:2] == ["audit", "replay"]:
    session = pathlib.Path(args[2])
    (session / "audit-lifecycle.json").write_text(json.dumps({{"links": [{{
        "audit_run_id": "audit-1", "execution_session_id": "{SESSION}",
        "trigger": "replay", "state": "completed"}}]}}))
    report = session.parent / "audit-1" / "local-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({{"delivery_state": "delivered"}}))
    print("audit-1")
"""
    )

    done = subprocess.run(
        ["/bin/bash", str(built.wrapper)], capture_output=True, text=True, check=False
    )

    assert done.returncode == 3, done.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    replay = next(r for r in recorded if r["args"][:2] == ["audit", "replay"])
    assert replay["args"][-2:] == ["--project", str(built.clone.resolve())]
    assert replay["gh_token"] is None
    assert replay["factory_profile"] is True
    assert json.loads((built.evidence / audit.AUDIT_FILE).read_text())["outcome"] == "delivered"
