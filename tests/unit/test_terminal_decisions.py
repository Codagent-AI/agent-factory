"""Clock and ownership decisions before filesystem or registry effects."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_factory import retention, terminal
from agent_factory.config import (
    LimitsConfig,
    _optional_positive_int,  # pyright: ignore[reportPrivateUsage]
)
from agent_factory.fly.registry_cleanup import ownership_skip
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval.handler import EvalHandler


def _claim(tmp_path: Path):
    store = ClaimStore(tmp_path / "state.sqlite3")
    return store, store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "eval", "fp", {}))


def test_release_due_uses_terminal_transition_and_board_status(tmp_path: Path) -> None:
    store, claim = _claim(tmp_path)
    now = datetime.now(UTC)
    recent = now - timedelta(days=2)
    old = now - timedelta(days=31)
    assert not terminal.release_due(claim, "Review", old, now, 30)
    for lifecycle in ("cancelled", "superseded"):
        assert terminal.release_due(replace(claim, lifecycle=lifecycle), None, recent, now, 30)
    settled = replace(claim, lifecycle="settled")
    assert not terminal.release_due(settled, "Review", recent, now, 30)
    assert terminal.release_due(settled, "Review", old, now, 30)
    assert not terminal.release_due(settled, "Done", old, now, 30)
    observed = replace(settled, cleanup={"done_observed_at": now.isoformat()})
    assert terminal.release_due(observed, "Done", recent, now, 30)
    assert not terminal.release_due(
        replace(observed, cleanup={**observed.cleanup, "review_observed": True}),
        "Done",
        recent,
        now,
        30,
    )
    store.close()


@pytest.mark.parametrize(
    ("lifecycle", "board_status", "age", "done_age", "expected"),
    [
        ("superseded", None, 13, None, False),
        ("superseded", None, 14, None, True),
        ("settled", None, 29, None, False),
        ("settled", None, 30, None, True),
        ("settled", "Done", 40, 13, False),
        ("settled", "Done", 40, 14, True),
    ],
)
def test_prune_due_follows_each_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle: str,
    board_status: str | None,
    age: int,
    done_age: int | None,
    expected: bool,
) -> None:
    store, claim = _claim(tmp_path)
    now = datetime.now(UTC)
    cleanup: dict[str, object] = {
        "complete": True,
        "terminal_at": (now - timedelta(days=age)).isoformat(),
    }
    if done_age is not None:
        cleanup["done_observed_at"] = (now - timedelta(days=done_age)).isoformat()
    saved = replace(claim, lifecycle=lifecycle, cleanup=cleanup)
    local = Mock()
    local.limits = SimpleNamespace(evidence_retention_days=14, unreviewed_retention_days=30)
    pruned = Mock()
    monkeypatch.setattr(retention, "_prune", pruned)
    retention.prune_due(store, local, saved, board_status, now)  # type: ignore[arg-type]
    assert pruned.called is expected
    store.close()


def test_expiry_message_uses_result_links_and_local_fallback(tmp_path: Path) -> None:
    store, claim = _claim(tmp_path)
    handler = object.__new__(EvalHandler)
    message = handler.expiry_message(
        replace(
            claim,
            reporting={
                "events": {
                    "rep-1:review-command": {"body": "Run: ./human-review.sh"},
                    "rep-1:results:abc": {
                        "body": "rep-1 results saved to example/repo: https://github.com/example/repo/tree/abc/results"
                    },
                }
            },
        )
    )
    assert "Run: ./human-review.sh" in message
    assert "https://github.com/example/repo/tree/abc/results" in message
    assert "results saved to" not in message
    assert "retained result records on the factory Mac" in handler.expiry_message(claim)
    store.close()


def test_expiry_requires_a_delivered_review_command(tmp_path: Path) -> None:
    store, claim = _claim(tmp_path)
    assert not terminal.review_command_published(claim)
    pending = replace(
        claim,
        reporting={
            "events": {"rep-1:review-command": {"body": "Run: command", "comment_id": None}}
        },
    )
    assert not terminal.review_command_published(pending)
    delivered = replace(
        claim,
        reporting={
            "events": {"rep-1:review-command": {"body": "Run: command", "comment_id": "comment-1"}}
        },
    )
    assert terminal.review_command_published(delivered)
    assert not terminal.review_command_published(
        replace(
            claim,
            reporting={
                "events": {"rep-1:results:abc": {"body": "results", "comment_id": "comment-2"}}
            },
        )
    )
    store.close()


def test_unreviewed_retention_default_and_validation() -> None:
    assert LimitsConfig(0, 1, 1, 1, 1).unreviewed_retention_days == 30
    assert _optional_positive_int({}, "unreviewed_retention_days", "limits", 30) == 30
    with pytest.raises(ValueError):
        _optional_positive_int(
            {"unreviewed_retention_days": 0}, "unreviewed_retention_days", "limits", 30
        )


def test_registry_ownership_decision_checks_claim_tag_then_sharing() -> None:
    digest = "sha256:" + "a" * 64
    newer = "sha256:" + "b" * 64
    assert ownership_skip(digest, None, {"other": digest}, "claim-old") == (
        "untagged; ownership cannot be proven"
    )
    assert ownership_skip(digest, newer, {"other": digest}, "claim-old") == (
        f"claim tag now points to {newer}"
    )
    assert ownership_skip(digest, digest, {"claim-old": digest, "base": digest}, "claim-old") == (
        "shared with base"
    )
    assert ownership_skip(digest, digest, {"claim-old": digest}, "claim-old") is None


def test_pr_read_is_cached_within_a_tick(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 2, "I2", "P2", "fix", "fp", {}))
    store.set_claim_lifecycle(
        claim.id, "settled", {"pr": {"number": 2, "url": "https://example.test/pr/2"}}
    )
    saved = store.get_claim(claim.id)
    assert saved is not None
    client = Mock()
    client.get_pull_request.return_value.merged_at = "2026-01-01"
    cache: dict[str, bool] = {}
    assert terminal.sync_pending(store, saved, client, cache)
    assert terminal.sync_pending(store, saved, client, cache)
    client.get_pull_request.assert_called_once_with("example/repo", 2)
    store.close()


def test_quiescence_waits_for_execution_reporting_and_machine_disposal(tmp_path: Path) -> None:
    store, claim = _claim(tmp_path)
    run = store.reserve_run(
        claim.id, "rep-1", lane="low", reason="initial", evidence_path=str(tmp_path)
    )
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    client = Mock()
    saved = store.get_claim(claim.id)
    assert saved is not None and not terminal.quiescent(store, saved, client)
    store.finish_run(run.id, execution_status="cancelled", result={})
    store.record_event(claim.id, "report", "message")
    saved = store.get_claim(claim.id)
    assert saved is not None and not terminal.quiescent(store, saved, client)
    store.acknowledge_event(claim.id, "report", "comment-1")
    store.set_setting("runtime", f"fly:machine:{run.id}", {"machine_id": "machine-1"})
    saved = store.get_claim(claim.id)
    assert saved is not None and not terminal.quiescent(store, saved, client)
    store.clear_setting("runtime", f"fly:machine:{run.id}")
    assert terminal.quiescent(store, saved, client)
    client.get_pull_request.assert_not_called()
    store.close()
