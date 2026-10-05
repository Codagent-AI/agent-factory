"""Suite-wide test isolation from the developer's own tools, daemons, and accounts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest

# Outside the `docker` marker no test may reach the developer's Docker daemon, model
# CLIs, GitHub login, or Fly account. A wedged Docker Desktop alone made every
# `docker info` wait out its 30-60 s timeout, and the real gh, flyctl, codex, and
# claude reach the network with the developer's credentials. These stand-ins answer
# at once like a host where each tool is installed but unusable: the daemon is down
# and nothing is logged in. A test that needs an answer puts its own double earlier
# on PATH, and tests that exercise the installed Agent Runner or Validator on
# purpose still find them. Git is kept off the network too.
_UNAVAILABLE = """#!/bin/sh
echo "{name}: unavailable in the test suite (stand-in)" >&2
exit 1
"""
_STAND_INS = ("docker", "gh", "flyctl", "fly", "codex", "claude", "agent", "cursor")


@pytest.fixture(scope="session")
def hermetic_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("hermetic-bin")
    for name in _STAND_INS:
        stand_in = directory / name
        stand_in.write_text(_UNAVAILABLE.format(name=name))
        stand_in.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def hermetic_tools(
    request: pytest.FixtureRequest, hermetic_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The marker, not a keyword: parametrize ids such as `[docker]` are keywords too.
    node = cast(pytest.Item, request.node)  # pyright: ignore[reportUnknownMemberType]
    if node.get_closest_marker("docker") is None:
        monkeypatch.setenv("PATH", f"{hermetic_bin}{os.pathsep}{os.environ['PATH']}")
        # Git may use only local repositories: an https or ssh remote fails at once
        # instead of reaching the network. Unlike an insteadOf redirect, this leaves the
        # URLs that git reports unchanged.
        monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
