"""INT-004: post-merge working-clone sync against real temporary Git repositories."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from agent_factory.work_kinds.fix import sync
from agent_factory.work_kinds.fix.sync import (
    _merge_working_clone,  # pyright: ignore[reportPrivateUsage]
)


def _git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    )


def _init_bare(path: Path) -> None:
    path.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", "--initial-branch=main", str(path)], check=True
    )


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


def test_agent_runner_merge_runs_make_build_after_merging_main(tmp_path: Path) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    (clone / "Makefile").write_text("build:\n\t@printf 'rebuilt\\n' > local-binary\n")
    _git(clone, "add", "Makefile")
    _git(
        clone,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "add build target",
    )

    reason = _merge_working_clone(clone, rebuild=True)

    assert reason is None
    assert (clone / "local-binary").read_text() == "rebuilt\n"


def test_timed_out_build_terminates_its_process_group(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "Makefile").write_text(
        "build:\n\t@sleep 60 & echo $$! > child.pid; wait\n",
        encoding="utf-8",
    )

    build_working_clone = getattr(sync, "_build_working_clone", None)
    assert build_working_clone is not None
    reason = build_working_clone(clone, timeout=0.2)

    assert reason is not None
    assert reason.startswith("cannot rebuild agent-runner:")
    child_pid = int((clone / "child.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_build_does_not_hang_when_background_child_holds_output_open(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "Makefile").write_text(
        "build:\n\t@sleep 60 & echo $$! > child.pid\n",
        encoding="utf-8",
    )
    build_working_clone = getattr(sync, "_build_working_clone", None)
    assert build_working_clone is not None
    result: list[str | None] = []
    worker = threading.Thread(target=lambda: result.append(build_working_clone(clone)), daemon=True)

    worker.start()
    worker.join(timeout=1)
    hung = worker.is_alive()
    if hung:
        for _ in range(100):
            if (clone / "child.pid").exists():
                os.kill(int((clone / "child.pid").read_text()), signal.SIGKILL)
                break
            time.sleep(0.01)
        worker.join(timeout=2)

    assert not hung
    assert result == [None]


def test_failed_build_streams_output_and_reports_only_a_bounded_tail(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "Makefile").write_text(
        "build:\n\t@dd if=/dev/zero bs=10000 count=1 2>/dev/null >&2; "
        "printf 'tail-marker\\n' >&2; false\n",
        encoding="utf-8",
    )

    build_working_clone = getattr(sync, "_build_working_clone", None)
    assert build_working_clone is not None
    reason = build_working_clone(clone)

    assert reason is not None
    assert "tail-marker" in reason
    assert len(reason) < 5000


def test_build_output_tail_discards_older_bytes() -> None:
    bounded_output = getattr(sync, "_BoundedOutput", None)
    assert bounded_output is not None
    output = bounded_output(limit=8)

    output.append(b"12345")
    output.append(b"67890")

    assert output.text() == "34567890"


def test_build_process_start_failure_returns_a_block_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()

    def fail_popen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("too many open files")

    monkeypatch.setattr(sync.subprocess, "Popen", fail_popen)
    build_working_clone = getattr(sync, "_build_working_clone", None)
    assert build_working_clone is not None

    reason = build_working_clone(clone)

    assert reason == "cannot rebuild agent-runner: too many open files"


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
    assert (
        _git(second_worktree, "rev-parse", "HEAD").stdout == _git(clone, "rev-parse", "main").stdout
    )


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


def test_stalled_git_command_blocks_with_a_reason_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)

    def stalled(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=300)

    monkeypatch.setattr(sync.subprocess, "run", stalled)

    reason = _merge_working_clone(clone)

    assert reason is not None
    assert reason.startswith("git command did not complete:")


def test_git_runs_without_a_terminal_or_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _origin, clone = _setup_origin_and_clone(tmp_path)
    seen: list[dict[str, object]] = []
    real_run = subprocess.run

    def recording(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(dict(kwargs))
        return cast(subprocess.CompletedProcess[str], real_run(*args, **kwargs))

    monkeypatch.setattr(sync.subprocess, "run", recording)

    assert _merge_working_clone(clone) is None
    assert seen
    for kwargs in seen:
        assert kwargs["timeout"] == 300
        assert kwargs["stdin"] is subprocess.DEVNULL
        env = kwargs["env"]
        assert isinstance(env, dict) and env["GIT_TERMINAL_PROMPT"] == "0"
