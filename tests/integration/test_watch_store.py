# pyright: reportPrivateUsage=false
"""INT-002: watch table remains readable without a schema-version bump."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
