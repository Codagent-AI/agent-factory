from __future__ import annotations

from pathlib import Path
from unittest import mock

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup


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
    cleanup = PullRequestCleanup(store)

    result = cleanup.reconcile(claim_id, board_status="Review")

    assert result is False
    assert clone.exists()


def test_clones_removed_after_review_then_done(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = PullRequestCleanup(store)
    cleanup.reconcile(claim_id, board_status="Review")

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is True
    assert not clone.exists()
    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.cleanup["complete"] is True


def test_read_only_module_cache_in_a_clone_is_removed(tmp_path: Path) -> None:
    # Go's module cache marks its directories and files read-only, so a plain tree
    # removal fails with EACCES and the clone would leak on every retry.
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    module = clone / ".validator/cache/go/pkg/mod/example.com/lib@v1.0.0"
    module.mkdir(parents=True)
    (module / "lib.go").write_text("package lib\n")
    (module / "lib.go").chmod(0o444)
    module.chmod(0o555)
    module.parent.chmod(0o555)
    cleanup = PullRequestCleanup(store)
    cleanup.reconcile(claim_id, board_status="Review")

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is True
    assert not clone.exists()


def test_done_without_prior_review_is_not_cleaned_up(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = PullRequestCleanup(store)

    result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is False
    assert clone.exists()


def test_already_removed_clone_does_not_fail_retry(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    cleanup = PullRequestCleanup(store)
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
    cleanup = PullRequestCleanup(store)
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
    cleanup = PullRequestCleanup(store)

    result = cleanup.reconcile(claim.id, board_status="Running")

    assert result is False
    assert clone.exists()


def test_done_removes_every_attempts_private_credential_copy(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    private = tmp_path / "private"
    copies: list[Path] = []
    for reason in ("initial", "recovery"):
        run = store.reserve_run(claim.id, "fix", reason=reason, evidence_path=str(tmp_path / "a"))
        copy = private / run.id / "fix.env"
        copy.parent.mkdir(parents=True)
        copy.write_text("GH_TOKEN=secret\n")
        copies.append(copy)
        store.configure_run(run.id, plan={"credential_files": [str(copy)]}, limits={})
        store.finish_run(run.id, execution_status="failed", result={})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "infra-error"})
    cleanup = PullRequestCleanup(store, private_root=private)
    cleanup.reconcile(claim.id, board_status="Review")
    assert all(copy.exists() for copy in copies)

    with mock.patch("agent_factory.work_kinds.images.subprocess.run"):
        result = cleanup.reconcile(claim.id, board_status="Done")

    assert result is True
    assert not any(copy.exists() for copy in copies)
    assert not any(copy.parent.exists() for copy in copies)


def test_done_for_a_host_only_claim_removes_clones_without_running_docker(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone = _settled_claim_with_clones(store, tmp_path)
    run = store.reserve_run(claim_id, "fix", reason="initial", evidence_path=str(tmp_path / "a"))
    private = tmp_path / "private"
    copy = private / run.id / "fix.env"
    copy.parent.mkdir(parents=True)
    copy.write_text("GH_TOKEN=secret\n")
    store.configure_run(
        run.id,
        plan={
            "credential_files": [str(copy)],
            "ownership_hints": {"sandbox": "host", "session_dir": str(tmp_path / "a" / "s")},
        },
        limits={},
    )
    store.finish_run(run.id, execution_status="completed", result={"outcome": "failed"})
    cleanup = PullRequestCleanup(store, private_root=private)
    cleanup.reconcile(claim_id, board_status="Review")

    with mock.patch("agent_factory.work_kinds.images.subprocess.run") as docker:
        result = cleanup.reconcile(claim_id, board_status="Done")

    assert result is True
    docker.assert_not_called()
    assert not clone.exists()
    assert not copy.parent.exists()


def _cancelled_claim_with_clone_and_token(
    store: ClaimStore, tmp_path: Path, *, run_status: str
) -> tuple[str, Path, Path]:
    claim = store.create_claim(ClaimDraft("example/work", 313, "I313", "P313", "fix", "fp", {}))
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "marker.txt").write_text("hi")
    store.set_preparation(claim.id, {"clones": {"target": str(clone)}})
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "a"))
    private = tmp_path / "private" / run.id
    private.mkdir(parents=True)
    (private / "fix.env").write_text("GH_TOKEN=secret\n")
    store.configure_run(run.id, plan={"credential_files": [str(private / "fix.env")]}, limits={})
    if run_status == "running":
        store.mark_running(run.id, {"pid": 1})
    else:
        store.finish_run(run.id, execution_status="cancelled", result={"reason": "cancelled"})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    return claim.id, clone, private


def test_cancelled_claim_keeps_clones_while_execution_is_stopping(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone, private = _cancelled_claim_with_clone_and_token(
        store, tmp_path, run_status="running"
    )
    cleanup = PullRequestCleanup(store, private_root=tmp_path / "private")

    result = cleanup.reconcile(claim_id, board_status="Running")

    assert result is False
    assert clone.exists()
    assert private.exists()


def test_cancelled_claim_is_released_once_execution_stopped(tmp_path: Path) -> None:
    """A cancelled card may never pass through Review, so it does not wait for Done."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, clone, private = _cancelled_claim_with_clone_and_token(
        store, tmp_path, run_status="cancelled"
    )
    cleanup = PullRequestCleanup(store, private_root=tmp_path / "private")

    result = cleanup.reconcile(claim_id, board_status="Running")

    assert result is True
    assert not clone.exists()
    assert not private.exists()
    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.cleanup["complete"] is True
    assert cleanup.reconcile(claim_id, board_status="Done") is True
