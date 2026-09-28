"""INT-002: the store sweep releases all owned clone attempts off the board."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import Mock

from agent_factory import terminal
from agent_factory.controller import Controller
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.base import WorkKindHandler
from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup
from tests.integration.test_retention import _local  # pyright: ignore[reportPrivateUsage]


def test_off_board_cancelled_claim_releases_every_attempt_and_keeps_other_files(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "fix", "fp", {}))
    clone_root = tmp_path / "factory" / "clones"
    claim_dir = clone_root / claim.id
    for number in range(3):
        path = claim_dir / str(number)
        path.mkdir(parents=True)
        (path / "file").write_text("owned")
    other = clone_root / "another-claim"
    other.mkdir()
    (other / "file").write_text("keep")
    mirror = tmp_path / "factory" / "mirrors" / "repo.git"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("keep")
    store.set_preparation(claim.id, {"clones": {"target": str(claim_dir / "2")}})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    cleanup = PullRequestCleanup(store, claim_directory=lambda _: claim_dir)
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
