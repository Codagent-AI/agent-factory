"""Post-run development audits: every factory run's step-value metrics must reach the Sheet.

Agent Runner auto-audits only ``openspec/`` and ``spec-driven/`` workflows, so the factory
owns the audit of its own runs. A host fix or review attempt replays the audit inside its
launch wrapper, while the attempt's staged profile config is still in the clone
(``python -m agent_factory.audit host``). An eval audits inside its sandbox, which holds
no reporting connection, so the resident delivers the collected reports from the host
when it consumes the attempt (:func:`deliver_collected`).

Either path leaves ``audit.json`` in the attempt's evidence. The audit never changes the
attempt's own outcome: an undelivered audit is reported through :func:`settle`, which
the resident records as an issue event.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

AUDIT_FILE = "audit.json"
LIFECYCLE_FILE = "audit-lifecycle.json"
METRICS_FILE = "run-metrics.json"
CONNECTION_FILE = Path.home() / ".agent-runner" / "development-audit-connection.json"
SUITE_LOG = "factory-suite.log"
COLLECTED_RUNS_GLOB = ".runtime/agent-runner-projects/*/runs/*/" + METRICS_FILE
HOST_SESSION_DIR = "agent-runner-session"

DELIVERED = "delivered"
PENDING = "pending-delivery"
FAILED = "failed"
ACTIVE = "active"
MISSING = "missing"

DEFAULT_TIMEOUT_SECONDS = 45 * 60
DEFAULT_POLL_SECONDS = 15.0
REPLAY_ATTEMPTS = 2
# The resident delivers eval reports synchronously while consuming results.
RETRY_TIMEOUT_SECONDS = 120

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), capture_output=True, text=True, check=False, timeout=RETRY_TIMEOUT_SECONDS
    )


@dataclass(frozen=True)
class LinkOutcome:
    audit_run_id: str
    execution_session_id: str
    audit_dir: str
    outcome: str
    reason: str = ""

    def document(self) -> dict[str, str]:
        return {
            "audit_run_id": self.audit_run_id,
            "execution_session_id": self.execution_session_id,
            "audit_dir": self.audit_dir,
            "outcome": self.outcome,
            "reason": self.reason,
        }


def _read_json(path: Path) -> Mapping[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def finalized_execution_session(session_dir: Path) -> str | None:
    """The execution session an audit judges: the only one, else the last closed one."""
    metrics = _read_json(session_dir / METRICS_FILE)
    sessions = metrics.get("sessions") if metrics is not None else None
    if not isinstance(sessions, list):
        return None
    records = [
        cast(Mapping[str, object], s)
        for s in cast(list[object], sessions)
        if isinstance(s, Mapping)
    ]
    ids = [(_text(r.get("execution_session_id")), _text(r.get("status"))) for r in records]
    ids = [(session, status) for session, status in ids if session]
    if not ids:
        return None
    if len(ids) == 1:
        return ids[0][0]
    closed = [session for session, status in ids if status == "closed"]
    return closed[-1] if closed else ids[-1][0]


def link_outcomes(source_dir: Path) -> list[LinkOutcome]:
    """Each linked audit's outcome, read from the durable files the Runner writes.

    The lifecycle marks a linked audit ``completed`` even when a stage failed and no rows
    were delivered; only the audit's ``local-report.json`` says whether the Sheet has them.
    """
    lifecycle = _read_json(source_dir / LIFECYCLE_FILE)
    links = lifecycle.get("links") if lifecycle is not None else None
    if not isinstance(links, list):
        return []
    outcomes: list[LinkOutcome] = []
    for raw in cast(list[object], links):
        if not isinstance(raw, Mapping):
            continue
        link = cast(Mapping[str, object], raw)
        audit_id = _text(link.get("audit_run_id"))
        if not audit_id:
            continue
        audit_dir = source_dir.parent / audit_id
        state = _text(link.get("state"))
        warning = _text(link.get("warning"))
        outcome, reason = _outcome(state, warning, _text(link.get("reporting_warning")), audit_dir)
        outcomes.append(
            LinkOutcome(
                audit_id, _text(link.get("execution_session_id")), str(audit_dir), outcome, reason
            )
        )
    return outcomes


def _outcome(state: str, warning: str, reporting: str, audit_dir: Path) -> tuple[str, str]:
    if state in {"reserved", "launching", "started"}:
        return ACTIVE, ""
    failure = _text((_read_json(audit_dir / "state.json") or {}).get("failureReason"))
    if state == "failed":
        return FAILED, warning or failure or "audit launch failed"
    report_path = audit_dir / "local-report.json"
    report = _read_json(report_path)
    if report is None and report_path.exists():
        return FAILED, f"local report is unreadable: {report_path}"
    delivery = _text(report.get("delivery_state")) if report is not None else ""
    if delivery == "delivered":
        return DELIVERED, ""
    if delivery == "pending":
        error = _text(report.get("delivery_error")) if report is not None else ""
        return PENDING, reporting or error or "Sheets reporting pending"
    return FAILED, warning or failure or "audit finished without a local report"


def retry_delivery(runner: str, audit_dir: Path, *, run: Runner = _run) -> str:
    """Deliver a finished report from this machine; returns an error, or "" on success.

    A report assembled where no reporting connection existed (an eval sandbox) froze an
    unconfigured destination, so it is pointed at this machine's configured Sheet first.
    """
    argv = [runner, "audit", "retry", str(audit_dir)]
    report = _read_json(audit_dir / "local-report.json") or {}
    destination = report.get("destination")
    state = (
        _text(cast(Mapping[str, object], destination).get("state"))
        if isinstance(destination, Mapping)
        else ""
    )
    connection = _read_json(CONNECTION_FILE)
    if state != "configured" and connection is not None:
        spreadsheet, tab = _text(connection.get("spreadsheet_id")), _text(connection.get("tab"))
        if spreadsheet and tab:
            argv += ["--migrate-spreadsheet", spreadsheet, "--migrate-tab", tab]
    try:
        completed = run(argv)
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)
    if completed.returncode != 0:
        return (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
    return ""


def summarize(
    outcomes: Iterable[LinkOutcome], *, expected: Iterable[str] = ()
) -> dict[str, object]:
    """One attempt's audit summary: delivered only when every audited session delivered."""
    values = list(outcomes)
    sessions = {session for session in expected if session} | {
        o.execution_session_id for o in values
    }
    reasons: list[str] = []
    overall = DELIVERED if sessions else MISSING
    rank = {DELIVERED: 0, ACTIVE: 1, PENDING: 2, FAILED: 3, MISSING: 4}
    for session in sorted(sessions):
        linked = [o for o in values if o.execution_session_id == session]
        if any(o.outcome == DELIVERED for o in linked):
            continue
        worst = linked[-1] if linked else LinkOutcome("", session, "", MISSING, "no linked audit")
        if rank[worst.outcome] > rank[overall]:
            overall = worst.outcome
        reasons.append(f"{session or 'unknown session'}: {worst.outcome}: {worst.reason}")
    if not sessions:
        reasons.append("no linked audit was recorded")
    return {
        "outcome": overall,
        "reason": "; ".join(reasons),
        "audits": [o.document() for o in values],
        "updated_at": datetime.now(UTC).isoformat(),
    }


def write_summary(evidence: Path, summary: Mapping[str, object]) -> None:
    path = evidence / AUDIT_FILE
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_summary(evidence: Path) -> Mapping[str, object] | None:
    return _read_json(evidence / AUDIT_FILE)


def _log(evidence: Path, message: str) -> None:
    line = f"factory-audit: {message}\n"
    try:
        with (evidence / SUITE_LOG).open("a", encoding="utf-8") as stream:
            stream.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


def host_audit(
    runner: str,
    session_dir: Path,
    project: Path,
    evidence: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    run: Runner = _run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Replay the audit for a finished host attempt and wait until its rows are delivered."""
    invoke = run
    session = finalized_execution_session(session_dir)
    if session is None:
        summary = summarize([])
        summary.update(outcome=FAILED, reason="the run recorded no execution session")
        write_summary(evidence, summary)
        _log(evidence, str(summary["reason"]))
        return summary
    deadline = clock() + timeout_seconds
    errors: list[str] = []
    for attempt in range(1, REPLAY_ATTEMPTS + 1):
        linked = [o for o in link_outcomes(session_dir) if o.execution_session_id == session]
        if any(o.outcome == DELIVERED for o in linked):
            break
        pending = next((o for o in reversed(linked) if o.outcome == PENDING), None)
        if pending is not None:
            error = retry_delivery(runner, Path(pending.audit_dir), run=invoke)
            if not error:
                continue
            errors.append(f"retry {pending.audit_run_id}: {error}")
        _log(evidence, f"replaying audit for session {session} (attempt {attempt})")
        try:
            replay = invoke(
                [runner, "audit", "replay", str(session_dir), "--session", session]
                + ["--project", str(project)]
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(f"replay: {error}")
            _log(evidence, f"audit replay failed: {error}")
            continue
        audit_id = replay.stdout.strip().splitlines()[-1] if replay.stdout.strip() else ""
        if replay.returncode != 0 or not audit_id:
            errors.append(
                (replay.stderr or replay.stdout).strip() or f"replay exited {replay.returncode}"
            )
            _log(evidence, f"audit replay failed: {errors[-1]}")
            continue
        outcome = _wait(session_dir, audit_id, evidence, deadline, poll_seconds, sleep, clock)
        if outcome is not None and outcome.outcome == PENDING:
            error = retry_delivery(runner, Path(outcome.audit_dir), run=invoke)
            if error:
                errors.append(f"retry {audit_id}: {error}")
        if outcome is None:
            errors.append(f"audit {audit_id} did not finish before the audit deadline")
            break
        if clock() >= deadline:
            break
    outcomes = [o for o in link_outcomes(session_dir) if o.execution_session_id == session]
    summary = summarize(outcomes, expected=[session])
    if errors and summary["outcome"] != DELIVERED:
        summary["reason"] = "; ".join([str(summary["reason"]), *errors]).strip("; ")
    write_summary(evidence, summary)
    _log(
        evidence,
        f"audit {summary['outcome']}" + (f": {summary['reason']}" if summary["reason"] else ""),
    )
    return summary


def _wait(
    session_dir: Path,
    audit_id: str,
    evidence: Path,
    deadline: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> LinkOutcome | None:
    """Poll one linked audit until it is no longer active; ``None`` at the deadline."""
    while True:
        current = next((o for o in link_outcomes(session_dir) if o.audit_run_id == audit_id), None)
        if current is not None and current.outcome != ACTIVE:
            return current
        if clock() >= deadline:
            return None
        # Each poll is progress for the supervisor's inactivity watch.
        _log(evidence, f"waiting for audit {audit_id}")
        sleep(poll_seconds)


def deliver_collected(runner: str, evidence: Path, *, run: Runner = _run) -> dict[str, object]:
    """Deliver an eval's sandbox-assembled reports from the host and record the outcome."""
    errors: list[str] = []
    outcomes: list[LinkOutcome] = []
    expected: list[str] = []
    for source in collected_sources(evidence):
        session = finalized_execution_session(source)
        if session:
            expected.append(session)
        for outcome in link_outcomes(source):
            if outcome.outcome == PENDING:
                error = retry_delivery(runner, Path(outcome.audit_dir), run=run)
                if error:
                    errors.append(f"retry {outcome.audit_run_id}: {error}")
        outcomes.extend(link_outcomes(source))
    summary = summarize(outcomes, expected=expected)
    if errors and summary["outcome"] != DELIVERED:
        summary["reason"] = "; ".join([str(summary["reason"]), *errors]).strip("; ")
    write_summary(evidence, summary)
    return summary


def collected_sources(evidence: Path) -> list[Path]:
    """Source runs an eval's sandbox left in its evidence; linked audit runs are excluded."""
    return [
        metrics.parent
        for metrics in sorted(evidence.glob(COLLECTED_RUNS_GLOB))
        if not metrics.parent.name.startswith("audit-")
    ]


def settle(evidence: Path, *, eval_suite: bool, runner: str | None) -> Mapping[str, object] | None:
    """The attempt's audit outcome, delivering an eval's collected reports first.

    ``None`` means the workflow never started, so there is nothing to audit. Never
    raises: an audit problem is reported, and never changes the attempt's result.
    """
    try:
        if eval_suite:
            if not collected_sources(evidence):
                return None
            existing = read_summary(evidence)
            if existing is not None and existing.get("outcome") == DELIVERED:
                return existing
            if runner is None:
                return {"outcome": FAILED, "reason": "agent-runner is not on PATH"}
            return deliver_collected(runner, evidence)
        if not (evidence / HOST_SESSION_DIR / METRICS_FILE).is_file():
            return None
        summary = read_summary(evidence)
        if summary is None:
            return {"outcome": MISSING, "reason": "the attempt recorded no post-run audit"}
        return summary
    except Exception as error:  # noqa: BLE001 - audit problems are reported, never raised
        return {"outcome": FAILED, "reason": f"audit settlement failed: {error}"}


def event_body(summary: Mapping[str, object], evidence: Path) -> str | None:
    """The issue event for an undelivered audit, or ``None`` when its rows were delivered."""
    outcome = _text(summary.get("outcome"))
    if outcome == DELIVERED:
        return None
    reason = _text(summary.get("reason")) or "no reason recorded"
    return (
        f"Post-run audit did not deliver step-value metrics to the Sheet ({outcome}): "
        f"{reason}\n\nThe attempt's own result is unaffected. Evidence: `{evidence}`. "
        "Recover with Agent Runner's `scripts/recover-development-audits.sh --execute "
        "--session <session-dir>:<execution-session-id>[:<project-dir>]`."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_factory.audit")
    commands = parser.add_subparsers(dest="command", required=True)
    host = commands.add_parser("host", help="audit a finished host attempt")
    host.add_argument("--runner", required=True)
    host.add_argument("--session-dir", required=True, type=Path)
    host.add_argument("--project", required=True, type=Path)
    host.add_argument("--evidence", required=True, type=Path)
    host.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    summary = host_audit(
        args.runner,
        args.session_dir,
        args.project,
        args.evidence,
        timeout_seconds=args.timeout_seconds,
    )
    return 0 if summary.get("outcome") == DELIVERED else 1


if __name__ == "__main__":
    sys.exit(main())


def _probe(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, check=False, timeout=30)


def readiness(
    runner: str | None,
    *,
    connection: Path = CONNECTION_FILE,
    run: Runner = _probe,
) -> tuple[bool, str, str]:
    """Whether this host can audit factory runs: ``(available, detail, action)``."""
    if runner is None:
        return False, "agent-runner is not on PATH", "Install Agent Runner on this Mac."
    try:
        usage = run([runner, "audit"])
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"{runner} audit: {error}", "Check the installed Agent Runner."
    text = f"{usage.stdout}\n{usage.stderr}"
    if "audit replay" not in text:
        return (
            False,
            f"{runner} was built without development audits",
            "Build Agent Runner with `make build` (the dev_audit tag) and install that binary.",
        )
    if "--project" not in text:
        return (
            False,
            f"{runner} cannot replay audits for factory sessions (no `audit replay --project`)",
            "Rebuild Agent Runner from main with `make build`.",
        )
    try:
        file_mode = connection.stat().st_mode & 0o777
        directory_mode = connection.parent.stat().st_mode & 0o777
    except OSError:
        return (
            False,
            f"no reporting connection at {connection}",
            "Run `agent-runner audit setup --client <file> --token <file> "
            "--spreadsheet <id> --tab <tab>`.",
        )
    if file_mode != 0o600 or directory_mode != 0o700:
        return (
            False,
            f"reporting connection permissions are {oct(file_mode)} in {oct(directory_mode)}",
            f"chmod 600 {connection} and chmod 700 {connection.parent}.",
        )
    return True, f"{runner} audits factory runs and reports to the configured Sheet", ""
