"""Packaged feature catalog against a Runner with core/verify-change."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request.kinds import FEATURE_STAGED_FILES, FIX

PACKAGE = files("agent_factory.work_kinds.pull_request") / "workflow"
RUNNER = Path(os.environ.get("FEATURE_TEST_RUNNER", shutil.which("agent-runner") or ""))


def suitable_runner() -> bool:
    if not RUNNER.is_file():
        return False
    result = subprocess.run(
        [str(RUNNER), "-validate", str(PACKAGE / "factory-feature-v1.0.yaml")],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


@pytest.mark.darwin
def test_feature_catalog_validates_and_preserves_prepopulated_session_dir(tmp_path: Path) -> None:
    if platform.system() != "Darwin":
        pytest.skip("Runner session directory behavior requires macOS")
    if not suitable_runner():
        pytest.skip(
            "installed Runner lacks core/verify-change; set FEATURE_TEST_RUNNER to a suitable build"
        )
    repo = tmp_path / "repo"
    catalog = repo / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    names = (
        set(FEATURE_STAGED_FILES)
        | set(FIX.staged_files)
        | {
            "factory-review-v1.0.yaml",
            "factory-implement-v1.0.yaml",
            "record-review-outcome.sh",
            "record-review-triage.sh",
            "record-triage.sh",
        }
    )
    for name in names:
        shutil.copy2(str(PACKAGE / name), catalog / name)
    for name in (
        "factory-feature-v1.0.yaml",
        "factory-define-v1.0.yaml",
        "factory-fix-v1.0.yaml",
        "factory-review-v1.0.yaml",
        "factory-implement-v1.0.yaml",
    ):
        result = subprocess.run(
            [str(RUNNER), "-validate", str(catalog / name)],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"
    feature = (catalog / "factory-feature-v1.0.yaml").read_text()
    define = (catalog / "factory-define-v1.0.yaml").read_text()
    for step in ("implement", "archive", "verify", "finalize"):
        assert re.search(rf"- id: {step}\n(?:(?!  - id:).)*factory-resume-skip.sh", feature, re.S)
    for step in (
        "proposal",
        "proposal-review",
        "specs",
        "design",
        "test-plan",
        "approach-review",
        "write-tasks",
    ):
        assert re.search(rf"- id: {step}\n(?:(?!  - id:).)*factory-resume-skip.sh", define, re.S)
    assert "tools: [call_agent]" not in feature + define
    assert "agent-validator" not in feature + define
    session = tmp_path / "session"
    output = session / "output"
    output.mkdir(parents=True)
    report = output / "task-session-report.out"
    report.write_text("preserve me")
    standin = catalog / "standin-v1.0.yaml"
    standin.write_text(
        "name: standin\ndescription: session test\nhidden: true\nsteps:\n"
        '  - id: check\n    command: test -s "{{session_dir}}/output/task-session-report.out"\n'
    )
    home = tmp_path / "home"
    (home / ".agent-runner").mkdir(parents=True)
    (home / ".agent-runner" / "settings.yaml").write_text(
        "theme: light\nautonomous_backend: headless\n"
        "setup:\n    completed_at: 2026-05-24T13:56:56Z\n"
    )
    result = subprocess.run(
        [
            str(RUNNER),
            "--headless",
            "-C",
            str(repo),
            "run",
            "standin",
            "--session-dir",
            str(session),
        ],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report.read_text() == "preserve me"
