"""Idempotent App-comment delivery for watch dispatches."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.github import GitHubClient
    from agent_factory.store import ClaimStore


def queue(store: ClaimStore, row: dict[str, Any], purpose: str, body: str, target: str) -> None:
    deliveries = watch_store.json_field(row, "deliveries_json")
    if purpose in deliveries:
        return
    number = row["pr_number"] if target == "pr" else row["issue_number"]
    deliveries[purpose] = {
        "target": target,
        "number": number,
        "marker": f"<!-- agent-factory:watch:{row['id']}:{purpose} -->",
        "body": body,
        "comment_id": None,
        "failure": {},
    }
    watch_store.update(store, row["id"], deliveries_json=json.dumps(deliveries))


def deliver(store: ClaimStore, client: GitHubClient, bot_login: str) -> None:
    for row in watch_store.rows(store):
        deliveries = watch_store.json_field(row, "deliveries_json")
        for purpose, raw in deliveries.items():
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, Any], raw)
            if item.get("comment_id"):
                continue
            try:
                comments = client.list_comment_records(row["repository"], item["number"])
                found = next(
                    (
                        comment.id
                        for comment in comments
                        if comment.author.lower() == bot_login.lower()
                        and item["marker"] in comment.body
                    ),
                    None,
                )
                item["comment_id"] = found or client.create_comment(
                    row["repository"], item["number"], item["marker"] + "\n" + item["body"]
                )
                item["failure"] = {}
            except Exception as error:
                previous = item.get("failure", {})
                item["failure"] = {
                    "attempts": previous.get("attempts", 0) + 1,
                    "error": str(error),
                    "at": datetime.now(UTC).isoformat(),
                }
            deliveries[purpose] = item
            watch_store.update(store, row["id"], deliveries_json=json.dumps(deliveries))
