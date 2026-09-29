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
    workflow = source / "factory-watch-v2.0.yaml"
    assert workflow.read_text().startswith("# factory-contract: factory-watch/2")
    assert all(
        name in workflow.read_text()
        for name in ("brief_file", "artifact_dir", "contract_version", "watcher")
    )
    catalog = tmp_path / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    for name in ("factory-watch-v2.0.yaml", "check-contract.sh", "check-result.sh"):
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


_ROOT = Path(__file__).parents[2]


def _section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_headless_procedures_live_in_factory_triage_and_never_review_or_fix() -> None:
    workflow = (_ROOT / "src/agent_factory/watch/workflow/factory-watch-v2.0.yaml").read_text()
    assert "factory-pr-review" not in workflow
    assert "Headless PR-READY check" in workflow and "Headless triage" in workflow
    triage = (_ROOT / ".claude/skills/factory-triage/SKILL.md").read_text()
    for heading in ("## Headless PR-READY check", "## Headless triage"):
        section = _section(triage, heading)
        assert "/Users/paul/codagent/" not in section
        assert "releases/current" not in section
        assert "commit, push, or open a pull request" in section
    check = _section(triage, "## Headless PR-READY check")
    assert "Do not review the pull request's code" in check
    assert '`procedure: "pr-check"`' in check
    assert '`procedure: "triage"`' in _section(triage, "## Headless triage")
    for path in (
        ".claude/skills/factory-pr-review/SKILL.md",
        ".claude/agents/factory-pr-reviewer.md",
    ):
        assert "Headless" not in (_ROOT / path).read_text()
