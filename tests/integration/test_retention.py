"""INT-005: age-based evidence retention over a real ClaimStore and evidence trees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from agent_factory import retention
from agent_factory.config import LocalConfig
from agent_factory.store import Claim, ClaimDraft, ClaimStore

_RETENTION_DAYS = 14


def _get(store: ClaimStore, claim_id: str) -> Claim:
    claim = store.get_claim(claim_id)
    assert claim is not None
    return claim


def _retention(claim: Claim) -> dict[str, object]:
    value = claim.cleanup.get("retention")
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _local(tmp_path: Path, *, retention_days: int = _RETENTION_DAYS) -> LocalConfig:
    text = f"""\
shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path / "factory"}"

[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"

[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 60
total_seconds = 60
codex_reset_fallback_seconds = 60
evidence_retention_days = {retention_days}

[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
"""
    return LocalConfig.from_toml(text)


def _make_fix_tree(
    root: Path, claim_id: str, *, attempt: int, unknown: str = "future.json"
) -> Path:
    evidence = root / f"{claim_id}-fix"
    attempt_dir = evidence / f"attempt-{attempt}"
    for rel in (
        "logs/agent-runner.log",
        "factory-suite.log",
        "agent-runner/workflows/factory-fix-v1.0.yaml",
        "agent-runner-session/state.json",
        ".runtime/session-state/x",
        "input/issue.json",
        "fix-outcome.json",
        "host-provenance.json",
        unknown,
    ):
        target = attempt_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    return evidence


def _make_eval_tree(root: Path, claim_id: str, *, rep: int, unknown: str = "future.json") -> Path:
    evidence = root / f"{claim_id}-rep-{rep}"
    for rel in (
        "logs/harness.log",
        "factory-suite.log",
        ".runtime/agent-runner-projects/p",
        ".runtime/agent-session-state/s",
        ".runtime/judge-workspace/w",
        ".runtime/judge/j",
        ".runtime/candidate-worktree/HEAD",
        "result.json",
        "run-state.json",
        "phases/phase-1.json",
        "evidence/e.json",
        "neutral/n.json",
        "report.html",
        unknown,
    ):
        target = evidence / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    return evidence


def _reserve_and_finish(store: ClaimStore, claim_id: str, unit_key: str, evidence: Path) -> None:
    run = store.reserve_run(claim_id, unit_key, reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={})


def _kept_paths(evidence: Path, *, kind: str) -> list[Path]:
    if kind == "fix":
        base = evidence / "attempt-1"
        return [
            base / "input" / "issue.json",
            base / "fix-outcome.json",
            base / "host-provenance.json",
            base / "future.json",
        ]
    return [
        evidence / "result.json",
        evidence / "run-state.json",
        evidence / "phases" / "phase-1.json",
        evidence / "evidence" / "e.json",
        evidence / "neutral" / "n.json",
        evidence / "report.html",
        evidence / "future.json",
    ]


def _removed_paths(evidence: Path, *, kind: str) -> list[Path]:
    if kind == "fix":
        base = evidence / "attempt-1"
        return [
            base / "logs",
            base / "factory-suite.log",
            base / "agent-runner",
            base / "agent-runner-session",
            base / ".runtime",
        ]
    return [
        evidence / "logs",
        evidence / "factory-suite.log",
        evidence / ".runtime" / "agent-runner-projects",
        evidence / ".runtime" / "agent-session-state",
        evidence / ".runtime" / "judge-workspace",
        evidence / ".runtime" / "judge",
        evidence / ".runtime" / "candidate-worktree",
    ]


def test_first_done_observation_writes_marker_and_removes_nothing(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)
    now = datetime.now(UTC)

    retention.reconcile(store, local, _get(store, claim.id), "Done", now)

    claim = _get(store, claim.id)
    assert claim is not None
    assert claim.cleanup.get("done_observed_at") is not None
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_nothing_removed_before_retention_period_elapses(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    almost = start + timedelta(days=13)
    retention.reconcile(store, local, _get(store, claim.id), "Done", almost)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_prunes_fix_attempt_evidence_after_retention_period(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()
    for path in _kept_paths(evidence, kind="fix"):
        assert path.exists()
    claim = _get(store, claim.id)
    assert _retention(claim)["pruned_at"] is not None


def test_prunes_feature_evidence_but_keeps_outcome_and_input(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "feature", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    (evidence / "attempt-1" / "fix-outcome.json").rename(
        evidence / "attempt-1" / "feature-outcome.json"
    )
    _reserve_and_finish(store, claim.id, "feature", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))
    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()
    assert (evidence / "attempt-1" / "feature-outcome.json").exists()
    assert (evidence / "attempt-1" / "input" / "issue.json").exists()


def test_prunes_eval_repetition_evidence_after_retention_period(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {}))
    evidence = _make_eval_tree(tmp_path / "factory" / "artifacts", claim.id, rep=1)
    _reserve_and_finish(store, claim.id, "rep-1", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="eval"):
        assert not path.exists()
    for path in _kept_paths(evidence, kind="eval"):
        assert path.exists()


def test_observing_review_after_done_clears_marker_and_a_later_done_restarts_clock(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    retention.reconcile(store, local, _get(store, claim.id), "Review", start + timedelta(days=1))
    claim = _get(store, claim.id)
    assert claim is not None and claim.cleanup.get("done_observed_at") is None

    restart = start + timedelta(days=2)
    retention.reconcile(store, local, _get(store, claim.id), "Done", restart)
    claim = _get(store, claim.id)
    assert claim is not None
    assert claim.cleanup["done_observed_at"] == restart.isoformat()

    # 14 days from the original Done, but only 13 from the restarted clock: not yet eligible.
    almost = restart + timedelta(days=13)
    retention.reconcile(store, local, _get(store, claim.id), "Done", almost)
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()

    later = restart + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)
    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()


def test_a_claim_never_visited_is_never_pruned(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)

    claim = _get(store, claim.id)
    assert claim is not None and claim.cleanup.get("done_observed_at") is None
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_nonterminal_run_leaves_evidence_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    # A second attempt is still reserved (non-terminal).
    store.reserve_run(claim.id, "fix", reason="recovery", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_pending_reporting_event_leaves_evidence_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)
    store.record_event(claim.id, "handoff", "pending report")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_pending_delivery_failure_leaves_evidence_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    _settle_with_cleanup_complete(store, claim.id)
    store.record_delivery_failure(claim.id, "handoff", RuntimeError("delivery boom"))
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_session_dir_inside_the_attempt_directory_is_pruned(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    session_dir = evidence / "attempt-1" / "custom-session"
    session_dir.mkdir()
    (session_dir / "state.json").write_text("x")
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={"session_dir": str(session_dir)})
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    assert not session_dir.exists()


def test_session_dir_outside_the_evidence_tree_is_never_deleted(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    outside = tmp_path / "not-evidence"
    outside.mkdir()
    (outside / "do-not-delete.txt").write_text("precious")
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={"session_dir": str(outside)})
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    assert outside.exists()
    assert (outside / "do-not-delete.txt").exists()


@pytest.mark.parametrize("kind", ["fix", "feature"])
def test_incomplete_sync_leaves_evidence_untouched(tmp_path: Path, kind: str) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", kind, "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, kind, reason="initial", evidence_path=str(evidence))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"pr": {"url": "https://github.com/example/work/pull/1", "number": 1}},
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    _settle_with_cleanup_complete(store, claim.id)
    store.set_claim_sync(claim.id, {"attempted": True, "blocked_reason": "uncommitted changes"})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_incomplete_cleanup_on_a_settled_claim_leaves_evidence_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.set_cleanup(claim.id, {"review_observed": True, "complete": False})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_superseded_claim_waits_for_cleanup_complete(tmp_path: Path) -> None:
    """Every lifecycle now waits on its released workspace; idle release sets `complete`."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    store.supersede_and_create(
        claim.id, ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp2", {})
    )
    start = datetime.now(UTC)
    superseded = _get(store, claim.id)
    assert superseded is not None and superseded.lifecycle == "superseded"
    retention.reconcile(store, local, superseded, "Done", start)

    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()

    _mark_complete(store, claim.id)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)

    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()


def test_failed_removal_records_an_error_and_retries_until_removable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    later = start + timedelta(days=14)
    logs_dir = evidence / "attempt-1" / "logs"
    remove_tree = retention.remove_tree

    def fail_on_logs(path: Path | str) -> None:
        if Path(path) == logs_dir.resolve():
            raise OSError("device busy")
        remove_tree(path)

    # Read-only trees are repaired, so the failure is injected at the removal itself.
    monkeypatch.setattr(retention, "remove_tree", fail_on_logs)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)
    monkeypatch.setattr(retention, "remove_tree", remove_tree)

    claim = _get(store, claim.id)
    assert _retention(claim).get("pruned_at") is None
    assert _retention(claim)["errors"]

    retention.reconcile(store, local, _get(store, claim.id), "Done", later + timedelta(seconds=1))
    claim = _get(store, claim.id)
    assert _retention(claim)["pruned_at"] is not None
    assert not logs_dir.exists()


def test_second_reconcile_after_success_removes_and_records_nothing_new(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)
    later = start + timedelta(days=14)
    retention.reconcile(store, local, _get(store, claim.id), "Done", later)
    claim = _get(store, claim.id)
    first_retention = _retention(claim)

    (evidence / "attempt-1" / "future.json").write_text("still here")
    retention.reconcile(store, local, _get(store, claim.id), "Done", later + timedelta(days=1))

    claim = _get(store, claim.id)
    assert _retention(claim) == first_retention
    assert (evidence / "attempt-1" / "future.json").exists()


def test_cancelled_claim_waits_for_its_release_before_pruning(tmp_path: Path) -> None:
    """A cancelled claim is pruned only once its cancellation or idle release completed."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="cancelled", result={"reason": "cancelled"})
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()

    _mark_complete(store, claim.id)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()
    for path in _kept_paths(evidence, kind="fix"):
        assert path.exists()


def _mark_complete(store: ClaimStore, claim_id: str) -> None:
    claim = _get(store, claim_id)
    store.set_cleanup(claim_id, {**claim.cleanup, "complete": True})


def _settle_with_cleanup_complete(store: ClaimStore, claim_id: str) -> None:
    store.set_claim_lifecycle(claim_id, "settled", {"verdict": "pending-human-review"})
    store.set_cleanup(claim_id, {"review_observed": True, "complete": True})


def test_waiting_claim_is_never_pruned_even_when_its_card_is_done(tmp_path: Path) -> None:
    """A waiting claim may still retry or recover from its evidence; retention is for terminal
    claims only."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    _reserve_and_finish(store, claim.id, "fix", evidence)
    store.set_claim_lifecycle(claim.id, "waiting", {"verdict": "infra-error"})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()


def test_session_dir_equal_to_the_attempt_directory_never_removes_the_attempt(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"session_dir": str(evidence / "attempt-1")},
    )
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    for path in _kept_paths(evidence, kind="fix"):
        assert path.exists()


def test_prunes_the_claim_level_suite_log_of_a_fix_claim(tmp_path: Path) -> None:
    """The supervisor appends the launched process's output to <evidence>/factory-suite.log,
    one level above the attempt directories."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    (evidence / "factory-suite.log").write_text("x")
    _reserve_and_finish(store, claim.id, "fix", evidence)
    _settle_with_cleanup_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    assert not (evidence / "factory-suite.log").exists()


def test_cancelled_claim_with_a_recorded_pr_prunes_without_a_sync(tmp_path: Path) -> None:
    """Post-merge sync only ever runs for settled claims, so a cancelled claim that recorded
    a PR must not wait on a sync that can never complete."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(
        run.id,
        execution_status="cancelled",
        result={"pr": {"url": "https://github.com/example/work/pull/1", "number": 1}},
    )
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    _mark_complete(store, claim.id)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)

    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()


# INT-001: the idle path, its guards, and the candidate worktree.

_ABANDONED_DAYS = 3
_SETTLED_DAYS = 14


def _terminal_claim(
    store: ClaimStore, tmp_path: Path, lifecycle: str, *, kind: str = "eval", item: str = "P1"
) -> tuple[str, Path]:
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", item, kind, "fp", {}))
    root = tmp_path / "factory" / "artifacts"
    if kind == "eval":
        evidence = _make_eval_tree(root, claim.id, rep=1)
        _reserve_and_finish(store, claim.id, "rep-1", evidence)
    else:
        evidence = _make_fix_tree(root, claim.id, attempt=1)
        _reserve_and_finish(store, claim.id, kind, evidence)
    if lifecycle == "superseded":
        store.supersede_and_create(
            claim.id, ClaimDraft("example/evals", 1, "I1", item, kind, "fp2", {})
        )
    else:
        store.set_claim_lifecycle(claim.id, lifecycle, {"verdict": "pending-human-review"})
    store.set_cleanup(claim.id, {"complete": True})
    return claim.id, evidence


def _pruned(evidence: Path, *, kind: str = "eval") -> bool:
    return not any(path.exists() for path in _removed_paths(evidence, kind=kind))


def _untouched(evidence: Path, *, kind: str = "eval") -> bool:
    return all(path.exists() for path in _removed_paths(evidence, kind=kind))


@pytest.mark.parametrize("lifecycle", ["cancelled", "superseded"])
@pytest.mark.parametrize(("board_status", "on_board"), [("Review", True), ("", False)])
def test_abandoned_claims_prune_after_the_abandoned_period(
    tmp_path: Path, lifecycle: str, board_status: str, on_board: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, lifecycle)
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), board_status, start, on_board=on_board)
    assert _get(store, claim_id).cleanup["terminal_observed_at"] == start.isoformat()

    early = start + timedelta(days=_ABANDONED_DAYS - 1)
    retention.reconcile(store, local, _get(store, claim_id), board_status, early, on_board=on_board)
    assert _untouched(evidence)

    due = start + timedelta(days=_ABANDONED_DAYS)
    retention.reconcile(store, local, _get(store, claim_id), board_status, due, on_board=on_board)
    assert _pruned(evidence)
    for path in _kept_paths(evidence, kind="eval"):
        assert path.exists()


@pytest.mark.parametrize(("board_status", "on_board"), [("Review", True), ("", False)])
def test_settled_claim_outside_done_prunes_after_the_settled_period(
    tmp_path: Path, board_status: str, on_board: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "settled")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), board_status, start, on_board=on_board)

    early = start + timedelta(days=_SETTLED_DAYS - 1)
    retention.reconcile(store, local, _get(store, claim_id), board_status, early, on_board=on_board)
    assert _untouched(evidence)

    due = start + timedelta(days=_SETTLED_DAYS)
    retention.reconcile(store, local, _get(store, claim_id), board_status, due, on_board=on_board)
    assert _pruned(evidence)


def test_settled_claim_in_done_is_judged_only_by_the_done_path(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path, retention_days=5)
    claim_id, evidence = _terminal_claim(store, tmp_path, "settled")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)

    # Moved to Done long after its idle period: the Done observation starts a fresh clock.
    moved = start + timedelta(days=_SETTLED_DAYS + 10)
    retention.reconcile(store, local, _get(store, claim_id), "Done", moved)
    retention.reconcile(store, local, _get(store, claim_id), "Done", moved + timedelta(days=4))
    assert _untouched(evidence)

    retention.reconcile(store, local, _get(store, claim_id), "Done", moved + timedelta(days=5))
    assert _pruned(evidence)


@pytest.mark.parametrize("lifecycle", ["active", "waiting", "blocked"])
@pytest.mark.parametrize(("board_status", "on_board"), [("Done", True), ("", False)])
def test_resumable_claims_are_never_pruned(
    tmp_path: Path, lifecycle: str, board_status: str, on_board: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, lifecycle)
    start = datetime.now(UTC)
    for days in (0, 30, 400):
        retention.reconcile(
            store,
            local,
            _get(store, claim_id),
            board_status,
            start + timedelta(days=days),
            on_board=on_board,
        )
    assert _untouched(evidence)
    assert "terminal_observed_at" not in _get(store, claim_id).cleanup


def test_a_recorded_fly_machine_blocks_pruning(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    store.set_setting("runtime", "fly:machine:run-x", {"claim_id": claim_id, "machine_id": "m1"})
    store.set_setting("runtime", "fly:machine:run-y", {"claim_id": "other", "machine_id": "m2"})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)
    due = start + timedelta(days=_ABANDONED_DAYS)

    retention.reconcile(store, local, _get(store, claim_id), "Review", due)
    assert _untouched(evidence)

    store.clear_setting("runtime", "fly:machine:run-x")
    retention.reconcile(store, local, _get(store, claim_id), "Review", due)
    assert _pruned(evidence)


def test_incomplete_results_capture_blocks_pruning(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "settled")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)
    due = start + timedelta(days=_SETTLED_DAYS)

    retention.reconcile(
        store, local, _get(store, claim_id), "Review", due, capture_settled=lambda _: False
    )
    assert _untouched(evidence)

    retention.reconcile(
        store, local, _get(store, claim_id), "Review", due, capture_settled=lambda _: True
    )
    assert _pruned(evidence)


@pytest.mark.parametrize("lifecycle", ["settled", "cancelled", "superseded"])
def test_incomplete_workspace_release_blocks_pruning(tmp_path: Path, lifecycle: str) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, lifecycle)
    store.set_cleanup(claim_id, {"complete": False})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)

    retention.reconcile(store, local, _get(store, claim_id), "Review", start + timedelta(days=60))

    assert _untouched(evidence)


def test_candidate_worktree_with_read_only_files_is_removed(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    candidate = evidence / ".runtime" / "candidate-worktree"
    (candidate / ".git" / "objects").mkdir(parents=True)
    (candidate / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    module = candidate / "node_modules" / "pkg"
    module.mkdir(parents=True)
    (module / "index.js").write_text("x")
    (module / "index.js").chmod(0o444)
    module.chmod(0o555)
    (evidence / "input").mkdir()
    (evidence / "input" / "issue.json").write_text("{}")
    (evidence / "provenance.json").write_text("{}")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)

    retention.reconcile(
        store, local, _get(store, claim_id), "Review", start + timedelta(days=_ABANDONED_DAYS)
    )

    assert not candidate.exists()
    assert _retention(_get(store, claim_id))["errors"] == []
    for kept in ("result.json", "provenance.json", "input/issue.json", "future.json"):
        assert (evidence / kept).exists()


def test_the_first_observation_after_an_upgrade_never_prunes(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    much_later = datetime.now(UTC) + timedelta(days=365)

    retention.reconcile(store, local, _get(store, claim_id), "", much_later, on_board=False)

    assert _untouched(evidence)
    assert _get(store, claim_id).cleanup["terminal_observed_at"] == much_later.isoformat()


def test_reactivation_resets_the_cleanup_cycle_but_keeps_finished_facts(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, _ = _terminal_claim(store, tmp_path, "settled", kind="fix")
    store.set_cleanup(
        claim_id,
        {
            "terminal_observed_at": "2026-01-01T00:00:00+00:00",
            "complete": True,
            "released_by": "idle",
            "last_error": None,
            "review_observed": True,
            "retention": {"pruned_at": "2026-01-10T00:00:00+00:00", "removed": [], "errors": []},
            "results_final": True,
            "size_estimate": {"bytes": 0, "measured_at": "2026-01-10T00:00:00+00:00"},
            "paths": {"runner": {"source": "s", "path": "p"}},
            "fly_image": {"state": "complete"},
            "done_observed_at": "2026-01-05T00:00:00+00:00",
        },
    )
    store.set_claim_lifecycle(claim_id, "active", {})

    retention.reconcile(store, local, _get(store, claim_id), "Done", datetime.now(UTC))

    assert _get(store, claim_id).cleanup == {
        "paths": {"runner": {"source": "s", "path": "p"}},
        "fly_image": {"state": "complete"},
        "done_observed_at": "2026-01-05T00:00:00+00:00",
    }


def test_an_off_board_claim_loses_its_done_observation(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "settled")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Done", start)
    assert _get(store, claim_id).cleanup.get("done_observed_at") is not None

    retention.reconcile(
        store, local, _get(store, claim_id), "Done", start + timedelta(days=1), on_board=False
    )

    assert _get(store, claim_id).cleanup.get("done_observed_at") is None
    assert _untouched(evidence)


def test_a_spent_budget_skips_the_prune_but_records_observations(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    other_id, other = _terminal_claim(store, tmp_path, "cancelled", item="P2")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "Review", start)
    due = start + timedelta(days=_ABANDONED_DAYS)
    budget = retention.CleanupBudget(removals=1)

    retention.reconcile(store, local, _get(store, claim_id), "Review", due, budget=budget)
    retention.reconcile(store, local, _get(store, other_id), "Review", due, budget=budget)

    assert _pruned(evidence)
    assert _untouched(other)
    assert _get(store, other_id).cleanup["terminal_observed_at"] == due.isoformat()
    assert budget.removals == 0


def test_idle_due_follows_each_lifecycles_period(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    limits = _local(tmp_path).limits
    observed = datetime(2026, 9, 1, tzinfo=UTC)
    cancelled_id, _ = _terminal_claim(store, tmp_path, "cancelled")
    settled_id, _ = _terminal_claim(store, tmp_path, "settled", item="P2")
    for claim_id in (cancelled_id, settled_id):
        store.set_cleanup(claim_id, {"terminal_observed_at": observed.isoformat()})
    cancelled = _get(store, cancelled_id)
    settled = _get(store, settled_id)

    def due(claim: Claim, days: int, *, card_done: bool = False) -> bool:
        return retention.idle_due(
            claim, card_done=card_done, now=observed + timedelta(days=days), limits=limits
        )

    assert not due(cancelled, 2) and due(cancelled, 3)
    assert not due(settled, 13) and due(settled, 14)
    # A settled claim in Done is idle-released only while its Done cleanup never ran.
    assert due(settled, 14, card_done=True)
    store.set_cleanup(settled_id, {"terminal_observed_at": observed.isoformat(), "complete": True})
    assert not due(_get(store, settled_id), 14, card_done=True)
    unobserved = _get(store, cancelled_id)
    store.set_cleanup(cancelled_id, {})
    assert not due(_get(store, cancelled_id), 400)
    assert unobserved.lifecycle == "cancelled"


# INT-003: a fix claim's full cleanup cycle across a review round.


def _fix_round(store: ClaimStore, claim_id: str, tmp_path: Path, attempt: int) -> tuple[Path, Path]:
    """One attempt as admission records it: clones, a token copy, and attempt evidence."""
    clone = tmp_path / "clones" / f"attempt-{attempt}"
    clone.mkdir(parents=True)
    store.set_preparation(claim_id, {"clones": {"repo": str(clone)}})
    evidence = tmp_path / "factory" / "artifacts" / f"{claim_id}-fix"
    _make_fix_tree(tmp_path / "factory" / "artifacts", claim_id, attempt=attempt)
    run = store.reserve_run(claim_id, "fix", reason="initial", evidence_path=str(evidence))
    token = tmp_path / "private" / run.id / "fix.env"
    token.parent.mkdir(parents=True)
    token.write_text("GH_TOKEN=secret\n")
    store.configure_run(run.id, plan={"credential_files": [str(token)]}, limits={})
    store.finish_run(run.id, execution_status="completed", result={})
    return clone, token


def _tick(store: ClaimStore, local: LocalConfig, claim_id: str, status: str, now: datetime) -> None:
    """The per-card order: observe and prune, then release when idle."""
    from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup

    retention.reconcile(store, local, _get(store, claim_id), status, now)
    claim = _get(store, claim_id)
    idle = retention.idle_due(
        claim, card_done=status == "Done", now=now, limits=local.limits
    ) and not retention.machine_recorded(store, claim_id)
    cleanup = PullRequestCleanup(store, private_root=store_root(local) / "private")
    cleanup.reconcile(claim_id, board_status=status, idle=idle)


def store_root(local: LocalConfig) -> Path:
    return local.storage_root.parent


def _attempt_pruned(evidence: Path, attempt: int) -> bool:
    return not (evidence / f"attempt-{attempt}" / "logs").exists()


def test_a_review_round_after_an_idle_release_is_released_and_pruned_again(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = tmp_path / "factory" / "artifacts" / f"{claim.id}-fix"
    first_clone, first_token = _fix_round(store, claim.id, tmp_path, 1)
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    start = datetime.now(UTC)
    _tick(store, local, claim.id, "Review", start)
    due = start + timedelta(days=_SETTLED_DAYS)
    _tick(store, local, claim.id, "Review", due)  # release
    _tick(store, local, claim.id, "Review", due)  # prune
    assert not first_clone.exists() and not first_token.exists()
    assert _attempt_pruned(evidence, 1)

    # A review comment starts a round: the claim is active with fresh clones and a token.
    store.set_claim_lifecycle(claim.id, "active", {})
    second_clone, second_token = _fix_round(store, claim.id, tmp_path, 2)
    reopened = due + timedelta(hours=1)
    _tick(store, local, claim.id, "Running", reopened)
    reset = _get(store, claim.id).cleanup
    for key in ("complete", "released_by", "review_observed", "retention"):
        assert key not in reset
    assert second_clone.exists() and second_token.exists()

    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    settled_again = reopened + timedelta(hours=1)
    _tick(store, local, claim.id, "Review", settled_again)
    _tick(store, local, claim.id, "Review", settled_again + timedelta(days=_SETTLED_DAYS - 1))
    assert second_clone.exists() and not _attempt_pruned(evidence, 2)

    later = settled_again + timedelta(days=_SETTLED_DAYS)
    _tick(store, local, claim.id, "Review", later)
    assert not second_clone.exists() and not second_token.exists()
    _tick(store, local, claim.id, "Review", later)
    assert _attempt_pruned(evidence, 2)


def test_a_settled_claim_in_done_never_seen_in_review_is_released_then_pruned(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path, retention_days=20)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = tmp_path / "factory" / "artifacts" / f"{claim.id}-fix"
    clone, token = _fix_round(store, claim.id, tmp_path, 1)
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    start = datetime.now(UTC)
    _tick(store, local, claim.id, "Done", start)

    _tick(store, local, claim.id, "Done", start + timedelta(days=_SETTLED_DAYS - 1))
    assert clone.exists() and token.exists()

    released = start + timedelta(days=_SETTLED_DAYS)
    _tick(store, local, claim.id, "Done", released)
    assert not clone.exists() and not token.exists()
    assert _get(store, claim.id).cleanup["released_by"] == "idle"
    _tick(store, local, claim.id, "Done", released)
    assert not _attempt_pruned(evidence, 1)

    _tick(store, local, claim.id, "Done", start + timedelta(days=20))
    assert _attempt_pruned(evidence, 1)


# The size estimate status reports, measured once per claim during the tick.


def _estimate(store: ClaimStore, claim_id: str) -> object:
    return _get(store, claim_id).cleanup.get("size_estimate")


def test_measure_sums_remaining_release_and_prune_targets_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    store.set_cleanup(claim_id, {"complete": False})
    (evidence / "logs" / "big.log").write_bytes(b"x" * 1000)
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "file").write_bytes(b"y" * 500)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge").write_bytes(b"z" * 10_000)
    (clone / "link").symlink_to(outside)
    store.set_preparation(claim_id, {"clones": {"repo": str(clone)}})
    now = datetime(2026, 9, 27, tzinfo=UTC)
    budget = retention.CleanupBudget(measurements=1)

    retention.measure(store, _get(store, claim_id), None, now, budget)

    estimate = cast(dict[str, object], _estimate(store, claim_id))
    evidence_bytes = sum(
        item.stat().st_size
        for path in _removed_paths(evidence, kind="eval")
        for item in ([path] if path.is_file() else path.rglob("*"))
        if item.is_file()
    )
    assert evidence_bytes > 1000
    # The link counts as itself; the 10,000 bytes it points at are never followed.
    link = (clone / "link").lstat().st_size
    assert estimate["bytes"] == evidence_bytes + 500 + link
    assert estimate["measured_at"] == now.isoformat()
    assert budget.measurements == 0

    (clone / "later").write_bytes(b"w" * 100)
    retention.measure(store, _get(store, claim_id), None, now, retention.CleanupBudget())
    assert cast(dict[str, object], _estimate(store, claim_id))["bytes"] == estimate["bytes"]


def test_measure_skips_when_the_budget_is_spent_or_the_claim_is_not_terminal(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    cancelled, _ = _terminal_claim(store, tmp_path, "cancelled")
    active, _ = _terminal_claim(store, tmp_path, "active", item="P2")
    now = datetime(2026, 9, 27, tzinfo=UTC)

    retention.measure(store, _get(store, cancelled), None, now, retention.CleanupBudget(0, 0, 0))
    retention.measure(store, _get(store, active), None, now, retention.CleanupBudget())

    assert _estimate(store, cancelled) is None
    assert _estimate(store, active) is None


def test_a_prune_zeroes_the_estimate(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim_id, evidence = _terminal_claim(store, tmp_path, "cancelled")
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim_id), "", start, on_board=False)
    retention.measure(store, _get(store, claim_id), None, start, None)
    assert cast(dict[str, object], _estimate(store, claim_id))["bytes"] != 0

    due = start + timedelta(days=_ABANDONED_DAYS)
    retention.reconcile(store, local, _get(store, claim_id), "", due, on_board=False)

    assert _pruned(evidence)
    assert cast(dict[str, object], _estimate(store, claim_id))["bytes"] == 0
