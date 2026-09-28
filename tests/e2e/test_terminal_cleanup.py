"""CLI journeys for expiry and the first sweep over pre-upgrade history."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore
from tests.e2e.test_factory_cycle import (
    _cli,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
)


def test_unreviewed_eval_expiry_is_delivered_once_before_release(tmp_path: Path) -> None:
    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory" / "state.sqlite3")
    run = store.nonterminal_runs()[0]
    evidence = Path(run.evidence_path)
    try:
        deadline = time.monotonic() + 5
        while not (evidence / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        (evidence / "finish").touch()
        deadline = time.monotonic() + 5
        while store.nonterminal_runs() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not store.nonterminal_runs()
        _cli(config, env, "tick")
        claim = store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert "30 days" in board.read_text()
        root = tmp_path / "factory" / "worktrees" / claim.id
        assert root.exists()
        store.set_cleanup(
            claim.id,
            {**claim.cleanup, "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat()},
        )
        failure = """
from agent_factory.github import GitHubApiError, GitHubClient
original_comment = GitHubClient.create_comment
def fail_expiry(self, repository, number, body):
    if "human-review command has expired" in body:
        raise GitHubApiError("scripted expiry failure")
    return original_comment(self, repository, number, body)
GitHubClient.create_comment = fail_expiry
"""
        _cli(config, env, "tick", before_cli=failure)
        assert (root / "evals").exists()
        assert "review expiry not delivered" in status(store)
        _cli(config, env, "tick")
        saved = store.get_claim(claim.id)
        assert saved is not None and saved.cleanup["complete"] is True
        assert saved.cleanup["retention"]["pruned_at"]  # type: ignore[index]
        assert not (root / "runner").exists()
        assert not (root / "skills").exists()
        assert not (root / "evals").exists()
        comments = json.loads(board.read_text())["comments"]
        assert sum("review-expired" in comment["body"] for comment in comments) == 1
        _cli(config, env, "tick")
        comments = json.loads(board.read_text())["comments"]
        assert sum("review-expired" in comment["body"] for comment in comments) == 1
    finally:
        (evidence / "finish").touch()
        store.close()


def test_first_tick_backfills_and_prunes_off_board_history(tmp_path: Path) -> None:
    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "pause")
    store = ClaimStore(tmp_path / "factory" / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 2, "I2", "P2", "eval", "legacy", {}))
    evidence = tmp_path / "factory" / "artifacts" / claim.id
    (evidence / "logs").mkdir(parents=True)
    (evidence / "logs" / "old.log").write_text("old")
    (evidence / "result.json").write_text("keep")
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="cancelled", result={})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    store.set_cleanup(claim.id, {"complete": True})
    old = (datetime.now(UTC) - timedelta(days=16)).isoformat()
    store._connection.execute(  # pyright: ignore[reportPrivateUsage]
        "UPDATE claim SET cleanup_json = '{\"complete\":true}', updated_at = ? WHERE id = ?",
        (old, claim.id),
    )
    data = json.loads(board.read_text())
    data["items"] = []
    board.write_text(json.dumps(data))
    _cli(config, env, "tick")
    saved = store.get_claim(claim.id)
    assert saved is not None
    assert saved.cleanup["terminal_at"] == old
    assert saved.cleanup["retention"]["pruned_at"]  # type: ignore[index]
    assert not (evidence / "logs").exists()
    assert (evidence / "result.json").exists()
    _cli(config, env, "tick")
    assert store.get_claim(claim.id).cleanup["terminal_at"] == old  # type: ignore[union-attr]
    store.close()


def test_cli_registry_failure_retries_without_touching_other_tags(tmp_path: Path) -> None:
    from tests.fixtures.fly.api import FakeMachinesApi

    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "pause")
    store = ClaimStore(tmp_path / "factory" / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 4, "I4", "P4", "eval", "image", {}))
    tag = f"claim-{claim.id[:12]}"
    digest = "sha256:" + "a" * 64
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path / "ev"))
    store.update_progress(
        run.id, {"image_build": {"repository": "registry.fly.io/app", "tag": tag, "digest": digest}}
    )
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(claim.id, "settled", {})
    token = tmp_path / "fly-token"
    token.write_text("test-token")
    data = json.loads(board.read_text())
    data["items"] = []
    board.write_text(json.dumps(data))
    with FakeMachinesApi() as api:
        api.registry_enabled = True
        api.registry_tags = {
            tag: digest,
            "base": "sha256:" + "b" * 64,
            "deployment-one": "sha256:" + "c" * 64,
        }
        api.registry_delete_failures.append(500)
        before_cli = f"""
from dataclasses import replace
from pathlib import Path
from agent_factory.config import LocalConfig, FlyLocalConfig
from agent_factory.fly import api as fly_api
original_from_file = LocalConfig.from_file
LocalConfig.from_file = classmethod(lambda cls, path: replace(
    original_from_file(path),
    fly=FlyLocalConfig("app", "registry.fly.io/app:base", Path({str(token)!r})),
))
class LocalRegistryClient(fly_api.FlyMachinesClient):
    def __init__(self, app, token_file, **kw):
        super().__init__(app, token_file, registry_base_url={api.base_url!r}, **kw)
fly_api.FlyMachinesClient = LocalRegistryClient
"""
        _cli(config, env, "tick", before_cli=before_cli)
        saved = store.get_claim(claim.id)
        assert saved is not None and "error" in saved.cleanup["registry"][digest]  # type: ignore[index]
        assert "HTTP 500" in status(store)
        assert store.get_setting("runtime", "fly:cleanup-failed") is None
        _cli(config, env, "tick", before_cli=before_cli)
        assert tag not in api.registry_tags
        assert "base" in api.registry_tags and "deployment-one" in api.registry_tags
        before = len(api.requests)
        _cli(config, env, "tick", before_cli=before_cli)
        assert len(api.requests) == before
    store.close()


def test_cli_releases_fix_clones_then_reopens_cleanup_for_later_run(tmp_path: Path) -> None:
    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "pause")
    store = ClaimStore(tmp_path / "factory" / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 5, "I5", "P5", "fix", "fix", {}))
    root = tmp_path / "factory" / "clones" / claim.id
    for number in (0, 1):
        path = root / str(number)
        path.mkdir(parents=True)
        (path / "owned").write_text("remove")
    store.set_preparation(claim.id, {"clones": {"target": str(root / "1")}})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    saved = store.get_claim(claim.id)
    assert saved is not None
    store.set_cleanup(
        claim.id,
        {**saved.cleanup, "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat()},
    )
    data = json.loads(board.read_text())
    data["items"] = []
    board.write_text(json.dumps(data))
    _cli(config, env, "tick")
    assert not root.exists()
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.cleanup["complete"] is True
    review = store.reserve_run(
        claim.id, "fix", reason="review", evidence_path=str(tmp_path / "review")
    )
    store.set_claim_lifecycle(claim.id, "active", {})
    assert store.get_claim(claim.id).cleanup["complete"] is False  # type: ignore[union-attr]
    fresh = root / "2"
    fresh.mkdir(parents=True)
    (fresh / "owned").write_text("later")
    store.set_preparation(claim.id, {"clones": {"target": str(fresh)}})
    store.finish_run(review.id, execution_status="completed", result={})
    store.set_setting("consumed-results", review.id, {"complete": True})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    assert store.get_claim(claim.id).cleanup["terminal_at"]  # type: ignore[union-attr]
    _cli(config, env, "tick")
    assert fresh.exists()
    saved = store.get_claim(claim.id)
    assert saved is not None
    store.set_cleanup(
        claim.id,
        {**saved.cleanup, "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat()},
    )
    _cli(config, env, "tick")
    assert not root.exists()
    store.close()
