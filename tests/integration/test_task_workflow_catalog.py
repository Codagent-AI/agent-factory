"""The packaged Task and shared review workflows validate together."""

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request import launch

WORKFLOW = Path("src/agent_factory/work_kinds/pull_request/workflow")


def test_task_validator_repairs_leave_out_of_scope_checks_for_a_human() -> None:
    text = (WORKFLOW / "factory-task-v1.0.yaml").read_text()
    for phase in ("initial", "final"):
        repair = text.split(f"      - id: repair-{phase}-validation\n", 1)[1].split(
            f"      - id: recheck-{phase}-validation\n", 1
        )[0]
        for phrase in (
            "either of these independent conditions",
            "not caused by this branch's changes",
            "git diff against the merge base",
            "the failing check's definition and configuration",
            "git, URL, or fork overrides or resolutions",
            "even if the branch caused the failure",
            "leave the check failing",
            "Out-of-scope failures needing a human decision",
            "what remedy a human would need to approve",
        ):
            assert phrase in repair, (phase, phrase)


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


def test_task_pre_push_guard_checks_scope_after_findings() -> None:
    task = (WORKFLOW / "factory-task-v1.0.yaml").read_text()
    guard = (WORKFLOW / "factory-task-guard-v1.0.yaml").read_text()
    after_findings = task.split("  - id: address-findings", 1)[1]
    before_push = after_findings.split("  - id: finalize-pr", 1)[0]
    assert "prepush-guard" in before_push
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
    triage = (WORKFLOW / "factory-task-v1.0.yaml").read_text()
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
