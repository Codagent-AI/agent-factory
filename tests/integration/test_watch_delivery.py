"""INT-005: comments are adopted after a receipt is lost."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_factory.github import IssueComment
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
