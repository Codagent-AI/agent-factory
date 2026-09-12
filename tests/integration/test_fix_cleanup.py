from __future__ import annotations

from pathlib import Path
from unittest import mock

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.fix.cleanup import FixCleanup


def _settled_claim_with_clones(store: ClaimStore, tmp_path: Path) -> tuple[str, Path]:
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "marker.txt").write_text("hi")
    store.set_preparation(claim.id, {"clones": {"target": str(clone)}})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    return claim.id, clone


def test_clones_remain_while_in_review(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = FixCleanup(store)

    result = cleanup.reconcile(claim_id, board_status="Review")

    assert result is False
    assert clone.exists()


def test_clones_removed_after_review_then_done(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = FixCleanup(store)
    cleanup.reconcile(claim_id, board_status="Review")

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is True
    assert not clone.exists()
    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.cleanup["complete"] is True


def test_done_without_prior_review_is_not_cleaned_up(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = FixCleanup(store)

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is False
    assert clone.exists()


def test_already_removed_clone_does_not_fail_retry(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = FixCleanup(store)
    cleanup.reconcile(claim_id, board_status="Review")
    cleanup.reconcile(claim_id, board_status="Done")
    assert not clone.exists()

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is True


def test_missing_docker_binary_records_error_instead_of_crashing(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "a"))
    store.configure_run(
        run.id,
        plan={"ownership_hints": {"image_tag": "agent-runner-factory:run-1"}},
        limits={},
    )
    store.finish_run(run.id, execution_status="completed", result={"outcome": "pull-request"})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    cleanup = FixCleanup(store)
    cleanup.reconcile(claim.id, board_status="Review")

    with mock.patch(
        "agent_factory.work_kinds.images.subprocess.run",
        side_effect=OSError("docker not found"),
    ):
        result = cleanup.reconcile(claim.id, board_status="Done")

    assert result is False
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert "docker not found" in str(reloaded.cleanup["last_error"])


def test_running_claim_is_never_touched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    clone = tmp_path / "clone"
    clone.mkdir()
    store.set_preparation(claim.id, {"clones": {"target": str(clone)}})
    cleanup = FixCleanup(store)

    result = cleanup.reconcile(claim.id, board_status="Running")

    assert result is False
    assert clone.exists()
