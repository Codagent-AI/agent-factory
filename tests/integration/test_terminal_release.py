"""INT-002: the store sweep releases all owned clone attempts off the board."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from agent_factory import terminal
from agent_factory.controller import Controller
from agent_factory.github import GitHubApiError, GitHubNotFoundError
from agent_factory.operations import status
from agent_factory.store import Claim, ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import (
    GitWorktreeManager,
    SourceRepositories,
    WorktreeCleanup,
)
from agent_factory.work_kinds.base import WorkKindHandler
from agent_factory.work_kinds.eval.handler import EvalHandler
from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from tests.integration.test_retention import _local  # pyright: ignore[reportPrivateUsage]


def test_off_board_cancelled_claim_releases_every_attempt_and_keeps_other_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "fix", "fp", {}))
    clone_root = tmp_path / "factory" / "clones"
    claim_dir = clone_root / claim.id
    for number in range(3):
        path = claim_dir / str(number)
        path.mkdir(parents=True)
        (path / "file").write_text("owned")
    (claim_dir / "0" / "file").chmod(0o444)
    other = clone_root / "another-claim"
    other.mkdir()
    (other / "file").write_text("keep")
    mirror = tmp_path / "factory" / "mirrors" / "repo.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("keep")
    store.set_preparation(claim.id, {"clones": {"target": str(claim_dir / "2")}})
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "ev"))
    credential = tmp_path / "factory" / "private" / run.id / "token"
    credential.parent.mkdir(parents=True)
    credential.write_text("private")
    store.configure_run(
        run.id,
        plan={
            "credential_files": [str(credential)],
            "ownership_hints": {"image_tag": "factory-owned-image"},
        },
        limits={},
    )
    removed_image = Mock(return_value=SimpleNamespace(returncode=0, stderr=""))
    monkeypatch.setattr("agent_factory.work_kinds.images.subprocess.run", removed_image)
    store.finish_run(run.id, execution_status="cancelled", result={})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    cleanup = PullRequestCleanup(
        store, claim_directory=lambda _: claim_dir, private_root=tmp_path / "factory" / "private"
    )
    handler = Mock()

    def release(_: object) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = []
    client = Mock()
    controller = Mock()
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert not claim_dir.exists()
    assert not credential.exists()
    removed_image.assert_called_once_with(
        ["docker", "rmi", "factory-owned-image"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert (other / "file").read_text() == "keep"
    assert (mirror / "HEAD").read_text() == "keep"
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is True


def test_settled_open_pr_waits_for_age_and_merged_pr_waits_for_sync(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "fix", "fp", {}))
    store.set_claim_lifecycle(
        claim.id, "settled", {"pr": {"number": 1, "url": "https://example.test/pr/1"}}
    )
    directory = tmp_path / "factory" / "clones" / claim.id
    directory.mkdir(parents=True)
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: directory)
    handler = Mock()

    def release(_: object) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = []
    client = Mock()
    client.get_pull_request.return_value.merged_at = None
    now = datetime.now(UTC)
    controller = Mock()
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {claim.id: "Review"},
        now,
    )
    assert directory.exists()
    saved = store.get_claim(claim.id)
    assert saved is not None
    store.set_cleanup(
        claim.id, {**saved.cleanup, "terminal_at": (now - timedelta(days=31)).isoformat()}
    )
    client.get_pull_request.return_value.merged_at = "2026-01-01"
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        now,
    )
    assert directory.exists()
    client.get_pull_request.return_value.merged_at = None
    client.get_pull_request.return_value.state = "closed"
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        now,
    )
    assert not directory.exists()


def test_pending_report_and_machine_hold_block_terminal_release(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 2, "I2", "P2", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "ev"))
    store.update_progress(run.id, {"machine": {"id": "m"}})
    store.finish_run(run.id, execution_status="cancelled", result={})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    directory = tmp_path / "factory" / "clones" / claim.id
    directory.mkdir(parents=True)
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: directory)
    handler = Mock()

    def release(_: object) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = []
    controller = Mock()
    client = Mock()
    store.record_event(claim.id, "cancelled", "message")
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert directory.exists()
    store.acknowledge_event(claim.id, "cancelled", "comment-1")
    store.set_setting("runtime", f"fly:machine:{run.id}", {"machine_id": "m"})
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert directory.exists()
    store.clear_setting("runtime", f"fly:machine:{run.id}")
    store.set_setting("runtime", "fly:cleanup-failed", {"machines": [{"machine_id": "m"}]})
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert directory.exists()
    store.clear_setting("runtime", "fly:cleanup-failed")
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert not directory.exists()


@pytest.mark.parametrize("failure", [GitHubApiError("revoked"), GitHubNotFoundError("gone")])
def test_unreadable_and_deleted_pr_state_have_distinct_release_results(
    tmp_path: Path, failure: Exception
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 3, "I3", "P3", "fix", "fp", {}))
    store.set_claim_lifecycle(
        claim.id, "settled", {"pr": {"number": 3, "url": "https://example.test/pr/3"}}
    )
    directory = tmp_path / "clones" / claim.id
    directory.mkdir(parents=True)
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: directory)
    handler = Mock()

    def release(_: object) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = []
    client = Mock()
    client.get_pull_request.side_effect = failure
    now = datetime.now(UTC)
    saved = store.get_claim(claim.id)
    assert saved is not None
    store.set_cleanup(
        claim.id, {**saved.cleanup, "terminal_at": (now - timedelta(days=31)).isoformat()}
    )
    terminal.sweep(
        store,
        cast(Controller, Mock()),
        client,
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        now,
    )
    saved = store.get_claim(claim.id)
    assert saved is not None
    if isinstance(failure, GitHubNotFoundError):
        assert not directory.exists()
        assert "sync_check_error" not in saved.cleanup
    else:
        assert directory.exists()
        assert saved.cleanup["sync_check_error"] == "revoked"
        assert "PR state unreadable: revoked" in status(store)
    assert client.get_pull_request.call_count == 1


def test_eval_handler_releases_real_git_worktrees_but_keeps_sources(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 10, "I10", "P10", "eval", "fp", {}))
    revisions: dict[str, str] = {}
    for name in ("runner", "skills", "evals"):
        source = tmp_path / name
        source.mkdir()
        subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
        (source / "keep").write_text("source")
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "source",
            ],
            check=True,
        )
        revisions[name] = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
    manager = GitWorktreeManager(
        tmp_path / "factory",
        SourceRepositories(tmp_path / "runner", tmp_path / "skills", tmp_path / "evals"),
    )
    worktrees = manager.prepare(claim.id, revisions)
    (worktrees.runner / "read-only").write_text("remove")
    (worktrees.runner / "read-only").chmod(0o444)
    cleanup = WorktreeCleanup(store, manager)
    cleanup.record(claim.id, worktrees)
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    handler = object.__new__(EvalHandler)
    handler._worktree_cleanup = cleanup  # pyright: ignore[reportPrivateUsage]
    terminal.sweep(
        store,
        cast(Controller, Mock()),
        Mock(),
        {"eval": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is True
    assert all(not path.exists() for path in worktrees.paths())
    assert all((tmp_path / name / "keep").read_text() == "source" for name in revisions)


@pytest.mark.parametrize("kind", ["eval", "fix"])
def test_done_handler_waits_for_pending_report_before_release(tmp_path: Path, kind: str) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 11, "I11", "P11", kind, "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {})
    root = tmp_path / "clones" / claim.id
    root.mkdir(parents=True)
    if kind == "fix":
        cleanup = PullRequestCleanup(store, claim_directory=lambda _: root)
        handler = object.__new__(PullRequestHandler)
        handler._cleanup = cleanup  # pyright: ignore[reportPrivateUsage]
    else:
        manager = Mock()
        manager.remove.return_value = {}
        cleanup = WorktreeCleanup(store, manager)
        saved = store.get_claim(claim.id)
        assert saved is not None
        paths = {
            name: {"source": str(tmp_path / name), "path": str(root / name)}
            for name in ("runner", "skills", "evals")
        }
        store.set_cleanup(claim.id, {**saved.cleanup, "paths": paths, "review_observed": True})
        handler = object.__new__(EvalHandler)
        handler._worktree_cleanup = cleanup  # pyright: ignore[reportPrivateUsage]

    saved = store.get_claim(claim.id)
    assert saved is not None
    store.set_cleanup(claim.id, {**saved.cleanup, "review_observed": True})
    store.record_event(claim.id, "pending", "message")
    current = store.get_claim(claim.id)
    assert current is not None
    handler.cleanup(current, board_status="Done")
    assert root.exists()
    assert store.get_claim(claim.id).cleanup.get("complete") is not True  # type: ignore[union-attr]
    store.acknowledge_event(claim.id, "pending", "comment-1")
    current = store.get_claim(claim.id)
    assert current is not None
    handler.cleanup(current, board_status="Done")
    assert store.get_claim(claim.id).cleanup["complete"] is True  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["fix", "eval"])
@pytest.mark.parametrize("blocker", ["pending", "delivery-failure"])
def test_done_without_review_releases_when_reporting_is_delivered(
    tmp_path: Path, kind: str, blocker: str
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 15, "I15", "P15", kind, "fp", {}))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    run = store.reserve_run(claim.id, kind, reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="passed", result={})
    store.set_claim_lifecycle(claim.id, "settled", {})
    root = tmp_path / "clones" / claim.id
    root.mkdir(parents=True)
    now = datetime.now(UTC)
    saved = store.get_claim(claim.id)
    assert saved is not None
    paths = {
        name: {"source": str(tmp_path / name), "path": str(root / name)}
        for name in ("runner", "skills", "evals")
    }
    store.set_cleanup(
        claim.id,
        {
            **saved.cleanup,
            "done_observed_at": now.isoformat(),
            **({"paths": paths} if kind == "eval" else {}),
        },
    )
    if kind == "eval":
        store.record_event(claim.id, "rep-0:review-command", "review command")
        store.acknowledge_event(claim.id, "rep-0:review-command", "comment-1")
        manager = Mock()

        def remove_worktrees(_: object) -> dict[str, str]:
            shutil.rmtree(root)
            return {}

        manager.remove.side_effect = remove_worktrees
        cleanup = WorktreeCleanup(store, manager)
    else:
        cleanup = PullRequestCleanup(store, claim_directory=lambda _: root)
    handler = Mock()

    def release(_: Claim) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = [evidence / "logs"]
    if blocker == "pending":
        store.record_event(claim.id, "pending", "report")
    else:
        store.record_event(claim.id, "report", "report")
        store.record_delivery_failure(claim.id, "report", RuntimeError("HTTP 503"))
    controller = Mock()
    client = Mock()
    handlers = {kind: cast(WorkKindHandler, handler)}

    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        handlers,
        _local(tmp_path),
        {claim.id: "Done"},
        now,
    )
    assert root.exists()
    handler.release.assert_not_called()
    saved = store.get_claim(claim.id)
    assert saved is not None
    events = saved.reporting.get("events", {})
    assert isinstance(events, dict) and "review-expired" not in events

    if blocker == "pending":
        store.acknowledge_event(claim.id, "pending", "comment-2")
    else:
        store.acknowledge_event(claim.id, "report", "comment-2")
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        handlers,
        _local(tmp_path),
        {claim.id: "Done"},
        now,
    )
    assert not root.exists()
    handler.release.assert_called_once()
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is True
    events = saved.reporting.get("events", {})
    assert isinstance(events, dict) and "review-expired" not in events

    logs = evidence / "logs"
    logs.mkdir()
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        handlers,
        _local(tmp_path),
        {claim.id: "Done"},
        now + timedelta(days=13),
    )
    assert logs.exists()
    terminal.sweep(
        store,
        cast(Controller, controller),
        client,
        handlers,
        _local(tmp_path),
        {claim.id: "Done"},
        now + timedelta(days=14),
    )
    assert not logs.exists()


def test_fix_review_is_observed_while_a_report_is_undelivered(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 14, "I14", "P14", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {})
    root = tmp_path / "clones" / claim.id
    root.mkdir(parents=True)
    store.record_delivery_failure(claim.id, "report", RuntimeError("HTTP 503"))
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: root)

    # Review is observed even while delivery is failing, so a later Done can still release.
    assert cleanup.reconcile(claim.id, board_status="Review") is False
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["review_observed"] is True
    assert cleanup.reconcile(claim.id, board_status="Done") is False
    assert root.exists()


def test_permission_failure_records_error_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.work_kinds.pull_request import cleanup as cleanup_module

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 12, "I12", "P12", "fix", "fp", {}))
    root = tmp_path / "clones" / claim.id
    root.mkdir(parents=True)
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: root)
    original = cleanup_module._remove_tree  # pyright: ignore[reportPrivateUsage]
    calls = 0

    def denied(path: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("permission denied")
        original(path)

    monkeypatch.setattr(cleanup_module, "_remove_tree", denied)
    assert cleanup.release(claim.id) is False
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is False
    assert "permission denied" in str(saved.cleanup["last_error"])
    assert cleanup.release(claim.id) is True
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is True
    assert saved.cleanup["last_error"] is None
    assert not root.exists()


@pytest.mark.parametrize(
    ("lifecycle", "run_state", "failed_delivery"),
    [
        ("active", "finished", False),
        ("waiting", "finished", False),
        ("blocked", "finished", False),
        ("cancelled", "running", False),
        ("cancelled", "observing", False),
        ("cancelled", "finished", True),
    ],
)
def test_nonterminal_and_delivery_gates_keep_owned_clones(
    tmp_path: Path, lifecycle: str, run_state: str, failed_delivery: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 13, "I13", "P13", "fix", "fp", {}))
    root = tmp_path / "clones" / claim.id
    root.mkdir(parents=True)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path / "ev"))
    if run_state == "running":
        store.mark_running(run.id, {})
    elif run_state == "observing":
        store.report_uncertainty(run.id, "unknown")
    else:
        store.finish_run(run.id, execution_status="cancelled", result={})
    store.set_claim_lifecycle(claim.id, lifecycle, {})
    if failed_delivery:
        store.record_delivery_failure(claim.id, "report", RuntimeError("HTTP 503"))
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: root)
    handler = Mock()

    def release(_: object) -> bool:
        return cleanup.release(claim.id)

    handler.release.side_effect = release
    handler.retention_targets.return_value = []
    terminal.sweep(
        store,
        cast(Controller, Mock()),
        Mock(),
        {"fix": cast(WorkKindHandler, handler)},
        _local(tmp_path),
        {},
        datetime.now(UTC),
    )
    assert root.exists()
    assert handler.release.call_count == 0
