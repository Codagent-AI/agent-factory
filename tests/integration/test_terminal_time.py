"""INT-001: terminal clocks survive same-state writes and reopen after new runs."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.terminal import terminal_time


def _draft(number: int) -> ClaimDraft:
    return ClaimDraft("example/repo", number, f"I{number}", f"P{number}", "fix", "fp", {})


def test_terminal_time_reopen_and_backfill(tmp_path: Path) -> None:
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store:
        claim = store.create_claim(_draft(1))
        store.set_cleanup(claim.id, {"paths": {"x": "y"}, "retention": {"removed": ["old"]}})
        store.set_claim_lifecycle(claim.id, "settled", {})
        first = store.get_claim(claim.id)
        assert first is not None
        clock = first.cleanup["terminal_at"]
        for _ in range(5):
            store.set_claim_lifecycle(claim.id, "settled", {})
        assert store.get_claim(claim.id).cleanup["terminal_at"] == clock  # type: ignore[union-attr]
        store.set_cleanup(
            claim.id, {"complete": True, "retention": {"pruned_at": "old", "removed": ["old"]}}
        )
        assert store.get_claim(claim.id).cleanup["terminal_at"] == clock  # type: ignore[union-attr]
        run = store.reserve_run(claim.id, "fix", reason="review", evidence_path=str(tmp_path))
        store.set_claim_lifecycle(claim.id, "active", {})
        reopened = store.get_claim(claim.id)
        assert reopened is not None
        assert reopened.cleanup["complete"] is False
        assert "terminal_at" not in reopened.cleanup
        assert reopened.cleanup["retention"] == {"removed": ["old"]}
        store.finish_run(run.id, execution_status="completed", result={})
        store.set_claim_lifecycle(claim.id, "settled", {})
        assert store.get_claim(claim.id).cleanup["terminal_at"] != "old"  # type: ignore[union-attr]
        replacement = store.supersede_and_create(claim.id, _draft(2))
        assert replacement.id != claim.id
        superseded = store.get_claim(claim.id)
        assert superseded is not None and superseded.cleanup["terminal_at"]
        legacy = store.create_claim(_draft(3))
        store.set_claim_lifecycle(legacy.id, "cancelled", {})
        old = store.get_claim(legacy.id)
        assert old is not None
        store.set_cleanup(legacy.id, {})  # terminal time remains protected by set_cleanup
        store._connection.execute(  # pyright: ignore[reportPrivateUsage]
            "UPDATE claim SET cleanup_json = '{}' WHERE id = ?", (legacy.id,)
        )
        historical = store.get_claim(legacy.id)
        assert historical is not None
        assert terminal_time(store, historical).isoformat() == historical.updated_at
        store.set_preparation(legacy.id, {})
        assert terminal_time(store, store.get_claim(legacy.id)).isoformat() == historical.updated_at  # type: ignore[arg-type]
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 4  # pyright: ignore[reportPrivateUsage]
