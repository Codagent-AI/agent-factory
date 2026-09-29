"""Probe detached watch process groups and finish each dispatch once."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agent_factory.audit import AUDIT_FILE, METRICS_FILE
from agent_factory.supervisor import (
    ProcessProbeError,
    process_identity_status,
    process_start_identity,
    terminate_owned_process,
)
from agent_factory.watch import deliver, result, session
from agent_factory.watch import store as watch_store
from agent_factory.work_kinds.pull_request.launch import SESSION_DIR_NAME

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig
    from agent_factory.store import ClaimStore


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return cast(dict[str, Any], value) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _usage(row: dict[str, Any], evidence: Path, finished: datetime) -> dict[str, object]:
    metrics = _read_json(evidence / SESSION_DIR_NAME / METRICS_FILE).get("totals", {})
    totals = cast(dict[str, Any], metrics) if isinstance(metrics, dict) else {}
    token_totals = totals.get("token_totals", {})
    tokens = cast(dict[str, Any], token_totals) if isinstance(token_totals, dict) else {}
    token_coverage = totals.get("token_total_coverage", "unavailable")
    cost_coverage = totals.get("cost_coverage", "unavailable")
    started_text = (
        (evidence / "started-at").read_text().strip()
        if (evidence / "started-at").is_file()
        else row["launched_at"]
    )
    try:
        started = datetime.fromisoformat(started_text.replace("Z", "+00:00"))
    except ValueError:
        started = datetime.fromisoformat(row["launched_at"])
    return {
        "profile": row["profile"],
        "started_at": started_text,
        "finished_at": finished.isoformat(),
        "duration_seconds": max(0, (finished - started).total_seconds()),
        "input_tokens": tokens.get("input") if token_coverage == "complete" else None,
        "output_tokens": tokens.get("output") if token_coverage == "complete" else None,
        "estimated_cost_usd": totals.get("estimated_api_cost_usd")
        if cost_coverage == "complete"
        else None,
        "coverage": {"tokens": token_coverage, "cost": cost_coverage},
    }


def _finish(
    store: ClaimStore,
    row: dict[str, Any],
    local: LocalConfig,
    config_path: Path,
    state: str,
    detail: str,
    validated: dict[str, Any] | None = None,
    operator: str = "",
) -> None:
    evidence = Path(row["evidence_path"])
    deliver.end(
        store,
        row["id"],
        state,
        detail,
        config_path,
        validated=validated,
        operator=operator,
        result_json=json.dumps(validated or {}),
        usage_json=json.dumps(_usage(row, evidence, datetime.now(UTC))),
        audit_json=json.dumps(_read_json(evidence / AUDIT_FILE) or {"outcome": "missing"}),
    )
    session.remove_clone(local, row["id"])


def supervise(store: ClaimStore, local: LocalConfig, config_path: Path, operator: str = "") -> None:
    for row in watch_store.rows(store, "launched"):
        evidence = Path(row["evidence_path"])
        identity = watch_store.json_field(row, "process_json")
        if not identity and (evidence / "pid").is_file():
            try:
                pid = int((evidence / "pid").read_text().strip())
                start = process_start_identity(pid)
                if start is not None:
                    identity = {"pid": pid, "start": start}
                    watch_store.update(store, row["id"], process_json=json.dumps(identity))
                else:
                    identity = {"pid": pid, "start": "missing"}
            except (OSError, ValueError):
                pass
            except ProcessProbeError:
                # An unknown probe leaves the dispatch launched; later rows still run.
                continue
        if not identity:
            if datetime.now(UTC) > datetime.fromisoformat(row["launched_at"]) + timedelta(
                minutes=2
            ):
                _finish(store, row, local, config_path, "launch-failed", "launch did not complete")
            continue
        status = process_identity_status(identity)
        if status == "unknown":
            continue
        if status == "alive":
            if datetime.now(UTC) > datetime.fromisoformat(row["deadline_at"]):
                try:
                    validated = result.read(evidence / result.RESULT_FILE, result.procedure(row))
                except (OSError, ValueError):
                    validated = None
                terminate_owned_process(identity)
                if validated is None:
                    _finish(
                        store, row, local, config_path, "timed-out", "session deadline exceeded"
                    )
                else:
                    _finish(
                        store,
                        row,
                        local,
                        config_path,
                        "completed",
                        "session deadline exceeded after result",
                        validated,
                        operator,
                    )
            continue
        exit_record = _read_json(evidence / "exit.json")
        if not exit_record:
            exit_detail = "no exit record"
        elif exit_record.get("code") != 0:
            exit_detail = f"exit code {exit_record.get('code')}"
        else:
            exit_detail = ""
        try:
            validated = result.read(evidence / result.RESULT_FILE, result.procedure(row))
        except (OSError, ValueError) as error:
            detail = exit_detail or f"invalid result: {error}"
            _finish(store, row, local, config_path, "interrupted", detail)
        else:
            # A session that exits with a valid result is completed whatever its exit
            # status: its review or triage already happened, so its comment must be posted.
            _finish(store, row, local, config_path, "completed", exit_detail, validated, operator)
