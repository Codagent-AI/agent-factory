"""Installed Runner sandbox: all agent adapters and external commands are local stubs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import FEATURE

RUNNER = os.environ.get("FEATURE_TEST_RUNNER") or shutil.which("agent-runner")


def sandbox(repo: Path, root: Path) -> tuple[str, dict[str, str], Path]:
    if not RUNNER:
        pytest.skip("no installed Runner (FEATURE_TEST_RUNNER or PATH)")
    home = root / "home"
    settings = home / ".agent-runner/settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        "theme: light\nautonomous_backend: headless\nsetup:\n  completed_at: 2026-05-24T13:56:56Z\n"
    )
    bin_dir = root / "bin"
    bin_dir.mkdir()
    stub = Path(__file__).with_name("claude_stub.py")
    (bin_dir / "claude").write_text(f"#!{sys.executable}\n" + stub.read_text().split("\n", 1)[1])
    (bin_dir / "claude").chmod(0o755)
    for command in ("codex", "cursor", "agent", "copilot", "gemini", "gh", "agent-validator"):
        (bin_dir / command).write_text('#!/bin/sh\necho "forbidden test command" >&2\nexit 99\n')
        (bin_dir / command).chmod(0o755)
    # commit-change-plan invokes skip; checks and reviews are forbidden.
    (bin_dir / "agent-validator").write_text('#!/bin/sh\n[ "$1" = skip ] || exit 99\n')
    (bin_dir / "openspec").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "openspec").chmod(0o755)
    artifacts = root / "artifacts"
    artifacts.mkdir()
    catalog = launch.stage_workflow_into(
        repo / ".agent-runner/workflows", FEATURE.default_contract, FEATURE
    )
    config = repo / ".agent-runner/config.yaml"
    config.write_text(
        "profiles:\n  stub:\n    agents:\n"
        + "".join(
            f"      {role}:\n        cli: claude\n        model: stub\n"
            "        default_mode: autonomous\n"
            for role in ("lead", "implementor", "tester", "crosscheck")
        )
    )
    with (repo / ".git/info/exclude").open("a") as exclude:
        exclude.write("\n/.agent-runner/workflows/\n/.agent-runner/config.yaml\n")
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_ARTIFACTS": str(artifacts),
        "AGENT_RUNNER_NO_TUI": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)
    return RUNNER, env, catalog


def execute(
    runner: str, repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [runner, "--headless", "-C", str(repo), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
