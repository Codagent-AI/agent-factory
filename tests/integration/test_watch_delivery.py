# pyright: reportPrivateUsage=false
"""INT-005: adoption, non-bot markers, recorded failures, retries, and targets."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_factory.github import GitHubApiError, IssueComment
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import deliver
from agent_factory.watch import store as watch_store


class Comments:
    def __init__(self) -> None:
        self.records: list[IssueComment] = []
        self.posts = 0

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return list(self.records)

    def create_comment(self, repository: str, number: int, body: str) -> str:
        self.posts += 1
        self.records.append(IssueComment(str(self.posts), body, "factory[bot]"))
        return str(self.posts)


def test_delivery_adopts_existing_bot_comment(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 2, "I", "P", "fix", "fp", {}))
        now = datetime.now(UTC).isoformat()
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id="r",
            repository="o/r",
            issue_number=2,
            pr_number=None,
            pr_url=None,
            event_at=now,
            now=now,
        )
        row = watch_store.rows(store)[0]
        deliver.queue(store, row, "alert", "A failure occurred", "issue")
        client = Comments()
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert client.posts == 1
        saved = watch_store.get(store, row["id"])
        assert saved is not None
        deliveries: dict[str, Any] = watch_store.json_field(saved, "deliveries_json")
        deliveries["alert"]["comment_id"] = None
        watch_store.update(store, row["id"], deliveries_json=__import__("json").dumps(deliveries))
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert client.posts == 1
        assert (
            watch_store.json_field(watch_store.get(store, row["id"]) or {}, "deliveries_json")[
                "alert"
            ]["comment_id"]
            == "1"
        )
    finally:
        store.close()


class Flaky(Comments):
    def __init__(self) -> None:
        super().__init__()
        self.fail = True
        self.targets: list[int] = []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        if self.fail:
            raise GitHubApiError("HTTP 502")
        self.targets.append(number)
        return super().create_comment(repository, number, body)


def _review_row(store: ClaimStore) -> dict[str, Any]:
    claim = store.create_claim(ClaimDraft("o/r", 5, "I", "P", "fix", "fp", {}))
    now = datetime.now(UTC).isoformat()
    watch_store.insert(
        store,
        event_key="PR-READY:r",
        event_kind="PR-READY",
        claim_id=claim.id,
        run_id="r",
        repository="o/r",
        issue_number=5,
        pr_number=70,
        pr_url="https://github.com/o/r/pull/70",
        event_at=now,
        now=now,
    )
    return watch_store.rows(store)[0]


def test_failed_delivery_is_recorded_then_posted_once_to_each_target(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        row = _review_row(store)
        deliver.queue(store, row, "decisions", "Decide this", "pr")
        row = watch_store.get(store, row["id"]) or {}
        deliver.queue(store, row, "alert", "It failed", "issue")
        client = Flaky()
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "deliveries_json")
        assert client.posts == 0
        assert saved["decisions"]["failure"]["error"] == "HTTP 502"
        assert saved["decisions"]["comment_id"] is None
        client.fail = False
        for _ in range(3):
            deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert sorted(client.targets) == [5, 70]
        assert client.posts == 2
    finally:
        store.close()


def test_marker_in_a_non_bot_comment_is_not_adopted(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        row = _review_row(store)
        deliver.queue(store, row, "decisions", "Decide this", "pr")
        marker = f"<!-- agent-factory:watch:{row['id']}:decisions -->"
        client = Comments()
        client.records.append(IssueComment("99", f"{marker}\nforged", "someone-else"))
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert client.posts == 1
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "deliveries_json")
        assert saved["decisions"]["comment_id"] == "1"
    finally:
        store.close()


def test_delivery_queries_only_rows_with_pending_comments(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        old = _review_row(store)
        deliver.queue(store, old, "decisions", "Already delivered", "pr")
        client = Comments()
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert watch_store.pending_deliveries(store) == []

        claim = store.create_claim(ClaimDraft("o/r", 6, "I", "P", "fix", "fp-2", {}))
        now = datetime.now(UTC).isoformat()
        watch_store.insert(
            store,
            event_key="PR-READY:next",
            event_kind="PR-READY",
            claim_id=claim.id,
            run_id="next",
            repository="o/r",
            issue_number=6,
            pr_number=71,
            pr_url="https://github.com/o/r/pull/71",
            event_at=now,
            now=now,
        )
        next_row = next(
            row for row in watch_store.rows(store) if row["event_key"] == "PR-READY:next"
        )
        deliver.queue(store, next_row, "decisions", "Still pending", "pr")
        assert [row["id"] for row in watch_store.pending_deliveries(store)] == [next_row["id"]]
        plan = store._connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM watch_dispatch WHERE delivery_pending=1"
        ).fetchall()
        assert any("watch_dispatch_delivery_pending" in str(row[3]) for row in plan)
    finally:
        store.close()


def test_existing_watch_rows_backfill_pending_delivery_index(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = ClaimStore(path)
    try:
        row = _review_row(store)
        deliver.queue(store, row, "decisions", "Still pending", "pr")
    finally:
        store.close()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX watch_dispatch_delivery_pending")
        connection.execute("ALTER TABLE watch_dispatch DROP COLUMN delivery_pending")
    reopened = ClaimStore(path)
    try:
        assert [row["id"] for row in watch_store.pending_deliveries(reopened)] == [row["id"]]
    finally:
        reopened.close()
