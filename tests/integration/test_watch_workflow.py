"""INT-007: installed Runner accepts the packaged workflow contract."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request.launch import staged_config_text


def test_packaged_watch_workflow_validates(tmp_path: Path) -> None:
    runner = shutil.which("agent-runner")
    if runner is None:
        pytest.skip("SKIP: agent-runner is not installed")
    help_text = subprocess.run(
        [runner, "run", "--help"], capture_output=True, text=True, check=False
    )
    if "--session-dir" not in help_text.stdout + help_text.stderr:
        pytest.skip("SKIP: installed Runner lacks --session-dir")
    source = Path(__file__).parents[2] / "src/agent_factory/watch/workflow"
    workflow = source / "factory-watch-v1.0.yaml"
    assert workflow.read_text().startswith("# factory-contract: factory-watch/1")
    assert all(
        name in workflow.read_text()
        for name in ("brief_file", "artifact_dir", "contract_version", "watcher")
    )
    catalog = tmp_path / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    for name in ("factory-watch-v1.0.yaml", "check-contract.sh", "check-result.sh"):
        shutil.copyfile(source / name, catalog / name)
    (tmp_path / ".agent-runner" / "config.yaml").write_text(
        staged_config_text(None, {"watcher": ("claude", "model", "medium")}), encoding="utf-8"
    )
    validated = subprocess.run(
        [
            runner,
            "-C",
            str(tmp_path),
            "--profile",
            "factory",
            "-validate",
            str(catalog / workflow.name),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    reviewer = (Path(__file__).parents[2] / ".claude/agents/factory-pr-reviewer.md").read_text()
    headless = reviewer.split("## Headless (dispatched) mode", 1)[1]
    assert "/Users/paul/codagent/" not in headless
    assert "releases/current" not in headless
