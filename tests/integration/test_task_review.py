"""Task review rounds retain task scope and skip feature base merging."""

import json
import subprocess
from pathlib import Path

from agent_factory.work_kinds.pull_request import launch

WORKFLOW = Path("src/agent_factory/work_kinds/pull_request/workflow")


def test_task_review_without_base_head_does_not_merge(tmp_path: Path) -> None:
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps({"kind": "task", "head_sha": "a" * 40, "branch": "factory/task-7-claim"})
    )
    result = subprocess.run(
        ["sh", str(WORKFLOW / "review-merge-base.sh")],
        input=json.dumps({"review_file": str(review), "artifact_dir": str(tmp_path)}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "none"
    assert not (tmp_path / "base-merge.json").exists()


def test_task_review_passes_scope_to_shared_implementation() -> None:
    review = launch.packaged_workflow_text(launch.REVIEW_CONTRACT)
    implementation = (WORKFLOW / "factory-implement-v1.0.yaml").read_text()
    assert "On a task pull request" in review
    implement_call = review.split("  - id: implement\n", 1)[1].split(
        "  - id: restore-description", 1
    )[0]
    assert 'task_scope: "{{is_task}}"' in implement_call
    assert 'scope_base: "{{scope_base}}"' in implement_call
    assert "record-task-scope-stop" in review
    assert "implement-task-plan" in implementation
    assert "prepush-task-guard" in implementation
    assert "postfinalize-task-guard" in implementation
    assert "discover-review-gates" in implementation
    assert implementation.count('gates_file: "{{artifact_dir}}/review-gates.json"') == 2
    assert "check-review-chore-commits" in implementation
    assert "record-nonchore-crossing" in implementation
    assert "normalize-task-commits" not in implementation
