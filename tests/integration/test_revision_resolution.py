from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_factory import runtime
from agent_factory.suites.and_scene import ReadinessError, SourceRepositories
from agent_factory.work_kinds.eval import EvalDefaults, parse_request


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def source_pair(tmp_path: Path) -> tuple[Path, Path, str, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "old",
    )
    old = git(origin, "rev-parse", "HEAD")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "new",
    )
    new = git(origin, "rev-parse", "HEAD")
    return origin, clone, old, new


def resolve(clone: Path, ref: str) -> tuple[str, str]:
    defaults = EvalDefaults("main", "main", {}, False, 1)
    request = parse_request(f'```eval\nagent_runner_ref = "{ref}"\n```', defaults)
    return runtime._resolve(SourceRepositories(clone, clone, clone), request)  # pyright: ignore[reportPrivateUsage]


def test_resolves_remote_branch_without_changing_local_checkout(tmp_path: Path) -> None:
    _, clone, old, new = source_pair(tmp_path)
    assert resolve(clone, "main") == (new, new)
    assert git(clone, "rev-parse", "HEAD") == old
    assert resolve(clone, old) == (old, new)


def test_fetch_failure_never_uses_cached_local_refs(tmp_path: Path) -> None:
    _, clone, _, _ = source_pair(tmp_path)
    git(clone, "remote", "set-url", "origin", str(tmp_path / "missing"))
    with pytest.raises(ReadinessError, match="fetch"):
        resolve(clone, "main")


def test_missing_ref_is_a_controlled_readiness_failure(tmp_path: Path) -> None:
    _, clone, _, _ = source_pair(tmp_path)
    with pytest.raises(ReadinessError, match="revision"):
        resolve(clone, "does-not-exist")


def test_fetch_timeout_is_a_controlled_readiness_failure(tmp_path: Path) -> None:
    with (
        patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 60)),
        pytest.raises(ReadinessError, match="timed out"),
    ):
        resolve(tmp_path, "main")
