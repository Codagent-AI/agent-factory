# pyright: reportPrivateUsage=false
"""INT-002: watch table remains readable without a schema-version bump."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import store as watch_store


def test_redispatch_requires_ended_review_or_triage(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = ClaimStore(path)
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        now = datetime.now(UTC).isoformat()
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id="r",
            repository="o/r",
            issue_number=1,
            pr_number=None,
            pr_url=None,
            event_at=now,
            now=now,
        )
        original = watch_store.rows(store)[0]
        with pytest.raises(ValueError, match="pending"):
            watch_store.redispatch(store, original["id"])
        watch_store.update(store, original["id"], state="timed-out", finished_at=now)
        new_id = watch_store.redispatch(store, original["id"])
        replacement = watch_store.get(store, new_id)
        assert replacement is not None
        assert replacement["attempt"] == 2
        assert replacement["redispatch_of"] == original["id"]
    finally:
        store.close()
    reopened = ClaimStore(path)
    try:
        assert reopened._connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert watch_store.get(reopened, new_id) is not None
    finally:
        reopened.close()


def test_daily_count_uses_the_local_day_bounds_and_the_new_index(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        new_york = ZoneInfo("America/New_York")
        # 2026-09-29 local runs from 04:00 UTC on the 29th to 04:00 UTC on the 30th.
        launches = {
            "before": datetime(2026, 9, 29, 3, 59, 59, 999999, tzinfo=UTC),
            "start": datetime(2026, 9, 29, 4, 0, tzinfo=UTC),
            "late": datetime(2026, 9, 30, 3, 59, 59, 500000, tzinfo=UTC),
            "after": datetime(2026, 9, 30, 4, 0, tzinfo=UTC),
        }
        for key, launched in launches.items():
            at = launched.isoformat()
            watch_store.insert(
                store,
                event_key=f"FAILURE:{key}",
                event_kind="FAILURE",
                claim_id=claim.id,
                run_id=key,
                repository="o/r",
                issue_number=1,
                pr_number=None,
                pr_url=None,
                event_at=at,
                now=at,
            )
            row = next(r for r in watch_store.rows(store) if r["run_id"] == key)
            assert watch_store.claim_launch(store, row["id"], launched, 90, "claude:m:e", "/e")
        noon = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
        assert watch_store.daily_count(store, new_york, noon) == 2
        today = watch_store.launched_today(store, new_york, noon)
        assert sorted(r["run_id"] for r in today) == ["late", "start"]
        indexes = {
            row[0]
            for row in store._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='watch_dispatch'"
            )
        }
        assert "watch_dispatch_launched_at" in indexes
    finally:
        store.close()
