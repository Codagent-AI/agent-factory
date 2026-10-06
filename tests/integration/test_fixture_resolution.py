from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_factory.suites.and_scene import FIXTURE_REPOSITORY, ReadinessError
from agent_factory.work_kinds.eval.handler import resolve_fixture


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def commit(path: Path, message: str) -> str:
    git(path, "commit", "--quiet", "--allow-empty", "-m", message)
    return git(path, "rev-parse", "HEAD")


def test_fixture_requires_a_published_origin_commit(tmp_path: Path) -> None:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(source)], check=True)
    git(source, "config", "user.name", "Tests")
    git(source, "config", "user.email", "test@example.invalid")
    base = commit(source, "base")
    git(source, "remote", "add", "origin", str(bare))
    git(source, "push", "--quiet", "-u", "origin", "main")
    branch = commit(source, "branch")
    git(source, "push", "--quiet", "origin", "HEAD:refs/heads/eval/fixture-x")
    git(source, "reset", "--hard", base)
    tagged = commit(source, "tagged")
    git(source, "tag", "-a", "published", "-m", "tag", tagged)
    git(source, "push", "--quiet", "origin", "refs/tags/published")
    git(source, "reset", "--hard", base)
    for number in range(30):
        git(source, "tag", f"bulk-{number:02d}", base)
    git(source, "push", "--quiet", "origin", "--tags")

    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(checkout)], check=True)
    git(checkout, "remote", "set-url", "origin", FIXTURE_REPOSITORY)
    git(checkout, "config", f"url.{bare}.insteadOf", FIXTURE_REPOSITORY)
    initial_head = git(checkout, "rev-parse", "HEAD")
    assert resolve_fixture(checkout, "eval/fixture-x") == branch
    assert resolve_fixture(checkout, branch[:8]) == branch
    with patch("agent_factory.suites.and_scene.inputs.subprocess.run", wraps=subprocess.run) as run:
        assert resolve_fixture(checkout, tagged) == tagged
    assert run.call_count < 10
    assert git(checkout, "rev-parse", "HEAD") == initial_head

    git(checkout, "config", "user.name", "Tests")
    git(checkout, "config", "user.email", "test@example.invalid")
    local = commit(checkout, "local")
    with pytest.raises(ReadinessError, match="not published"):
        resolve_fixture(checkout, local)
    git(checkout, "tag", "local-only", local)
    with pytest.raises(ReadinessError, match="not published"):
        resolve_fixture(checkout, "local-only")
    git(source, "push", "--quiet", "origin", ":refs/heads/eval/fixture-x")
    with pytest.raises(ReadinessError, match="not published"):
        resolve_fixture(checkout, branch)


def test_fixture_checkout_and_origin_fail_with_readiness(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ReadinessError, match=f"and-scene checkout {missing}: is missing"):
        resolve_fixture(missing, "main")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "--quiet", str(checkout)], check=True)
    git(checkout, "remote", "add", "origin", "https://user:secret@github.com/other/repo.git")
    with pytest.raises(ReadinessError, match="is not the and-scene fixture repository") as error:
        resolve_fixture(checkout, "main")
    assert "secret" not in str(error.value)
    git(checkout, "remote", "set-url", "origin", "file://user:secret@other/repo.git")
    with pytest.raises(ReadinessError, match="is not a GitHub repository") as error:
        resolve_fixture(checkout, "main")
    assert "secret" not in str(error.value)
