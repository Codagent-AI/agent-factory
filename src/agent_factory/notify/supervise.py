# pyright: reportPrivateUsage=false
"""Finish detached notifier sessions without altering claims."""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from agent_factory.notify import store as records
from agent_factory.supervisor import (
    ProcessProbeError,
    process_identity_status,
    process_start_identity,
    terminate_owned_process,
)

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig
    from agent_factory.store import ClaimStore

logger = logging.getLogger(__name__)


def parse_result(path: Path) -> tuple[str, str, float | None]:
    try:
        value: Any = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("Claude result is not an object")
        value = cast(dict[str, object], value)
        if value.get("is_error") is not False:
            raise ValueError("Claude reported an error")
        output = value.get("structured_output")
        if not isinstance(output, dict):
            raise ValueError("invalid structured output")
        output = cast(dict[str, object], output)
        if output.get("outcome") not in {"sent", "no-session", "failed"} or not isinstance(
            output.get("detail"), str
        ):
            raise ValueError("invalid structured output")
        cost = value.get("total_cost_usd")
        return (
            cast(str, output["outcome"]),
            cast(str, output["detail"]),
            float(cost) if isinstance(cost, int | float) else None,
        )
    except (OSError, ValueError, TypeError) as error:
        return "failed", str(error), None


def _valid_result(path: Path) -> bool:
    try:
        value: Any = json.loads(path.read_text())
        if not isinstance(value, dict):
            return False
        value = cast(dict[str, object], value)
        output = value.get("structured_output")
        if not isinstance(output, dict):
            return False
        result = cast(dict[str, object], output)
        return (
            value.get("is_error") is False
            and result.get("outcome") in {"sent", "no-session", "failed"}
            and isinstance(result.get("detail"), str)
        )
    except (OSError, ValueError):
        return False


def _supervise_row(store: ClaimStore, row: dict[str, Any], now: datetime) -> None:
    evidence = Path(row["evidence_path"])
    try:
        identity = json.loads(row["process_json"])
    except ValueError:
        identity = {}
    if not identity and (evidence / "pid").is_file():
        try:
            pid = int((evidence / "pid").read_text())
            started = process_start_identity(pid)
            if started:
                identity = {"pid": pid, "start": started}
                records.update(store, row["id"], "launched", process_json=json.dumps(identity))
        except (OSError, ValueError, ProcessProbeError):
            pass
    if not identity:
        if (evidence / "exit.json").is_file() and (evidence / "stdout.json").is_file():
            outcome, detail, cost = parse_result(evidence / "stdout.json")
            records.end(store, row["id"], "launched", outcome, detail, cost_usd=cost)
            return
        if now - datetime.fromisoformat(row["launched_at"]) >= timedelta(minutes=2):
            records.end(store, row["id"], "launched", "failed", "launch lost")
        return
    state = process_identity_status(identity)
    if state == "unknown":
        return
    deadline = datetime.fromisoformat(row["deadline_at"])
    if state == "alive" and now < deadline:
        return
    timed_out = state == "alive"
    if timed_out:
        terminate_owned_process(identity)
    if timed_out and not _valid_result(evidence / "stdout.json"):
        records.end(store, row["id"], "launched", "failed", "timed out")
        return
    if (evidence / "stdout.json").is_file():
        outcome, detail, cost = parse_result(evidence / "stdout.json")
        if outcome == "failed" and (evidence / "stderr.log").is_file():
            with suppress(OSError):
                detail = (evidence / "stderr.log").read_text(errors="replace")[:500] or detail
        records.end(store, row["id"], "launched", outcome, detail, cost_usd=cost)
    else:
        records.end(store, row["id"], "launched", "failed", "missing result")


def supervise(store: ClaimStore, local: LocalConfig) -> None:
    now = datetime.now(UTC)
    for row in records.rows(store, "launched"):
        try:
            _supervise_row(store, row, now)
        except Exception as error:
            logger.exception("notify supervision failed for %s", row["id"])
            with suppress(Exception):
                records.end(store, row["id"], "launched", "failed", str(error))
