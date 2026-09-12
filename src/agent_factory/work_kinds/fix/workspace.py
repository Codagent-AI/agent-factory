"""Bare mirrors of fix targets and the fresh per-attempt clones each launch runs in."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from agent_factory.suites.and_scene import ReadinessError, WorktreeError

_SHA = re.compile(r"[0-9a-f]{40}")
_ASKPASS = """#!/bin/sh
case "$1" in
  *sername*) printf '%s\\n' x-access-token ;;
  *assword*) printf '%s\\n' "${FACTORY_GIT_TOKEN:-}" ;;
  *) printf '\\n' ;;
esac
"""


class FixWorkspace:
    """Owns `<root>/mirrors` and `<root>/clones/<claim>/<attempt>` for the fix kind."""

    def __init__(self, storage_root: Path, runner_checkout: Path, skills_checkout: Path) -> None:
        self._root = storage_root.expanduser().resolve()
        self._runner = runner_checkout.expanduser().resolve()
        self._skills = skills_checkout.expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def mirror_path(self, repository: str) -> Path:
        return self._root / "mirrors" / f"{repository.replace('/', '__')}.git"

    def fetch_mirror(self, repository: str, token: str | None) -> None:
        """Create the mirror on first use, then fetch it with the controller's read token."""
        mirror = self.mirror_path(repository)
        if not mirror.is_dir():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            self._authenticated_git(
                token,
                [
                    "clone",
                    "--quiet",
                    "--mirror",
                    f"https://github.com/{repository}.git",
                    str(mirror),
                ],
                f"cannot create the mirror for {repository}",
            )
            return
        self._authenticated_git(
            token,
            ["--git-dir", str(mirror), "fetch", "--quiet", "--prune", "origin"],
            f"cannot fetch the mirror for {repository}",
        )

    def resolve_mirror(self, repository: str, branch: str) -> str:
        mirror = self.mirror_path(repository)
        completed = _git(
            ["--git-dir", str(mirror), "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"]
        )
        sha = completed.stdout.strip()
        if completed.returncode != 0 or not _SHA.fullmatch(sha):
            raise ReadinessError(
                f"Cannot resolve branch {branch!r} of {repository} in its mirror; "
                "check the configured target branch."
            )
        return sha

    def attempt_directory(self, claim_id: str, attempt: int) -> Path:
        return self._root / "clones" / _safe(claim_id) / str(attempt)

    def prepare_clones(
        self, claim_id: str, attempt: int, repository: str, revisions: Mapping[str, object]
    ) -> dict[str, str]:
        """Cut fresh clones at the recorded commits; never reuse a previous attempt's clones."""
        commits = {name: _commit(revisions, name) for name in ("target", "runner", "skills")}
        directory = self.attempt_directory(claim_id, attempt)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        mirror = self.mirror_path(repository)
        if not mirror.is_dir():
            raise WorktreeError(f"mirror for {repository} is missing: {mirror}")
        sources = (
            ("repo", mirror, commits["target"]),
            ("runner", self._runner, commits["runner"]),
            ("skills", self._skills, commits["skills"]),
        )
        clones: dict[str, str] = {}
        try:
            for name, source, sha in sources:
                target = directory / name
                _clone_at(source, target, sha)
                clones[name] = str(target)
            # The repo clone's origin must be GitHub, not the host mirror path, so the
            # workflow's push and `gh` calls inside the container address the real remote.
            _require(
                _git(
                    [
                        "-C",
                        clones["repo"],
                        "remote",
                        "set-url",
                        "origin",
                        f"https://github.com/{repository}.git",
                    ]
                ),
                "cannot point the target clone at GitHub",
            )
        except WorktreeError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return clones

    def _authenticated_git(self, token: str | None, arguments: list[str], prefix: str) -> None:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        with tempfile.TemporaryDirectory(prefix="agent-factory-askpass-") as directory:
            if token:
                helper = Path(directory) / "askpass.sh"
                helper.write_text(_ASKPASS, encoding="utf-8")
                helper.chmod(0o700)
                environment["GIT_ASKPASS"] = str(helper)
                environment["FACTORY_GIT_TOKEN"] = token
            try:
                completed = subprocess.run(
                    ["git", "-c", "credential.helper=", *arguments],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=environment,
                    timeout=300,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ReadinessError(f"{prefix}: {error}") from error
        if completed.returncode != 0:
            # Git stderr can echo credential-bearing URLs; report the exit code only.
            raise ReadinessError(
                f"{prefix} (git exit {completed.returncode}); check remote access."
            )


def _clone_at(source: Path, target: Path, sha: str) -> None:
    if not source.is_dir():
        raise WorktreeError(f"configured source checkout is unavailable: {source}")
    _require(
        _git(["clone", "--quiet", "--local", "--no-checkout", str(source), str(target)]),
        f"cannot clone {source}",
    )
    _require(
        _git(["-C", str(target), "checkout", "--quiet", "--detach", sha]),
        f"recorded commit {sha[:7]} is unavailable in {source}",
    )
    if _git(["-C", str(target), "status", "--porcelain"]).stdout.strip():
        raise WorktreeError(f"new clone is not clean: {target}")


def _commit(revisions: Mapping[str, object], name: str) -> str:
    value = revisions.get(name)
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise WorktreeError(f"claim has no recorded {name} commit")
    return value


def _git(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments], capture_output=True, text=True, check=False, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorktreeError(f"git could not run: {error}") from error


def _require(completed: subprocess.CompletedProcess[str], prefix: str) -> None:
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or str(completed.returncode)
        raise WorktreeError(f"{prefix}: {detail}")


def _safe(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    if not result:
        raise WorktreeError("claim identity cannot be converted to a safe directory name")
    return result
