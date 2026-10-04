"""Idempotent App-comment delivery for watch dispatches."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from agent_factory.github import IssueComment
from agent_factory.watch import result
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.github import GitHubClient
    from agent_factory.store import ClaimStore


def queue(
    store: ClaimStore, row: dict[str, Any], purpose: str, body: str, *, target: str = "issue"
) -> None:
    """Queue one comment on the claim's issue or its pull request."""
    deliveries = watch_store.json_field(row, "deliveries_json")
    if purpose in deliveries:
        return
    deliveries[purpose] = {
        "target": target,
        "number": row["pr_number"] if target == "pr" else row["issue_number"],
        "marker": f"<!-- agent-factory:watch:{row['id']}:{purpose} -->",
        "body": body,
        "comment_id": None,
        "failure": {},
    }
    watch_store.update(store, row["id"], deliveries_json=json.dumps(deliveries))


def end(
    store: ClaimStore,
    dispatch_id: str,
    state: str,
    detail: str,
    config_path: object,
    *,
    validated: dict[str, Any] | None = None,
    **fields: object,
) -> None:
    """Record a dispatch's end state and queue the comment it owes, in one transaction.

    A completed triage owes its triage comment on the claim's issue. A completed PR-READY
    check owes no comment: its filed issues are on the board and in status. A
    budget-exhausted dispatch owes a budget notice, and any other end state an alert.
    """
    with store._transaction():  # pyright: ignore[reportPrivateUsage]
        watch_store.update(
            store,
            dispatch_id,
            state=state,
            detail=detail,
            finished_at=datetime.now(UTC).isoformat(),
            **fields,
        )
        ended = watch_store.get(store, dispatch_id)
        assert ended is not None
        if state == "completed":
            if validated is not None and ended["event_kind"] == "FAILURE":
                queue(store, ended, "triage", result.triage(ended, validated, store.is_paused()))
            if validated is not None and "risk" in validated:
                risk = validated["risk"]
                merge = watch_store.json_field(ended, "merge_json")
                merge.update(
                    state="waiting" if risk["level"] == "low" else "not-merged",
                    level=risk["level"],
                    head_sha=risk["head_sha"],
                    reason="" if risk["level"] == "low" else f"{risk['level']} risk",
                    checked_at=ended["finished_at"],
                )
                watch_store.update(store, dispatch_id, merge_json=json.dumps(merge))
                if merge["state"] == "not-merged":
                    queue(store, ended, "risk", result.verdict(validated, merge), target="pr")
            return
        budget = state == "budget-exhausted"
        run = store.get_run(ended["run_id"]) if ended["run_id"] else None
        notice = result.notice(ended, config_path, budget=budget, run=run)
        queue(store, ended, "budget" if budget else "alert", notice)


def deliver(store: ClaimStore, client: GitHubClient, bot_login: str) -> None:
    # Each target's comments are listed at most once per pass, and new posts are added to it.
    listed: dict[tuple[str, int], list[IssueComment]] = {}
    for row in watch_store.pending_deliveries(store):
        deliveries = watch_store.json_field(row, "deliveries_json")
        for purpose, raw in deliveries.items():
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, Any], raw)
            if item.get("comment_id"):
                continue
            try:
                target = (row["repository"], item["number"])
                if target not in listed:
                    listed[target] = client.list_comment_records(*target)
                comments = listed[target]
                found = next(
                    (
                        comment.id
                        for comment in comments
                        if comment.author.lower() == bot_login.lower()
                        and item["marker"] in comment.body
                    ),
                    None,
                )
                if found is None:
                    body = item["marker"] + "\n" + item["body"]
                    found = client.create_comment(row["repository"], item["number"], body)
                    if found:
                        comments.append(IssueComment(found, body, bot_login))
                item["comment_id"] = found
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
