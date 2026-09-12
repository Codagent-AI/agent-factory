from __future__ import annotations

from pathlib import Path

from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore


def _settled_claim_with_pr(store: ClaimStore) -> str:
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"pr": {"url": "https://github.com/example/work/pull/1", "number": 1}},
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    return claim.id


def test_status_prints_pending_sync_with_last_failure_reason(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    store.set_claim_sync(claim_id, {"attempted": True, "blocked_reason": "uncommitted changes"})

    text = status(store)

    assert "pending sync: uncommitted changes" in text


def test_status_prints_pending_sync_before_any_attempt(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    _settled_claim_with_pr(store)

    text = status(store)

    assert "pending sync: awaiting merge" in text


def test_status_omits_completed_sync(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    store.set_claim_sync(claim_id, {"attempted": True, "completed": True})

    text = status(store)

    assert "pending sync" not in text


def test_status_omits_sync_for_a_settled_claim_without_a_pr(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "failed"})

    text = status(store)

    assert "pending sync" not in text
