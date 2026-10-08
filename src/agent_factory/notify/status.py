"""Operator status lines for notification sessions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent_factory.notify import store as records

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.store import ClaimStore


def lines(store: ClaimStore, local: LocalConfig, shared: SharedConfig) -> list[str]:
    now = datetime.now(UTC)
    output = [f"notifications: {'enabled' if shared.notify.enabled else 'disabled'}"]
    today = records.launched_today(store, local, now)
    if shared.notify.enabled:
        cost = sum(row["cost_usd"] or 0 for row in today)
        output.append(
            f"notify sessions today: {len(today)} of "
            f"{shared.notify.daily_sessions}, known cost ${cost:.2f}"
        )
    for row in records.rows(store, "launched"):
        elapsed = max(
            0, int((now - datetime.fromisoformat(row["launched_at"])).total_seconds() / 60)
        )
        output.append(
            f"notify running: {row['repository']}#{row['issue_number']} "
            f"{row['stop_kind']} {elapsed}m"
        )
    for row in records.rows(store, "ended"):
        if (
            row["outcome"] == "unmarked"
            or not row["finished_at"]
            or datetime.fromisoformat(row["finished_at"]) < now - timedelta(hours=24)
        ):
            continue
        output.append(
            f"notify ended: {row['repository']}#{row['issue_number']} "
            f"{row['stop_kind']} {row['outcome']} {row['session_name'] or ''} "
            f"{row['watch_note']}"
        )
    return output
