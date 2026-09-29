# pyright: reportPrivateUsage=false
"""INT-001: transactional cursor, grace eligibility, and de-duplication."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import detect
from agent_factory.watch import store as watch_store


def test_detection_uses_current_grace_and_never_requeues(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        claim = store.create_claim(
            ClaimDraft("Codagent-AI/example", 12, "I", "P", "eval", "fp", {})
        )
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        store.finish_run(run.id, execution_status="failed", result={})
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?",
            ((now - timedelta(minutes=5)).isoformat(), run.id),
        )
        detect.detect(store, 7, lambda: now + timedelta(seconds=1))
        assert not [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        detect.detect(store, 3, lambda: now + timedelta(seconds=2))
        detect.detect(store, 3, lambda: now + timedelta(seconds=3))
        failures = [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        assert len(failures) == 1
        assert failures[0]["event_key"] == f"FAILURE:{run.id}"
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        store.close()
