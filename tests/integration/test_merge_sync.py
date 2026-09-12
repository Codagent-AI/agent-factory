"""INT-004: post-merge working-clone sync against real temporary Git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_factory.work_kinds.fix.sync import (
    _merge_working_clone,  # pyright: ignore[reportPrivateUsage]
)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "--bare", str(path)], check=True)


def _write_commit(path: Path, name: str, content: str, message: str) -> None:
    (path / name).write_text(content)
    _git(path, "add", name)
    _git(
        path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        message,
    )


def _setup_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin whose main was already merged by the factory, plus a working clone on dev."""
    origin = tmp_path / "origin.git"
    _init_bare(origin)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(seed)], check=True)
    subprocess.run(["git", "-C", str(seed), "checkout", "-b", "main"], check=True)
    _write_commit(seed, "README.md", "seed\n", "seed commit")
    _git(seed, "push", "-u", "origin", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-b", "dev"], check=True)
    _write_commit(clone, "dev.txt", "dev work\n", "dev commit")

    _write_commit(seed, "fix.txt", "the fix\n", "factory fix merged to main")
    _git(seed, "push", "origin", "main")
    return origin, clone


def test_clean_dev_merges_main_and_succeeds(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)

    reason = _merge_working_clone(clone)

    assert reason is None
    local_main = _git(clone, "rev-parse", "main").stdout.strip()
    origin_main = _git(clone, "rev-parse", "origin/main").stdout.strip()
    assert local_main == origin_main
    log = _git(clone, "log", "--oneline", "dev").stdout
    assert "factory fix merged to main" in log


def test_tracked_modification_blocks_before_any_fetch(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "dev.txt").write_text("dirty\n")
    before = _git(clone, "rev-parse", "HEAD").stdout

    reason = _merge_working_clone(clone)

    assert reason == "uncommitted changes"
    assert _git(clone, "rev-parse", "HEAD").stdout == before
    assert (clone / "dev.txt").read_text() == "dirty\n"
    assert not (clone / ".git" / "FETCH_HEAD").exists()


def test_staged_only_change_blocks(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "dev.txt").write_text("staged change\n")
    _git(clone, "add", "dev.txt")

    reason = _merge_working_clone(clone)

    assert reason == "uncommitted changes"


def test_untracked_only_is_clean(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "untracked.txt").write_text("new file\n")

    reason = _merge_working_clone(clone)

    assert reason is None
    assert (clone / "untracked.txt").exists()


def test_conflicting_history_blocks_without_starting_a_merge(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "fix.txt").write_text("conflicting local content\n")
    _git(clone, "add", "fix.txt")
    _git(
        clone,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "conflicting dev commit",
    )
    before = _git(clone, "rev-parse", "HEAD").stdout

    reason = _merge_working_clone(clone)

    assert reason == "conflicts"
    assert not (clone / ".git" / "MERGE_HEAD").exists()
    assert _git(clone, "rev-parse", "HEAD").stdout == before


def test_diverged_local_main_blocks(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    subprocess.run(["git", "-C", str(clone), "checkout", "main"], check=True)
    _write_commit(clone, "local-only.txt", "local\n", "unpushed local main commit")
    local_main_before = _git(clone, "rev-parse", "main").stdout
    subprocess.run(["git", "-C", str(clone), "checkout", "dev"], check=True)

    reason = _merge_working_clone(clone)

    assert reason is not None
    assert "main" in reason or "diverged" in reason
    assert _git(clone, "rev-parse", "main").stdout == local_main_before


def test_main_checked_out_elsewhere_blocks(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    second_worktree = tmp_path / "second-worktree"
    subprocess.run(
        ["git", "-C", str(clone), "worktree", "add", str(second_worktree), "main"], check=True
    )

    reason = _merge_working_clone(clone)

    assert reason is not None
    assert _git(second_worktree, "rev-parse", "HEAD").stdout == _git(
        clone, "rev-parse", "main"
    ).stdout


def test_main_checked_out_fast_forwards(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    subprocess.run(["git", "-C", str(clone), "checkout", "main"], check=True)

    reason = _merge_working_clone(clone)

    assert reason is None
    assert _git(clone, "rev-parse", "main").stdout == _git(clone, "rev-parse", "origin/main").stdout


def test_detached_head_blocks(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    head = _git(clone, "rev-parse", "HEAD").stdout.strip()
    subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", head], check=True)

    reason = _merge_working_clone(clone)

    assert reason == "detached HEAD"


def test_missing_clone_directory_blocks(tmp_path: Path) -> None:
    reason = _merge_working_clone(tmp_path / "does-not-exist")

    assert reason == "the operator's working clone is not configured"


def test_retry_after_resolving_uncommitted_changes_succeeds(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "dev.txt").write_text("dirty\n")
    assert _merge_working_clone(clone) == "uncommitted changes"

    _git(clone, "checkout", "--", "dev.txt")
    reason = _merge_working_clone(clone)

    assert reason is None
