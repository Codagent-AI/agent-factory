"""scripts/update-runner.sh brings the operator's Agent Runner checkout to origin/main."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "update-runner.sh"
SKIP = 3


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    """An origin, the operator's checkout on main, and a second clone that pushes to origin."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    upstream = tmp_path / "upstream"
    subprocess.run(["git", "clone", "-q", str(origin), str(upstream)], check=True)
    _git(upstream, "checkout", "-q", "-b", "main")
    _commit(upstream, "first")
    _git(upstream, "push", "-q", "origin", "main")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    return origin, checkout, upstream


def _run(checkout: Path, name: str = "runner") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT.with_name(f"update-{name}.sh")), str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("name", ["runner", "validator"])
def test_check_only_fetches_without_fast_forward(tmp_path: Path, name: str) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    old = _git(checkout, "rev-parse", "HEAD")
    new = _commit(upstream, "new")
    _git(upstream, "push", "-q", "origin", "main")
    script = SCRIPT.with_name(f"update-{name}.sh")
    checked = subprocess.run(
        ["bash", str(script), "--check-only", str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert checked.returncode == 0, checked.stderr
    assert _git(checkout, "rev-parse", "HEAD") == old
    assert _git(checkout, "rev-parse", "origin/main") == new
    built = subprocess.run(
        ["bash", str(script), str(checkout)], capture_output=True, text=True, check=False
    )
    assert built.returncode == 0, built.stderr
    assert _git(checkout, "rev-parse", "HEAD") == new


def test_fast_forwards_a_checkout_behind_origin_main(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    merged = _commit(upstream, "merged-pr")
    _git(upstream, "push", "-q", "origin", "main")

    result = _run(checkout)

    assert result.returncode == 0, result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == merged


def test_a_current_checkout_is_ready_to_build(tmp_path: Path) -> None:
    _origin, checkout, _upstream = _repos(tmp_path)
    head = _git(checkout, "rev-parse", "HEAD")

    result = _run(checkout)

    assert result.returncode == 0, result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("name", ["runner", "validator"])
def test_skips_a_checkout_on_another_branch(tmp_path: Path, name: str) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    _git(checkout, "checkout", "-q", "-b", "dev")
    head = _git(checkout, "rev-parse", "HEAD")
    _commit(upstream, "merged-pr")
    _git(upstream, "push", "-q", "origin", "main")

    result = _run(checkout, name)

    assert result.returncode == SKIP
    assert "not on main" in result.stderr
    assert f"Agent {name.title()}" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("name", ["runner", "validator"])
def test_skips_a_checkout_with_uncommitted_changes(tmp_path: Path, name: str) -> None:
    _origin, checkout, _upstream = _repos(tmp_path)
    head = _git(checkout, "rev-parse", "HEAD")
    (checkout / "first").write_text("edited")

    result = _run(checkout, name)

    assert result.returncode == SKIP
    assert "uncommitted changes" in result.stderr
    assert f"Agent {name.title()}" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("name", ["runner", "validator"])
def test_skips_local_commits_that_are_not_on_origin_main_and_never_pushes(
    tmp_path: Path,
    name: str,
) -> None:
    origin, checkout, upstream = _repos(tmp_path)
    local = _commit(checkout, "unreviewed")
    _commit(upstream, "merged-pr")
    _git(upstream, "push", "-q", "origin", "main")
    published = _git(origin, "rev-parse", "main")

    result = _run(checkout, name)

    assert result.returncode == SKIP
    assert "not on origin/main" in result.stderr
    assert f"Agent {name.title()}" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == local
    assert _git(origin, "rev-parse", "main") == published


@pytest.mark.parametrize("name", ["runner", "validator"])
def test_a_missing_checkout_is_an_error(tmp_path: Path, name: str) -> None:
    result = _run(tmp_path / "absent", name)

    assert result.returncode == 1
    assert "not found" in result.stderr
    assert f"Agent {name.title()}" in result.stderr
