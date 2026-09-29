# ruff: noqa: E501
# pyright: reportPrivateUsage=false
"""Queue factory events and advance the detection cursor in one transaction."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.store import ClaimStore

_PR = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/(\d+)(?:/.*)?$")
_FAILURES = frozenset({"failed", "interrupted", "cancelled", "timed_out"})


def _mapping(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}


def _nested_url(value: dict[str, object]) -> str | None:
    pr = value.get("pr")
    pr = cast(dict[str, object], pr) if isinstance(pr, dict) else {}
    url = pr.get("url")
    return url if isinstance(url, str) else None


def detect(
    store: ClaimStore,
    grace_minutes: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    with store._transaction():
        saved = store._connection.execute(
            "SELECT value_json FROM settings WHERE namespace='watch' AND key='cursor'"
        ).fetchone()
        now = now_fn().astimezone(UTC)
        current = json.loads(saved[0]) if saved else None
        if current is None:
            current = {"handled_up_to": now.isoformat(), "enabled_at": now.isoformat()}
        enabled_at = datetime.fromisoformat(current["enabled_at"])
        lower = max(
            enabled_at, datetime.fromisoformat(current["handled_up_to"]) - timedelta(minutes=2)
        )
        horizon = max(enabled_at, now - timedelta(days=7))
        grace_end = now - timedelta(minutes=grace_minutes)
        # Fetch a conservative superset; Python compares parsed timestamps exactly.
        run_sql_lower = (min(lower, horizon) - timedelta(days=1)).isoformat()
        runs = store._connection.execute(
            """SELECT r.*,c.repository,c.issue_number,c.outcome_json
            FROM run r JOIN claim c ON c.id=r.claim_id WHERE r.finished_at >= ?""",
            (run_sql_lower,),
        ).fetchall()
        for run in runs:
            event_at = datetime.fromisoformat(run["finished_at"])
            status = run["status"]
            kind = run["kind"]
            event_kind = None
            if status in _FAILURES and horizon < event_at <= grace_end:
                event_kind = "FAILURE"
            elif (
                lower < event_at <= now
                and kind == "eval"
                and status not in _FAILURES
                and status not in {"reserved", "running", "observing"}
            ):
                event_kind = "EVAL-DONE"
            elif lower < event_at <= now and kind in {"fix", "feature"} and status == "completed":
                result = _mapping(run["result_json"])
                if result.get("outcome") == "pull-request":
                    event_kind = "PR-READY"
            if event_kind is None:
                continue
            pr_url = (
                _nested_url(_mapping(run["result_json"]))
                or _nested_url(_mapping(run["outcome_json"]))
                if event_kind == "PR-READY"
                else None
            )
            match = _PR.fullmatch(pr_url) if pr_url else None
            watch_store.insert(
                store,
                event_key=f"{event_kind}:{run['id']}",
                event_kind=event_kind,
                claim_id=run["claim_id"],
                run_id=run["id"],
                repository=run["repository"],
                issue_number=run["issue_number"],
                pr_number=int(match[1]) if match else None,
                pr_url=pr_url,
                event_at=run["finished_at"],
                now=now.isoformat(),
            )
        claim_sql_lower = (lower - timedelta(days=1)).isoformat()
        for claim in store._connection.execute(
            "SELECT * FROM claim WHERE created_at >= ?", (claim_sql_lower,)
        ):
            event_at = datetime.fromisoformat(claim["created_at"])
            if lower < event_at <= now:
                watch_store.insert(
                    store,
                    event_key=f"CLAIM:{claim['id']}",
                    event_kind="CLAIM",
                    claim_id=claim["id"],
                    run_id=None,
                    repository=claim["repository"],
                    issue_number=claim["issue_number"],
                    pr_number=None,
                    pr_url=None,
                    event_at=claim["created_at"],
                    now=now.isoformat(),
                )
        current["handled_up_to"] = now.isoformat()
        store._connection.execute(
            """INSERT INTO settings(namespace,key,value_json,updated_at)
            VALUES ('watch','cursor',?,?) ON CONFLICT(namespace,key) DO UPDATE SET
            value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (json.dumps(current), now.isoformat()),
        )
