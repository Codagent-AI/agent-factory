# pyright: reportPrivateUsage=false
"""INT-007: notification storage is additive and rollback compatible."""

from __future__ import annotations

from pathlib import Path

from agent_factory.store import SCHEMA_VERSION, ClaimDraft, ClaimStore


def test_additive_schema_reopens_without_version_bump(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = ClaimStore(path)
    claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
    store._connection.execute("DROP TABLE notify_stop")
    store.close()
    for _ in range(2):
        store = ClaimStore(path)
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert store.get_claim(claim.id) == claim
        assert (
            store._connection.execute(
                "SELECT name FROM sqlite_master WHERE name='notify_stop_state'"
            ).fetchone()
            is not None
        )
        store.close()
