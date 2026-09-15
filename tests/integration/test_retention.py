"""INT-005: age-based evidence retention over a real ClaimStore and evidence trees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

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
        evidence / ".runtime" / "candidate-worktree" / "HEAD",
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


def test_incomplete_sync_leaves_evidence_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
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


def test_superseded_claim_prunes_without_cleanup_complete(tmp_path: Path) -> None:
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
        assert not path.exists()


def test_failed_removal_records_an_error_and_retries_until_removable(tmp_path: Path) -> None:
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
    blocker = logs_dir.parent
    original_mode = blocker.stat().st_mode
    blocker.chmod(0o500)  # attempt-1 dir not writable: rmtree of "logs" inside it fails
    try:
        retention.reconcile(store, local, _get(store, claim.id), "Done", later)
    finally:
        blocker.chmod(original_mode)

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


def test_cancelled_claim_prunes_without_a_cleanup_pass(tmp_path: Path) -> None:
    """Cancelled claims never get the settled-only cleanup pass, so they must not wait on it."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    evidence = _make_fix_tree(tmp_path / "factory" / "artifacts", claim.id, attempt=1)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="cancelled", result={"reason": "cancelled"})
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    start = datetime.now(UTC)
    retention.reconcile(store, local, _get(store, claim.id), "Done", start)
    for path in _removed_paths(evidence, kind="fix"):
        assert path.exists()

    retention.reconcile(store, local, _get(store, claim.id), "Done", start + timedelta(days=14))

    for path in _removed_paths(evidence, kind="fix"):
        assert not path.exists()
    for path in _kept_paths(evidence, kind="fix"):
        assert path.exists()


def _settle_with_cleanup_complete(store: ClaimStore, claim_id: str) -> None:
    store.set_claim_lifecycle(claim_id, "settled", {"verdict": "pending-human-review"})
    store.set_cleanup(claim_id, {"review_observed": True, "complete": True})


def test_waiting_claim_is_never_pruned_even_when_its_card_is_done(tmp_path: Path) -> None:
    """A waiting claim may still retry or recover from its evidence; retention is for settled
    claims, with cancelled and superseded as the only exceptions."""
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
