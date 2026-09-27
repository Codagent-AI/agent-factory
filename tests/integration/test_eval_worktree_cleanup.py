"""INT-002: idle release of eval worktrees over real git worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

from agent_factory.store import Claim, ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import GitWorktreeManager, PreparedWorktrees
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler
from tests.integration.test_and_scene_adapter import _sources  # pyright: ignore[reportPrivateUsage]


def _handler(store: ClaimStore, manager: GitWorktreeManager) -> EvalHandler:
    defaults = EvalDefaults(
        "main", "main", {"lead": "a:b:c", "implementor": "a:b:c", "tester": "a:b:c"}, False, 1
    )
    handler = EvalHandler(defaults, harness_ref="e" * 40, manager=manager)
    handler.attach_store(store)
    return handler


def _get(store: ClaimStore, claim_id: str) -> Claim:
    claim = store.get_claim(claim_id)
    assert claim is not None
    return claim


def _listed(source: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), "worktree", "list"], text=True
    ).strip()


def _prepared(
    store: ClaimStore,
    manager: GitWorktreeManager,
    handler: EvalHandler,
    revisions: dict[str, str],
    number: int,
    item: str,
) -> tuple[Claim, PreparedWorktrees]:
    claim = store.create_claim(
        ClaimDraft("example/evals", number, f"I{number}", item, "eval", f"fp{number}", {})
    )
    trees = manager.prepare(claim.id, revisions)
    assert handler._worktree_cleanup is not None  # pyright: ignore[reportPrivateUsage]
    handler._worktree_cleanup.record(claim.id, trees)  # pyright: ignore[reportPrivateUsage]
    return claim, trees


def test_idle_release_removes_terminal_claims_worktrees_only(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    superseded, superseded_trees = _prepared(store, manager, handler, revisions, 1, "P1")
    replacing = store.supersede_and_create(
        superseded.id, ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp1b", {})
    )
    replacing_trees = manager.prepare(replacing.id, revisions)
    handler._worktree_cleanup.record(replacing.id, replacing_trees)  # pyright: ignore
    cancelled, cancelled_trees = _prepared(store, manager, handler, revisions, 2, "P2")
    store.set_claim_lifecycle(cancelled.id, "cancelled", {"verdict": "cancelled"})
    settled, settled_trees = _prepared(store, manager, handler, revisions, 3, "P3")
    store.set_claim_lifecycle(settled.id, "settled", {"verdict": "pending-human-review"})
    terminal = [(superseded, superseded_trees), (cancelled, cancelled_trees)]
    terminal.append((settled, settled_trees))

    for claim, trees in terminal:
        handler.cleanup(_get(store, claim.id), board_status="Review")
        assert all(path.exists() for path in trees.paths())

    for claim, trees in terminal:
        handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)
        assert not any(path.exists() for path in trees.paths())
        saved = _get(store, claim.id)
        assert saved.cleanup["complete"] is True
        assert saved.cleanup["released_by"] == "idle"
        assert saved.cleanup["last_error"] is None
    for source in (sources.runner, sources.skills, sources.evals):
        listing = _listed(source)
        for _, trees in terminal:
            assert trees.claim_id not in listing
        assert replacing.id in listing
    assert all(path.exists() for path in replacing_trees.paths())

    before = {claim.id: _get(store, claim.id).cleanup for claim, _ in terminal}
    for claim, _ in terminal:
        handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)
    assert {claim.id: _get(store, claim.id).cleanup for claim, _ in terminal} == before


def test_idle_release_of_a_claim_with_nothing_recorded_completes(tmp_path: Path) -> None:
    sources, _ = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    handler = _handler(store, GitWorktreeManager(tmp_path / "factory", sources))
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {}))
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})

    handler.cleanup(_get(store, claim.id), board_status="", idle=True)

    saved = _get(store, claim.id)
    assert saved.cleanup["complete"] is True
    assert saved.cleanup["released_by"] == "idle"


def test_idle_release_never_touches_active_claims(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, trees = _prepared(store, manager, handler, revisions, 1, "P1")

    handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)

    assert all(path.exists() for path in trees.paths())
    assert _get(store, claim.id).cleanup["complete"] is False


def test_failed_idle_removal_is_recorded_and_retried(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, trees = _prepared(store, manager, handler, revisions, 1, "P1")
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    parent = trees.runner.parent
    mode = parent.stat().st_mode
    parent.chmod(0o555)
    try:
        handler.cleanup(_get(store, claim.id), board_status="", idle=True)
    finally:
        parent.chmod(mode)

    saved = _get(store, claim.id)
    assert saved.cleanup["complete"] is False
    assert saved.cleanup["last_error"]
    assert trees.runner.exists()

    handler.cleanup(_get(store, claim.id), board_status="", idle=True)

    saved = _get(store, claim.id)
    assert saved.cleanup["complete"] is True
    assert saved.cleanup["last_error"] is None
    assert not any(path.exists() for path in trees.paths())


def _settled_with_command(
    store: ClaimStore, manager: GitWorktreeManager, handler: EvalHandler, revisions: dict[str, str]
) -> tuple[Claim, PreparedWorktrees]:
    claim, trees = _prepared(store, manager, handler, revisions, 1, "P1")
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    store.record_event(claim.id, "rep-1:review-command", "Run: human-review.sh")
    store.acknowledge_event(claim.id, "rep-1:review-command", "c1")
    return claim, trees


def _lapse_events(store: ClaimStore, claim_id: str) -> list[str]:
    events = cast(dict[str, object], _get(store, claim_id).reporting.get("events"))
    return [key for key in events if key == "review-window-lapsed"]


def test_idle_release_of_a_reviewable_claim_in_review_records_one_lapse_event(
    tmp_path: Path,
) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, _ = _settled_with_command(store, manager, handler, revisions)

    handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)
    handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)

    assert _lapse_events(store, claim.id) == ["review-window-lapsed"]
    body = [event.body for event in store.pending_events(claim.id)]
    assert len(body) == 1
    assert "human-review window has ended" in body[0]
    assert "released" in body[0]


def test_no_lapse_event_off_the_board(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, trees = _settled_with_command(store, manager, handler, revisions)

    handler.cleanup(_get(store, claim.id), board_status="", idle=True, on_board=False)

    assert not any(path.exists() for path in trees.paths())
    assert _lapse_events(store, claim.id) == []
    assert store.pending_events(claim.id) == []


def test_no_lapse_event_after_a_done_cleanup(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, trees = _settled_with_command(store, manager, handler, revisions)

    handler.cleanup(_get(store, claim.id), board_status="Review")
    handler.cleanup(_get(store, claim.id), board_status="Done")

    assert not any(path.exists() for path in trees.paths())
    assert _lapse_events(store, claim.id) == []


def test_no_lapse_event_without_a_review_command(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, _ = _prepared(store, manager, handler, revisions, 1, "P1")
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "failed"})

    handler.cleanup(_get(store, claim.id), board_status="Review", idle=True)

    assert _get(store, claim.id).cleanup["complete"] is True
    assert "events" not in _get(store, claim.id).reporting


def test_a_release_drops_the_stale_size_estimate(tmp_path: Path) -> None:
    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    handler = _handler(store, manager)
    claim, _ = _prepared(store, manager, handler, revisions, 1, "P1")
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    store.set_cleanup(claim.id, {**_get(store, claim.id).cleanup, "size_estimate": {"bytes": 999}})

    handler.cleanup(_get(store, claim.id), board_status="", idle=True)

    assert "size_estimate" not in _get(store, claim.id).cleanup
