from __future__ import annotations

from pathlib import Path

from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore


def test_status_prints_pending_sync_with_last_failure_reason(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    store.set_claim_sync(claim.id, {"attempted": True, "blocked_reason": "uncommitted changes"})

    text = status(store)

    assert "pending sync: uncommitted changes" in text


def test_status_omits_completed_sync(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    store.set_claim_sync(claim.id, {"attempted": True, "completed": True})

    text = status(store)

    assert "pending sync" not in text
