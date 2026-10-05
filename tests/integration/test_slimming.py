"""Slimming removes clones, installed dependencies, and source snapshots early."""

from __future__ import annotations

import stat
import subprocess
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
        ".runtime/candidate-worktree/app/node_modules/dep/index.js",
        ".runtime/candidate-worktree/README.md",
        ".runtime/candidate-worktree/openspec/changes/c/tasks/1.md",
        ".runtime/agent-runner-projects/p/runs/r/output/acceptance-1.md",
        ".runtime/agent-session-state/s",
        "result.json",
    )
    return claim, evidence


def _finish(store: ClaimStore, claim: Claim, evidence: Path, status: str = "completed") -> str:
    run = store.reserve_run(claim.id, claim.kind, reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status=status, result={})
    return run.id


def _finish_rep(store: ClaimStore, claim: Claim, evidence: Path) -> None:
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(evidence))
    store.finish_run(run.id, execution_status="completed", result={})


def test_idle_fix_claim_loses_clones_and_source_snapshots(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    run_id = _finish(store, claim, evidence)
    # Real snapshots and Go module caches leave read-only trees behind.
    (clones / "0" / "repo").chmod(stat.S_IRUSR | stat.S_IXUSR)
    (evidence / "attempt-1/audit-abc/snapshot/runner-source").chmod(stat.S_IRUSR | stat.S_IXUSR)

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
    assert record["runs"] == [run_id]
    assert record["errors"] == []


def test_clone_evidence_is_preserved_before_removal(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    repo = clones / "0" / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _write(repo, "validator_logs/check_lint.1.log", "uncommitted.txt")
    _finish(store, claim, evidence, status="failed")

    slimming.sweep(store, local, datetime.now(UTC))

    assert not (clones / "0").exists()
    assert (evidence / "attempt-1/validator_logs/check_lint.1.log").exists()
    state = (evidence / "attempt-1/clone-state.patch").read_text()
    assert "uncommitted.txt" in state


def test_running_claim_is_untouched(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    _finish(store, claim, evidence, status="failed")
    store.reserve_run(claim.id, "fix", reason="recovery", evidence_path=str(evidence))

    slimming.sweep(store, local, datetime.now(UTC))

    assert (clones / "0" / "repo" / "README.md").exists()
    assert (evidence / "attempt-1/audit-abc/snapshot/runner-source/main.go").exists()
    assert "slimmed" not in _get(store, claim.id).cleanup


def test_open_eval_claim_keeps_its_dependencies(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, evidence = _eval_claim(tmp_path, store)
    _finish_rep(store, claim, evidence)

    slimming.sweep(store, local, datetime.now(UTC))

    assert (evidence / ".runtime/candidate-worktree/node_modules/pkg/index.js").exists()


def test_terminal_eval_loses_only_installed_dependencies(tmp_path: Path) -> None:
    for lifecycle in ("settled", "cancelled", "superseded"):
        root = tmp_path / lifecycle
        store = ClaimStore(root / "state.sqlite3")
        local = _local(root)
        claim, evidence = _eval_claim(root, store)
        _finish_rep(store, claim, evidence)
        store.set_claim_lifecycle(claim.id, lifecycle, {})

        slimming.sweep(store, local, datetime.now(UTC))

        candidate = evidence / ".runtime/candidate-worktree"
        assert not (candidate / "node_modules").exists()
        assert not (candidate / "app/node_modules").exists()
        # Rescore hashes acceptance artifacts; human review serves `dist`.
        for kept in (
            "dist/index.html",
            "README.md",
            "openspec/changes/c/tasks/1.md",
        ):
            assert (candidate / kept).exists()
        assert (
            evidence / ".runtime/agent-runner-projects/p/runs/r/output/acceptance-1.md"
        ).exists()
        assert (evidence / ".runtime/agent-session-state/s").exists()
        assert (evidence / "result.json").exists()


def test_links_cannot_redirect_removal_outside_evidence(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, evidence = _eval_claim(tmp_path, store)
    outside = tmp_path / "host"
    _write(outside, "candidate-worktree/node_modules/keep.js")
    # A sandboxed agent can write the repetition directory; point `.runtime` at the host.
    runtime = evidence / ".runtime"
    runtime.rename(evidence / "runtime-real")
    runtime.symlink_to(outside, target_is_directory=True)
    _finish_rep(store, claim, evidence)
    store.set_claim_lifecycle(claim.id, "settled", {})

    slimming.sweep(store, local, datetime.now(UTC))

    assert (outside / "candidate-worktree/node_modules/keep.js").exists()
    record = _get(store, claim.id).cleanup["slimmed"]
    assert isinstance(record, dict)
    assert record["errors"]
    assert "runs" not in record


def test_slimming_is_idempotent_and_picks_up_new_runs(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    first = _finish(store, claim, evidence)
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
    assert record["runs"] == sorted([first, second.id])


def test_release_succeeds_after_clones_were_slimmed(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = _local(tmp_path)
    claim, clones, evidence = _fix_claim(tmp_path, store)
    store.set_preparation(claim.id, {"clones": {"attempt-0": str(clones / "0")}})
    _finish(store, claim, evidence)
    store.set_claim_lifecycle(claim.id, "cancelled", {})
    slimming.sweep(store, local, datetime.now(UTC))

    cleanup = PullRequestCleanup(store, claim_directory=lambda _: clones)

    assert cleanup.release(claim.id) is True
    assert _get(store, claim.id).cleanup["complete"] is True
