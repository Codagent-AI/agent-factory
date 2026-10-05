"""Slimming removes regenerable clones, candidate checkouts, and source snapshots early."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

from agent_factory import slimming
from agent_factory.store import Claim, ClaimDraft, ClaimStore
from agent_factory.work_kinds.pull_request.cleanup import PullRequestCleanup
from tests.integration.test_retention import _local  # pyright: ignore[reportPrivateUsage]


def _get(store: ClaimStore, claim_id: str) -> Claim:
    claim = store.get_claim(claim_id)
    assert claim is not None
    return claim


def _write(root: Path, *paths: str) -> None:
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")


def _fix_claim(tmp_path: Path, store: ClaimStore) -> tuple[Claim, Path, Path]:
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    clones = tmp_path / "factory" / "clones" / claim.id
    _write(clones, "0/repo/README.md", "1/repo/README.md", "1/skills/SKILL.md")
    evidence = tmp_path / "factory" / "artifacts" / f"{claim.id}-fix"
    _write(
        evidence,
        "attempt-1/audit-abc/snapshot/runner-source/main.go",
        "attempt-1/audit-abc/snapshot/manifest.json",
        "attempt-1/fix-outcome.json",
        "attempt-1/logs/agent-runner.log",
    )
    return claim, clones, evidence


def _eval_claim(tmp_path: Path, store: ClaimStore) -> tuple[Claim, Path]:
    claim = store.create_claim(ClaimDraft("example/evals", 2, "I2", "P2", "eval", "fp", {}))
    evidence = tmp_path / "factory" / "artifacts" / f"{claim.id}-rep-1"
    _write(
        evidence,
        ".runtime/candidate-worktree/dist/index.html",
        ".runtime/candidate-worktree/node_modules/pkg/index.js",
        ".runtime/candidate-worktree/package.json",
        ".runtime/agent-runner-projects/p",
        ".runtime/agent-session-state/s",
        "result.json",
        "implementation.diff",
    )
    return claim, evidence


def test_idle_fix_claim_loses_clones_and_source_snapshots(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={})
    # Tools such as Go's module cache leave read-only trees behind.
    (clones / "0" / "repo").chmod(stat.S_IRUSR | stat.S_IXUSR)

    slimming.sweep(store, local, datetime.now(UTC))

    assert not (clones / "0").exists()
    # Attempt 1's clones may be cut for a run that is about to be reserved.
    assert (clones / "1" / "skills" / "SKILL.md").exists()
    assert not (evidence / "attempt-1/audit-abc/snapshot/runner-source").exists()
    assert (evidence / "attempt-1/audit-abc/snapshot/manifest.json").exists()
    assert (evidence / "attempt-1/fix-outcome.json").exists()
    assert (evidence / "attempt-1/logs/agent-runner.log").exists()
    record = _get(store, claim.id).cleanup["slimmed"]
    assert isinstance(record, dict)
    assert record["runs"] == [run.id]
    assert record["errors"] == []


def test_running_claim_is_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    first = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(first.id, execution_status="failed", result={})
    store.reserve_run(claim.id, "fix", reason="recovery", evidence_path=str(evidence))

    slimming.sweep(store, local, datetime.now(UTC))

    assert (clones / "0" / "repo" / "README.md").exists()
    assert (evidence / "attempt-1/audit-abc/snapshot/runner-source/main.go").exists()
    assert "slimmed" not in _get(store, claim.id).cleanup


def test_open_eval_claim_keeps_its_candidate_checkout(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, evidence = _eval_claim(tmp_path, store)
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="failed", result={})

    slimming.sweep(store, local, datetime.now(UTC))

    assert (evidence / ".runtime/candidate-worktree/node_modules/pkg/index.js").exists()


def test_settled_eval_keeps_only_the_served_build(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, evidence = _eval_claim(tmp_path, store)
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(claim.id, "settled", {})

    slimming.sweep(store, local, datetime.now(UTC))

    candidate = evidence / ".runtime/candidate-worktree"
    assert sorted(path.name for path in candidate.iterdir()) == ["dist"]
    assert (candidate / "dist/index.html").exists()
    assert not (evidence / ".runtime/agent-runner-projects").exists()
    # Session state and results stay for retention and human review.
    assert (evidence / ".runtime/agent-session-state/s").exists()
    assert (evidence / "result.json").exists()
    assert (evidence / "implementation.diff").exists()


def test_slimming_is_idempotent_and_picks_up_new_runs(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    first = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(first.id, execution_status="completed", result={})
    slimming.sweep(store, local, datetime.now(UTC))
    marked = _get(store, claim.id).cleanup["slimmed"]

    slimming.sweep(store, local, datetime.now(UTC))
    assert _get(store, claim.id).cleanup["slimmed"] == marked

    _write(evidence, "attempt-2/audit-def/snapshot/runner-source/main.go")
    second = store.reserve_run(claim.id, "fix", reason="review", evidence_path=str(evidence))
    store.finish_run(second.id, execution_status="completed", result={})
    slimming.sweep(store, local, datetime.now(UTC))

    assert list(clones.iterdir()) == []
    assert not (evidence / "attempt-2/audit-def/snapshot/runner-source").exists()
    record = _get(store, claim.id).cleanup["slimmed"]
    assert isinstance(record, dict)
    assert record["runs"] == sorted([first.id, second.id])


def test_release_succeeds_after_clones_were_slimmed(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    store.set_preparation(claim.id, {"clones": {"attempt-1": str(clones / "1")}})
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    slimming.sweep(store, local, datetime.now(UTC))

    cleanup = PullRequestCleanup(store, claim_directory=lambda _: clones)

    assert cleanup.release(claim.id) is True
    assert _get(store, claim.id).cleanup["complete"] is True
