"""The packaged Task and shared review workflows validate together."""

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request import launch

WORKFLOW = Path("src/agent_factory/work_kinds/pull_request/workflow")


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def test_task_workflow_catalog_validates(tmp_path: Path) -> None:
    runner = shutil.which("agent-runner")
    if runner is None:
        pytest.skip("agent-runner unavailable")
    catalog = tmp_path / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    for name in launch.STAGED_FILES:
        shutil.copy2(WORKFLOW / name, catalog / name)
    for name in (
        "factory-task-v1.0.yaml",
        "factory-task-guard-v1.0.yaml",
        "factory-review-v1.0.yaml",
        "factory-implement-v1.0.yaml",
    ):
        result = run([runner, "-validate", str(catalog / name)], cwd=tmp_path)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_task_pre_push_guard_rechecks_gates_after_findings() -> None:
    task = (WORKFLOW / "factory-task-v1.0.yaml").read_text()
    guard = (WORKFLOW / "factory-task-guard-v1.0.yaml").read_text()
    after_findings = task.split("  - id: address-findings", 1)[1]
    before_push = after_findings.split("  - id: finalize-pr", 1)[0]
    assert "prepush-guard" in before_push
    assert 'gates_file: "{{artifact_dir}}/task-triage.json"' in before_push
    assert 'gate_changes_file: "{{artifact_dir}}/gate-changes.json"' in before_push
    assert "re-exercise-gates" in guard
    assert "check-gate-exercises.py" in guard
    assert "inventory-gates" in guard
    assert "derive-diff-gates" in guard
    assert "check-gate-inventory.py" in guard
    assert "scope-{{mode}}.json" in guard
    assert "mark-nonchore-crossing" in guard
    assert "non-chore CI commits" in guard
