# ruff: noqa: E501
"""Saved watch state for the operator's status view."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig
    from agent_factory.store import ClaimStore


def lines(store: ClaimStore, local: LocalConfig) -> list[str]:
    from agent_factory.config import ConfigurationError, SharedConfig, WatchConfig

    try:
        watch = SharedConfig.from_file(local.shared_config).watch
    except (ConfigurationError, OSError):
        watch = WatchConfig()
    dispatches = watch_store.rows(store)
    current = watch_store.cursor(store)
    if watch.enabled:
        output = [
            f"watch: enabled, handled up to {current.get('handled_up_to') if current else 'not started'}"
        ]
        count = watch_store.daily_count(store, local.schedule.timezone, datetime.now(UTC))
        today = datetime.now(UTC).astimezone(local.schedule.timezone).date()
        cost = sum(
            (watch_store.json_field(row, "usage_json").get("estimated_cost_usd") or 0)
            for row in dispatches
            if row["launched_at"]
            and datetime.fromisoformat(row["launched_at"])
            .astimezone(local.schedule.timezone)
            .date()
            == today
        )
        output.append(
            f"watch sessions today: {count}/{watch.daily_sessions}, known cost ${cost:.2f}"
        )
    else:
        output = ["watch: disabled"]
    pending = [row for row in dispatches if row["state"] == "pending"]
    running = [row for row in dispatches if row["state"] == "launched"]
    for row in running:
        elapsed = max(
            0,
            int(
                (datetime.now(UTC) - datetime.fromisoformat(row["launched_at"])).total_seconds()
                / 60
            ),
        )
        output.append(
            f"watch running: {row['id']} {row['event_kind']} {row['repository']}#{row['issue_number']} PR #{row['pr_number'] or '?'} {row['profile']} {elapsed}m"
        )
    if pending:
        reasons: set[str] = set()
        readiness = store.get_setting("runtime", "readiness:watch") or {}
        for row in pending:
            if not watch.enabled:
                reasons.add("watching disabled")
            elif readiness.get("reason"):
                reasons.add(f"readiness: {readiness['reason']}")
            elif (
                row["event_kind"] == "PR-READY"
                and row["pr_number"]
                and watch_store.pr_running(store, row["repository"], row["pr_number"])
            ):
                reasons.add("PR review running")
            elif len(running) >= watch.max_sessions:
                reasons.add("concurrency cap")
            else:
                reasons.add("queued")
        output.append(f"watch pending: {len(pending)} ({'; '.join(sorted(reasons))})")
    for row in dispatches:
        claim = store.get_claim(row["claim_id"])
        current_claim = (
            claim is not None
            and claim.lifecycle not in {"cancelled", "superseded"}
            and not claim.cleanup.get("done_observed_at")
        )
        if current_claim and row["state"] in {
            "completed",
            "timed-out",
            "interrupted",
            "launch-failed",
            "budget-exhausted",
        }:
            output.append(
                f"watch ended: {row['id']} {row['event_kind']} {row['repository']}#{row['issue_number']} {row['state']} {row['evidence_path'] or ''}"
            )
        for purpose, item in watch_store.json_field(row, "deliveries_json").items():
            if isinstance(item, dict):
                item = cast(dict[str, Any], item)
                if item.get("comment_id"):
                    continue
                failure = item.get("failure", {})
                failure = cast(dict[str, Any], failure) if isinstance(failure, dict) else {}
                detail = failure.get("error", "pending")
                output.append(
                    f"watch undelivered: {row['id']} {purpose} → {item.get('target')} #{item.get('number')}: {detail}"
                )
        if row["state"] in {"completed", "timed-out", "interrupted"}:
            audit = watch_store.json_field(row, "audit_json")
            if audit.get("outcome") != "delivered":
                output.append(f"watch audit: {row['id']} {audit.get('outcome', 'missing')}")
        if row["event_kind"] == "PR-READY" and current_claim:
            result = watch_store.json_field(row, "result_json")
            if result.get("decisions"):
                output.append(f"watch decisions: {row['pr_url']} ({row['id']})")
    return output
