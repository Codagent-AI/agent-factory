# ruff: noqa: E501
# pyright: reportPrivateUsage=false
"""Queue factory events and advance the detection cursor in one transaction."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from agent_factory.store import _dump
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.store import ClaimStore

_PR = re.compile(r"^https://github\.com/[^/]+/[^/]+/pull/(\d+)(?:/.*)?$")
_FAILURES = frozenset({"failed", "interrupted", "cancelled", "timed_out"})


def _nested_url(value: dict[str, Any]) -> str | None:
    pr = value.get("pr")
    pr = cast(dict[str, object], pr) if isinstance(pr, dict) else {}
    url = pr.get("url")
    return url if isinstance(url, str) else None


def _is_failure(run: dict[str, Any], result: dict[str, Any]) -> bool:
    return run["status"] in _FAILURES or (
        run["kind"] in {"fix", "feature"}
        and run["status"] == "completed"
        and result.get("outcome") == "failed"
    )


def detect(
    store: ClaimStore,
    grace_minutes: int,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    with store._transaction():
        # `now` is taken inside the transaction, so every earlier writer has committed.
        now = now_fn().astimezone(UTC)
        current = watch_store.cursor(store) or {
            "handled_up_to": now.isoformat(),
            "enabled_at": now.isoformat(),
        }
        enabled_at = datetime.fromisoformat(str(current["enabled_at"]))
        horizon = max(enabled_at, now - timedelta(days=7))
        grace_end = now - timedelta(minutes=grace_minutes)
        # Fetch a conservative superset; Python compares parsed timestamps exactly.
        run_sql_lower = (horizon - timedelta(days=1)).isoformat()
        runs = store._connection.execute(
            """SELECT r.*,c.repository,c.issue_number,c.outcome_json,
            consumed.key IS NOT NULL AS result_consumed
            FROM run r JOIN claim c ON c.id=r.claim_id
            LEFT JOIN settings consumed ON consumed.namespace='consumed-results'
            AND consumed.key=r.id WHERE r.finished_at >= ?""",
            (run_sql_lower,),
        ).fetchall()
        for run in map(dict, runs):
            event_at = datetime.fromisoformat(run["finished_at"])
            result = watch_store.json_field(run, "result_json")
            event_kind = None
            if (
                _is_failure(run, result)
                and run["result_consumed"]
                and horizon < event_at <= grace_end
            ):
                event_kind = "FAILURE"
            elif (
                horizon < event_at <= now
                and run["kind"] in {"fix", "feature"}
                and run["status"] == "completed"
                and result.get("outcome") == "pull-request"
            ):
                event_kind = "PR-READY"
            if event_kind is None:
                continue
            pr_url = (
                _nested_url(result) or _nested_url(watch_store.json_field(run, "outcome_json"))
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
        # handled_up_to now records the last detection pass, which status shows.
        current["handled_up_to"] = now.isoformat()
        # set_setting opens its own transaction, so the cursor is upserted here directly.
        store._connection.execute(
            """INSERT INTO settings(namespace,key,value_json,updated_at)
            VALUES ('watch','cursor',?,?) ON CONFLICT(namespace,key) DO UPDATE SET
            value_json=excluded.value_json,updated_at=excluded.updated_at""",
            (_dump(current), now.isoformat()),
        )
