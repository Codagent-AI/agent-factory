"""CLI journeys for expiry and the first sweep over pre-upgrade history."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore
from tests.e2e.test_factory_cycle import (
    _cli,  # pyright: ignore[reportPrivateUsage]
    _finish,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
    _status,  # pyright: ignore[reportPrivateUsage]
)


def test_unreviewed_eval_expiry_is_delivered_once_before_release(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
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
        item = json.loads(board.read_text())["items"][0]
        assert item["content"]["state"] == "OPEN"
        assert next(
            value["optionId"]
            for value in item["fieldValues"]["nodes"]
            if value["field"]["id"] == shared.project.status.id
        ) == shared.project.status.option("review")
        assert next(
            value["optionId"]
            for value in item["fieldValues"]["nodes"]
            if value["field"]["id"] == shared.project.verdict.id
        ) == shared.project.verdict.option("pending-human-review")
        _cli(config, env, "tick")
        comments = json.loads(board.read_text())["comments"]
        assert sum("review-expired" in comment["body"] for comment in comments) == 1
    finally:
        (evidence / "finish").touch()
        store.close()


def test_failed_eval_has_no_expiry_comment_but_is_released(tmp_path: Path) -> None:
    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    run = store.nonterminal_runs()[0]
    artifact = Path(run.evidence_path)
    try:
        (artifact / "finish").write_text(
            json.dumps(
                {
                    "evaluation_status": "complete",
                    "product_verdict": "failed",
                    "automated_subtotal": 0,
                }
            )
        )
        _finish(store, artifact)
        _cli(config, env, "tick")
        claim = store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert not any(
            "human-review.sh" in c["body"] for c in json.loads(board.read_text())["comments"]
        )
        store.set_cleanup(
            claim.id,
            {**claim.cleanup, "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat()},
        )
        _cli(config, env, "tick")
        saved = store.get_claim(claim.id)
        assert saved is not None and saved.cleanup["complete"] is True
        assert not any(
            "review-expired" in c["body"] for c in json.loads(board.read_text())["comments"]
        )
    finally:
        (artifact / "finish").touch()
        store.close()


def test_done_eval_uses_done_observation_for_evidence_retention(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    run = store.nonterminal_runs()[0]
    artifact = Path(run.evidence_path)
    try:
        (artifact / "finish").touch()
        _finish(store, artifact)
        _cli(config, env, "tick")
        claim = store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        (artifact / "logs").mkdir(exist_ok=True)
        (artifact / "logs/old.log").write_text("prune")
        store.set_cleanup(
            claim.id,
            {
                **claim.cleanup,
                "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat(),
            },
        )
        _status(board, shared, "done")
        _cli(config, env, "tick")
        assert (artifact / "result.json").exists()
        assert not any(
            "review-expired" in c["body"] for c in json.loads(board.read_text())["comments"]
        )
        saved = store.get_claim(claim.id)
        assert saved is not None and isinstance(saved.cleanup.get("done_observed_at"), str)
        _cli(config, env, "tick")
        assert (artifact / "result.json").exists()
        assert (artifact / "run-state.json").exists()
        assert (artifact / "logs").exists()
        assert not saved.cleanup.get("retention", {}).get("pruned_at")  # type: ignore[union-attr]
        store.set_cleanup(
            claim.id,
            {
                **saved.cleanup,
                "done_observed_at": (datetime.now(UTC) - timedelta(days=13)).isoformat(),
            },
        )
        _cli(config, env, "tick")
        assert (artifact / "logs").exists()
        saved = store.get_claim(claim.id)
        assert saved is not None
        store.set_cleanup(
            claim.id,
            {
                **saved.cleanup,
                "done_observed_at": (datetime.now(UTC) - timedelta(days=14, minutes=1)).isoformat(),
            },
        )
        _cli(config, env, "tick")
        assert not (artifact / "logs").exists()
        assert (artifact / "result.json").exists()
    finally:
        (artifact / "finish").touch()
        store.close()


def test_done_eval_waits_for_review_command_delivery(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    run = store.nonterminal_runs()[0]
    artifact = Path(run.evidence_path)
    failure = """
from agent_factory.github import GitHubApiError, GitHubClient
original_comment = GitHubClient.create_comment
def fail_command(self, repository, number, body):
    if "human-review.sh" in body:
        raise GitHubApiError("scripted command failure")
    return original_comment(self, repository, number, body)
GitHubClient.create_comment = fail_command
"""
    try:
        deadline = time.monotonic() + 10
        while not (artifact / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (artifact / "started").exists()
        (artifact / "finish").touch()
        _finish(store, artifact)
        _cli(config, env, "tick", before_cli=failure)
        claim = store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "settled"
        assert any(event.key.endswith(":review-command") for event in store.pending_events(claim.id))
        _status(board, shared, "done")
        _cli(config, env, "tick", before_cli=failure)
        assert any(event.key.endswith(":review-command") for event in store.pending_events(claim.id))
        root = tmp_path / "factory/worktrees" / claim.id
        assert (root / "runner").exists()
        _cli(config, env, "tick")
        _cli(config, env, "tick")
        assert not (root / "runner").exists()
        comments = json.loads(board.read_text())["comments"]
        assert sum("human-review.sh" in c["body"] for c in comments) == 1
    finally:
        if artifact.is_dir():
            (artifact / "finish").touch()
        store.close()


def test_first_tick_backfills_and_prunes_off_board_history(tmp_path: Path) -> None:
    config, board, env, _shared = _setup(tmp_path)
    _cli(config, env, "pause")
    store = ClaimStore(tmp_path / "factory" / "state.sqlite3")
    now = datetime.now(UTC)
    cases: list[tuple[str, str, int, Path, Path | None, str]] = []
    for number, (kind, lifecycle, age) in enumerate(
        (
            ("eval", "cancelled", 16),
            ("fix", "cancelled", 16),
            ("eval", "superseded", 16),
            ("fix", "superseded", 16),
            ("fix", "settled", 31),
            ("eval", "cancelled", 2),
            ("eval", "active", 40),
            ("fix", "waiting", 40),
            ("fix", "blocked", 40),
        ),
        start=2,
    ):
        claim = store.create_claim(
            ClaimDraft("example/evals", number, f"I{number}", f"P{number}", kind, "legacy", {})
        )
        evidence = tmp_path / "factory" / "artifacts" / claim.id
        records = evidence if kind == "eval" else evidence / "attempt-1"
        (records / "logs").mkdir(parents=True)
        (records / "logs" / "old.log").write_text("old")
        (records / "result.json").write_text("keep")
        run = store.reserve_run(
            claim.id,
            "rep-1" if kind == "eval" else "fix",
            reason="initial",
            evidence_path=str(evidence),
        )
        store.finish_run(run.id, execution_status="cancelled", result={})
        store.set_setting("consumed-results", run.id, {"complete": True})
        owned: Path | None = None
        if kind == "fix":
            owned = tmp_path / "factory/clones" / claim.id
            (owned / "0").mkdir(parents=True)
            (owned / "0/owned").write_text("owned")
            store.set_preparation(claim.id, {"clones": {"target": str(owned / "0")}})
        elif lifecycle in {"cancelled", "superseded"}:
            owned = tmp_path / "factory/worktrees" / claim.id
            paths: dict[str, dict[str, str]] = {}
            for name in ("runner", "skills", "evals"):
                source = tmp_path / name
                target = owned / name
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "-C", str(source), "worktree", "add", "--detach", str(target), "HEAD"],
                    check=True,
                    capture_output=True,
                )
                paths[name] = {"source": str(source), "path": str(target)}
            store.set_preparation(claim.id, {"worktrees": paths})
            store.set_cleanup(claim.id, {"paths": paths, "complete": False})
        store.set_claim_lifecycle(claim.id, lifecycle, {})
        previous = (now - timedelta(days=age)).isoformat()
        store._connection.execute(  # pyright: ignore[reportPrivateUsage]
            "UPDATE claim SET cleanup_json = json_remove(cleanup_json, '$.terminal_at'), "
            "updated_at = ? WHERE id = ?",
            (previous, claim.id),
        )
        cases.append((claim.id, lifecycle, age, evidence, owned, previous))
    data = json.loads(board.read_text())
    data["items"] = []
    board.write_text(json.dumps(data))
    schema = store._connection.execute("PRAGMA user_version").fetchone()[0]  # pyright: ignore[reportPrivateUsage]
    _cli(config, env, "tick")
    for claim_id, lifecycle, age, evidence, owned, previous in cases:
        saved = store.get_claim(claim_id)
        assert saved is not None
        if lifecycle in {"cancelled", "superseded", "settled"}:
            assert saved.cleanup.get("terminal_at") == previous, (
                claim_id,
                lifecycle,
                saved.cleanup,
            )
            assert saved.cleanup["complete"] is True
            if owned is not None:
                assert not (owned / ("runner" if saved.kind == "eval" else "0")).exists()
            records = evidence if saved.kind == "eval" else evidence / "attempt-1"
            assert (records / "result.json").exists()
            if age >= 14:
                assert saved.cleanup["retention"]["pruned_at"]  # type: ignore[index]
                assert not (records / "logs").exists()
            else:
                assert (records / "logs").exists()
        else:
            assert owned is None or owned.exists()
            records = evidence if saved.kind == "eval" else evidence / "attempt-1"
            assert (records / "logs").exists()
    snapshot = [(c.id, c.cleanup) for c in store.all_claims()]
    comments = json.loads(board.read_text())["comments"]
    _cli(config, env, "tick")
    assert [(c.id, c.cleanup) for c in store.all_claims()] == snapshot
    assert json.loads(board.read_text())["comments"] == comments
    assert store._connection.execute("PRAGMA user_version").fetchone()[0] == schema  # pyright: ignore[reportPrivateUsage]
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
        registry = dict(cast(dict[str, dict[str, object]], saved.cleanup["registry"]))
        registry[digest] = {**registry[digest], "next_retry_at": "2000-01-01T00:00:00+00:00"}
        store.set_cleanup(claim.id, {**saved.cleanup, "registry": registry})
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
