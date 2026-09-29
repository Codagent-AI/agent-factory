# pyright: reportPrivateUsage=false
"""Durable watch dispatch records on the factory store connection."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from agent_factory.store import ClaimStore


# End states a review or triage dispatch can be redispatched from.
ENDED = frozenset({"completed", "interrupted", "timed-out", "launch-failed", "budget-exhausted"})
# End states of a dispatch whose session actually ran.
SESSION_ENDED = frozenset({"completed", "timed-out", "interrupted"})


def rows(store: ClaimStore, state: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM watch_dispatch"
    args: tuple[str, ...] = ()
    if state is not None:
        query += " WHERE state = ?"
        args = (state,)
    query += " ORDER BY event_at, event_key, attempt"
    return [dict(row) for row in store._connection.execute(query, args)]


def get(store: ClaimStore, dispatch_id: str) -> dict[str, Any] | None:
    row = store._connection.execute(
        "SELECT * FROM watch_dispatch WHERE id = ?", (dispatch_id,)
    ).fetchone()
    return dict(row) if row else None


def cursor(store: ClaimStore) -> dict[str, object] | None:
    return store.get_setting("watch", "cursor")


def clear_cursor(store: ClaimStore) -> None:
    store.clear_setting("watch", "cursor")


def insert(
    store: ClaimStore,
    *,
    event_key: str,
    event_kind: str,
    claim_id: str,
    run_id: str | None,
    repository: str,
    issue_number: int,
    pr_number: int | None,
    pr_url: str | None,
    event_at: str,
    now: str,
) -> None:
    store._connection.execute(
        """INSERT OR IGNORE INTO watch_dispatch
        (id,event_key,attempt,event_kind,claim_id,run_id,repository,issue_number,
         pr_number,pr_url,event_at,state,created_at,updated_at)
        VALUES (?,?,1,?,?,?,?,?,?,?,?,'pending',?,?)""",
        (
            str(uuid.uuid4()),
            event_key,
            event_kind,
            claim_id,
            run_id,
            repository,
            issue_number,
            pr_number,
            pr_url,
            event_at,
            now,
            now,
        ),
    )


def update(store: ClaimStore, dispatch_id: str, **fields: object) -> None:
    if not fields:
        return
    fields["updated_at"] = datetime.now(UTC).isoformat()
    columns = ", ".join(f"{key} = ?" for key in fields)
    store._connection.execute(
        f"UPDATE watch_dispatch SET {columns} WHERE id = ?", (*fields.values(), dispatch_id)
    )


def claim_launch(
    store: ClaimStore,
    dispatch_id: str,
    now: datetime,
    timeout_minutes: int,
    profile: str,
    evidence_path: str,
) -> bool:
    with store._transaction():
        result = store._connection.execute(
            """UPDATE watch_dispatch SET state='launched',
            launched_at=?, deadline_at=?, profile=?, evidence_path=?, updated_at=?
            WHERE id=? AND state='pending'""",
            (
                now.isoformat(),
                (now + timedelta(minutes=timeout_minutes)).isoformat(),
                profile,
                evidence_path,
                now.isoformat(),
                dispatch_id,
            ),
        )
        return result.rowcount == 1


def _local_day_bounds(timezone: ZoneInfo, now: datetime) -> tuple[str, str]:
    """The UTC isoformat bounds of `now`'s local day.

    launched_at is always a UTC isoformat string, so these bounds compare correctly as text.
    """
    today = now.astimezone(timezone).date()
    start = datetime.combine(today, time(), tzinfo=timezone)
    end = datetime.combine(today + timedelta(days=1), time(), tzinfo=timezone)
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()


def launched_today(store: ClaimStore, timezone: ZoneInfo, now: datetime) -> list[dict[str, Any]]:
    """Dispatches whose session started on `now`'s local day in the schedule timezone."""
    return [
        dict(row)
        for row in store._connection.execute(
            "SELECT * FROM watch_dispatch WHERE launched_at >= ? AND launched_at < ?",
            _local_day_bounds(timezone, now),
        )
    ]


def daily_count(store: ClaimStore, timezone: ZoneInfo, now: datetime) -> int:
    return store._connection.execute(
        "SELECT count(*) FROM watch_dispatch WHERE launched_at >= ? AND launched_at < ?",
        _local_day_bounds(timezone, now),
    ).fetchone()[0]


def running_count(store: ClaimStore) -> int:
    return store._connection.execute(
        "SELECT count(*) FROM watch_dispatch WHERE state='launched'"
    ).fetchone()[0]


def pr_running(store: ClaimStore, repository: str, pr_number: int) -> bool:
    return (
        store._connection.execute(
            """SELECT 1 FROM watch_dispatch WHERE state='launched'
        AND repository=? AND pr_number=? LIMIT 1""",
            (repository, pr_number),
        ).fetchone()
        is not None
    )


def redispatch(store: ClaimStore, dispatch_id: str) -> str:
    with store._transaction():
        row = store._connection.execute(
            "SELECT * FROM watch_dispatch WHERE id=?", (dispatch_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown watch dispatch {dispatch_id}")
        if row["event_kind"] not in {"PR-READY", "FAILURE"} or row["state"] not in ENDED:
            raise ValueError(f"cannot redispatch {dispatch_id} in state {row['state']}")
        attempt = (
            store._connection.execute(
                "SELECT max(attempt) FROM watch_dispatch WHERE event_key=?", (row["event_key"],)
            ).fetchone()[0]
            + 1
        )
        new_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        store._connection.execute(
            """INSERT INTO watch_dispatch
            (id,event_key,attempt,event_kind,claim_id,run_id,repository,issue_number,
             pr_number,pr_url,event_at,state,redispatch_of,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
            (
                new_id,
                row["event_key"],
                attempt,
                row["event_kind"],
                row["claim_id"],
                row["run_id"],
                row["repository"],
                row["issue_number"],
                row["pr_number"],
                row["pr_url"],
                row["event_at"],
                dispatch_id,
                now,
                now,
            ),
        )
        return new_id


def json_field(row: dict[str, Any], name: str) -> dict[str, Any]:
    value = json.loads(row[name])
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}
