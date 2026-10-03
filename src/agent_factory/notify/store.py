# pyright: reportPrivateUsage=false
"""Forward-only notification records on the ClaimStore connection."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory.watch.store import _local_day_bounds

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig
    from agent_factory.store import ClaimStore


def rows(store: ClaimStore, state: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM notify_stop"
    if state:
        sql += " WHERE state=?"
    return [
        dict(row)
        for row in store._connection.execute(
            sql + " ORDER BY stopped_since", (state,) if state else ()
        )
    ]


def cursor(store: ClaimStore) -> dict[str, object] | None:
    return store.get_setting("notify", "cursor")


def insert(
    store: ClaimStore,
    claim_id: str,
    run_id: str,
    repository: str,
    issue_number: int,
    claim_kind: str,
    stop_kind: str,
    now: datetime,
    pr_url: str | None = None,
) -> None:
    instant = now.isoformat()
    store._connection.execute(
        """INSERT OR IGNORE INTO notify_stop
        (id,claim_id,run_id,repository,issue_number,claim_kind,stop_kind,state,
         stopped_since,pr_url,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,'settling',?,?,?,?)""",
        (
            str(uuid.uuid4()),
            claim_id,
            run_id,
            repository,
            issue_number,
            claim_kind,
            stop_kind,
            instant,
            pr_url,
            instant,
            instant,
        ),
    )


def update(store: ClaimStore, row_id: str, expected_state: str, **fields: object) -> bool:
    if not fields:
        return False
    destination = fields.get("state")
    if destination is not None and (expected_state, destination) not in {
        ("settling", "launched"),
        ("settling", "ended"),
        ("launched", "ended"),
    }:
        raise ValueError("notify state transition must move forward")
    fields["updated_at"] = datetime.now(UTC).isoformat()
    assignments = ", ".join(f"{key}=?" for key in fields)
    result = store._connection.execute(
        f"UPDATE notify_stop SET {assignments} WHERE id=? AND state=?",
        (*fields.values(), row_id, expected_state),
    )
    return result.rowcount == 1


def end(
    store: ClaimStore,
    row_id: str,
    from_state: str,
    outcome: str,
    detail: str = "",
    **fields: object,
) -> bool:
    return update(
        store,
        row_id,
        from_state,
        state="ended",
        outcome=outcome,
        detail=detail[:500],
        finished_at=datetime.now(UTC).isoformat(),
        **fields,
    )


def restart_settling(store: ClaimStore) -> None:
    store._connection.execute("UPDATE notify_stop SET restart_settle=1 WHERE state='settling'")


def disable(store: ClaimStore) -> None:
    store.clear_setting("notify", "cursor")
    store._connection.execute("DELETE FROM notify_stop WHERE state='settling'")


def launched_today(store: ClaimStore, local: LocalConfig, now: datetime) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in store._connection.execute(
            "SELECT * FROM notify_stop WHERE launched_at>=? AND launched_at<?",
            _local_day_bounds(local.schedule.timezone, now),
        )
    ]


def daily_count(store: ClaimStore, local: LocalConfig, now: datetime) -> int:
    return len(launched_today(store, local, now))


def prune(store: ClaimStore, local: LocalConfig) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=max(local.limits.evidence_retention_days, 8))
    for row in rows(store, "ended"):
        if row["finished_at"] and datetime.fromisoformat(row["finished_at"]) < cutoff:
            if row["evidence_path"]:
                shutil.rmtree(Path(row["evidence_path"]), ignore_errors=True)
            store._connection.execute(
                "DELETE FROM notify_stop WHERE id=? AND state='ended'", (row["id"],)
            )
