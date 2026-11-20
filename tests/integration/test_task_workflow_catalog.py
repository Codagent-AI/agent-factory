"""The packaged Task and shared review workflows validate together."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request import launch

WORKFLOW = Path("src/agent_factory/work_kinds/pull_request/workflow")


def test_task_constraints_carry_the_boundary_and_the_chore_commit_rule(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    design.write_text("# Design\n")
    payload = json.dumps({"design_file": str(design), "kind": "task"})
    first = subprocess.run(
        [str(WORKFLOW / "add-constraints.sh")], input=payload, text=True, capture_output=True
    )
    assert first.returncode == 0, first.stderr
    appended = design.read_text()
    boundary = (WORKFLOW / "factory-task-boundary.md").read_text().split("\n", 1)[1]
    assert (WORKFLOW / "factory-change-rules.md").read_text() in appended
    assert boundary.strip() in appended
    assert "`chore:`" in appended
    # Running it again changes nothing.
    again = subprocess.run(
        [str(WORKFLOW / "add-constraints.sh")], input=payload, text=True, capture_output=True
    )
    assert again.returncode == 0, again.stderr
    assert design.read_text() == appended


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
        "factory-task-v2.0.yaml",
        "factory-task-guard-v1.0.yaml",
        "factory-review-v2.0.yaml",
        "factory-implement-v2.0.yaml",
    ):
        result = run([runner, "-validate", str(catalog / name)], cwd=tmp_path)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_task_scope_guard_runs_after_implement_change_and_before_the_pull_request_is_ready() -> (
    None
):
    task = (WORKFLOW / "factory-task-v2.0.yaml").read_text()
    guard = (WORKFLOW / "factory-task-guard-v1.0.yaml").read_text()
    order = ["- id: change\n", "- id: prepush-guard\n", "- id: finalize-pr\n"]
    positions = [task.index(step) for step in order]
    assert positions == sorted(positions)
    # The Runner pushes the branch and opens the draft inside implement-change, so nothing
    # here can rewrite commit subjects; the non-chore ones are recorded as evidence instead.
    assert "normalize-chore-commits.py" not in task
    assert "check-chore-subjects.py" in task
    assert "scope-floor" in guard
    assert "task-scope-review" in guard
    assert "scope-{{mode}}.json" in guard
    assert "gate-exercises" not in task + guard
    assert "gate-inventory" not in task + guard
    # Non-chore CI repair subjects are evidence only; they never mark a scope crossing.
    assert "record-chore-subjects" in guard
    assert "mark-nonchore-crossing" not in guard
    assert (
        "nonchore-commits.json"
        not in guard.split("- id: record-chore-subjects", 1)[1].split("- id: complete-guard", 1)[1]
    )


def test_task_boundary_and_triage_leave_open_decisions_to_writer() -> None:
    boundary = (WORKFLOW / "factory-task-boundary.md").read_text()
    triage = (WORKFLOW / "factory-task-v2.0.yaml").read_text()
    spec = Path("openspec/specs/factory-task-execution/spec.md").read_text()

    assert "a product, design, compatibility, or other decision the issue leaves open" in boundary
    assert (
        "Decline any decision the issue leaves open with `needs-input`, naming that decision."
        in boundary
    )
    assert "Decline any decision the issue leaves open." in triage
    assert "unless the issue explicitly requests it" in boundary
    assert "unless the issue explicitly requests it" in spec
    assert "threshold, or other decision" not in boundary + triage + spec


def test_task_enablement_docs_require_approval_for_every_ready_card() -> None:
    for path in (Path("AGENTS.md"), Path("docs/operations.md")):
        document = path.read_text()
        enablement = document.split("Before enabling `[task]`", 1)[1].split("Before roll", 1)[0]
        assert "every fix target" in enablement
        assert "approved for factory admission" in enablement
        assert "agent-factory#74" not in enablement
