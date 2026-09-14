from __future__ import annotations

from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore


def _draft() -> ClaimDraft:
    return ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {})


def test_set_claim_sync_persists_under_reporting(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(_draft())

    store.set_claim_sync(claim.id, {"attempted": True, "completed": False})

    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert reloaded.reporting["sync"] == {"attempted": True, "completed": False}


def test_set_claim_sync_overwrites_prior_value(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(_draft())

    store.set_claim_sync(claim.id, {"blocked_reason": "uncommitted changes"})
    store.set_claim_sync(claim.id, {"completed": True})

    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert reloaded.reporting["sync"] == {"completed": True}
