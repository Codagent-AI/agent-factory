"""Packaged feature catalog against a Runner with core/verify-change."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent_factory.work_kinds.pull_request.kinds import FEATURE_STAGED_FILES, FIX, registered

PACKAGE = files("agent_factory.work_kinds.pull_request") / "workflow"
RUNNER = Path(os.environ.get("FEATURE_TEST_RUNNER", shutil.which("agent-runner") or ""))


def test_implement_validator_repair_leaves_out_of_scope_checks_for_a_human() -> None:
    text = (PACKAGE / "factory-implement-v1.0.yaml").read_text()
    repair = text.split("  - id: repair-validation\n", 1)[1].split(
        "  - id: recheck-validation\n", 1
    )[0]
    for phrase in (
        "either of these independent conditions",
        "not caused by this branch's changes",
        "same check fails with the same error at the merge base",
        "origin/<target branch>",
        "temporary worktree",
        "lockfile entries",
        "check's definition and configuration",
        "If you cannot confirm it, treat the failure as caused by the branch",
        "If the branch added or changed the check or its policy, the branch caused the failure",
        "git, URL, or fork overrides or resolutions",
        "even if the branch caused the failure",
        "leave the check failing",
        "Out-of-scope failures needing a human decision",
        "what remedy a human would need to approve",
    ):
        assert phrase in repair, phrase


def suitable_runner() -> bool:
    compatible = False
    if RUNNER.is_file():
        # Probe Runner capabilities independently of the catalog being tested.
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "probe-v1.0.yaml"
            fixture.write_text(
                "name: probe\ndescription: known-good capability probe\nhidden: true\n"
                "steps:\n  - id: verify\n"
                "    workflow: builtin:core/verify-change-v1.0.yaml\n"
                "    params:\n      change_name: probe\n      change_dir: probe\n"
                "      change_label: probe\n      artifact_validation_instruction: probe\n"
                "    continue_on_failure: true\n"
            )
            result = subprocess.run(
                [str(RUNNER), "-validate", str(fixture)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            compatible = result.returncode == 0
    if not compatible and os.environ.get("FEATURE_REQUIRE_RUNNER") == "1":
        pytest.fail("required Agent Runner is missing or incompatible; set FEATURE_TEST_RUNNER")
    return compatible


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
    for step in ("implement", "verify", "finalize"):
        assert re.search(rf"- id: {step}\n(?:(?!  - id:).)*factory-resume-skip.sh", feature, re.S)
    assert re.search(
        r"- id: seed-archive-status\n(?:(?!  - id:).)*factory-resume-skip.sh"
        r"(?:(?!  - id:).)*capture: archive_status",
        feature,
        re.S,
    )
    assert re.search(
        r'- id: archive\n(?:(?!  - id:).)*skip_if: \'sh: test "{{archive_status}}" = skipped\'',
        feature,
        re.S,
    )
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
    assert "agent-validator run" in feature
    assert "agent-validator" not in define
    assert "capture: annotation_status" in feature
    assert 'annotation_status: "{{annotation_status}}"' in feature
    assert "mark-annotation-failed" in feature
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


def test_int009_feature_merge_steps_and_staged_catalog(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request import launch

    text = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    ids = re.findall(r"^  - id: ([\w-]+)$", text, re.MULTILINE)
    ordered = (
        "prepare-branch",
        "resolve-merge",
        "check-merge",
        "record-merge-stop",
        "continue-change",
        "create-change",
    )
    indexes = [ids.index(step) for step in ordered]
    assert indexes == sorted(indexes)
    assert re.search(r"  - name: base_head\n    default: \"\"", text)
    for name in ordered[1:-1]:
        block = text.split(f"  - id: {name}\n", 1)[1].split("\n  - id: ", 1)[0]
        assert "skip_if:" in block
        assert "feature-outcome.json" in block
    from agent_factory.work_kinds.pull_request.kinds import FEATURE

    staged = launch.stage_workflow(tmp_path, "factory-feature/1", FEATURE)
    for name in (
        "merge-base.sh",
        "continue-change.sh",
        "check-merge.sh",
        "record-merge-stop.sh",
        "review-merge-base.sh",
        "record-review-merge-stop.sh",
    ):
        assert name in launch.STAGED_FILES
        path = staged / name
        assert path.is_file()
        assert path.stat().st_mode & 0o111
    assert "{{base_head}}" in text


def test_staged_catalog_scripts_are_executable(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request import launch

    workflows = {
        workflow.name: workflow.read_text()
        for workflow in PACKAGE.iterdir()
        if workflow.name.endswith(".yaml")
    }
    workflow_scripts = {
        name: set(re.findall(r"^\s*script:\s*(\S+)\s*$", text, re.MULTILINE))
        for name, text in workflows.items()
    }
    workflow_calls = {
        name: {
            target
            for target in re.findall(r"^\s*workflow:\s*(\S+)\s*$", text, re.MULTILINE)
            if not target.startswith("builtin:")
        }
        for name, text in workflows.items()
    }
    scripts = {name for names in workflow_scripts.values() for name in names}
    assert scripts
    assert "task-compliance-gate.py" in scripts
    for name in scripts:
        assert name in launch.STAGED_FILES
        assert (PACKAGE / name).read_bytes().startswith(b"#!"), name

    review_staged_files = {
        launch.REVIEW_WORKFLOW_FILE,
        launch.IMPLEMENT_WORKFLOW_FILE,
        *launch.REVIEW_WORKFLOW_SCRIPTS,
    }
    # The task guard is shared with review and copied by the global staged catalog.
    task_guard = "factory-task-guard-v1.0.yaml"
    shared_guard_files = {task_guard} | workflow_scripts[task_guard]
    assert shared_guard_files <= review_staged_files
    contracts = [
        (
            kind.default_contract,
            kind,
            kind.workflow_file,
            set(kind.staged_files) | (shared_guard_files if kind.kind == "task" else set[str]()),
        )
        for kind in registered()
    ]
    contracts.append(
        (
            launch.REVIEW_CONTRACT,
            FIX,
            launch.REVIEW_WORKFLOW_FILE,
            review_staged_files,
        )
    )
    covered_workflows: set[str] = set()
    for index, (contract, kind, root, staged_files) in enumerate(contracts):
        catalog = launch.stage_workflow_into(tmp_path / str(index), contract, kind)
        pending = [root]
        seen: set[str] = set()
        referenced_scripts: set[str] = set()
        while pending:
            workflow = pending.pop()
            assert workflow in workflow_scripts, (contract, workflow)
            if workflow in seen:
                continue
            seen.add(workflow)
            assert workflow in staged_files, (contract, workflow)
            assert (catalog / workflow).is_file(), (contract, workflow)
            for name in workflow_scripts[workflow]:
                assert name in staged_files, (contract, workflow, name)
            referenced_scripts.update(workflow_scripts[workflow])
            pending.extend(workflow_calls[workflow])
        covered_workflows.update(seen)
        for name in referenced_scripts:
            assert os.access(catalog / name, os.X_OK), (contract, name)
    assert set(workflow_scripts) == covered_workflows


def test_staged_catalog_modes_follow_shebang_not_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.work_kinds.pull_request import launch

    package = tmp_path / "package" / "workflow"
    package.mkdir(parents=True)
    (package / FIX.workflow_file).write_text(f"{launch.contract_marker(FIX.default_contract)}\n")
    (package / "script-without-extension").write_bytes(b"#!/bin/sh\nexit 0\n")
    (package / "not-a-script.sh").write_text("plain data\n")

    def fake_files(_package: str) -> Path:
        return package.parent

    monkeypatch.setattr(launch, "files", fake_files)
    monkeypatch.setattr(
        launch,
        "STAGED_FILES",
        (FIX.workflow_file, "script-without-extension", "not-a-script.sh"),
    )

    catalog = launch.stage_workflow_into(tmp_path / "staged", FIX.default_contract, FIX)
    assert (catalog / "script-without-extension").stat().st_mode & 0o777 == 0o755
    assert (catalog / "not-a-script.sh").stat().st_mode & 0o777 == 0o644
    assert (catalog / FIX.workflow_file).stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("workflow", ["factory-feature-v1.0.yaml", "factory-review-v1.0.yaml"])
def test_merge_resolution_prompt_states_commit_boundary(workflow: str) -> None:
    text = (PACKAGE / workflow).read_text()
    prompt = text.split("  - id: resolve-merge\n", 1)[1].split("\n  - id: check-merge", 1)[0]
    for required in (
        "merge-conflict.json",
        "conflicted",
        "git commit --no-edit",
        "agent-validator run",
        "follow-up commits",
        "any file, including new files",
        "reset",
        "rebase",
        "squash",
        "amend",
        "merge-stop.json",
        "questions",
        "direction_summary",
        "leave the merge in progress",
    ):
        assert required in prompt
    assert prompt.index("git commit --no-edit") < prompt.index("agent-validator run")


@pytest.mark.parametrize("workflow", ["factory-feature-v1.0.yaml", "factory-review-v1.0.yaml"])
def test_merge_resolution_validator_repair_leaves_out_of_scope_checks_for_a_human(
    workflow: str,
) -> None:
    text = (PACKAGE / workflow).read_text()
    prompt = text.split("  - id: resolve-merge\n", 1)[1].split("\n  - id: check-merge", 1)[0]
    for phrase in (
        "either of these independent conditions",
        "not caused by this branch's changes",
        "same check fails with the same error at the merge base",
        "origin/<target branch>",
        "temporary worktree",
        "lockfile entries",
        "check's definition and configuration",
        "If you cannot confirm it, treat the failure as caused by the branch",
        "If the branch added or changed the check or its policy, the branch caused the failure",
        "git, URL, or fork overrides or resolutions",
        "even if the branch caused the failure",
        "leave the check failing",
        "Out-of-scope failures needing a human decision",
        "what remedy a human would need to approve",
    ):
        assert phrase in prompt, (workflow, phrase)


def test_task_compliance_repair_leaves_out_of_scope_checks_for_a_human() -> None:
    text = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    prompt = text.split("      - id: task-compliance-repair\n", 1)[1].split(
        "  - id: task-compliance-verified-final\n", 1
    )[0]
    for phrase in (
        "For CHECK failures from agent-validator check",
        "either of these independent conditions",
        "same check fails with the same error at the merge base",
        "origin/<target branch>",
        "temporary worktree",
        "lockfile entries",
        "check's definition and configuration",
        "If you cannot confirm it, treat the failure as caused by the branch",
        "If the branch added or changed the check or its policy, the branch caused the failure",
        "git, URL, or fork overrides or resolutions",
        "even if the branch caused the failure",
        "leave the check failing",
        "Out-of-scope failures needing a human decision",
        "what remedy a human would need to approve",
    ):
        assert phrase in prompt, phrase


def test_classification_order_and_failure_wiring(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.kinds import FEATURE
    from agent_factory.work_kinds.pull_request.launch import stage_workflow

    catalog = stage_workflow(tmp_path, "factory-feature/1", FEATURE)
    feature = (catalog / "factory-feature-v1.0.yaml").read_text()
    workflow = cast(dict[str, Any], yaml.safe_load(feature))
    steps = cast(list[dict[str, Any]], workflow["steps"])
    ids = [step["id"] for step in steps]
    by_id = {step["id"]: step for step in steps}
    ordered = (
        "finalize",
        "mark-ci-failed",
        "seed-classification-reasons",
        "seed-classification-counts",
        "seed-classification-status",
        "classify",
        "mark-classify-failed",
        "verify-classification",
        "mark-classification-invalid",
        "record-classification-failure",
        "clear-classification-counts",
        "seed-annotation-status",
        "annotate-pr",
        "record-outcome",
    )
    indexes = [ids.index(step) for step in ordered]
    assert indexes == sorted(indexes)
    assert ids[ids.index("classify") - 1] == "seed-classification-status"
    for name in ("classify", "verify-classification", "record-classification-failure"):
        assert by_id[name]["continue_on_failure"] is True
    for name in ("verify-classification", "annotate-pr"):
        assert 'test "{{classification_status}}" != passed' in by_id[name]["skip_if"]
    assert by_id["seed-annotation-status"]["command"] == (
        'if test "{{classification_status}}" = passed; then printf passed; else printf failed; fi'
    )
    inputs = by_id["record-outcome"]["script_inputs"]
    assert inputs["reasons"] == "{{classification_reasons}}"
    assert inputs["review_attention_counts"] == "{{classification_counts}}"
    assert by_id["seed-classification-reasons"]["command"] == "printf '[]'"
    assert by_id["seed-classification-counts"]["capture"] == "classification_counts"
    assert by_id["clear-classification-counts"]["command"] == "printf ''"
    for name, status in (
        ("mark-classify-failed", "session-failed"),
        ("mark-classification-invalid", "invalid"),
    ):
        assert by_id[name]["command"] == f"printf {status}"
        assert by_id[name]["capture"] == "classification_status"
        assert by_id[name]["skip_if"] == "previous_success"
    for name in ("record-classification-failure", "clear-classification-counts"):
        assert by_id[name]["skip_if"] == 'sh: test "{{classification_status}}" = passed'
    assert by_id["record-classification-failure"]["capture"] == "classification_reasons"
    assert "record-classification-failure.py" in FEATURE_STAGED_FILES
    assert os.access(catalog / "record-classification-failure.py", os.X_OK)
    classify_step = re.search(r"- id: classify\n(?:(?!  - id:).)*", feature, re.S)
    assert classify_step
    classify = " ".join(classify_step.group(0).split())
    for required_red in (
        "acceptance criterion that failed",
        "could not be verified and that no automated test covers",
        "acceptance that did not complete",
        "known deviation from the specifications or from a decision the issue settled",
        "fell back to a",
    ):
        assert required_red in classify
    assert "Read {{issue_file}} and its settled decisions" in classify
    assert "share one root cause a single item" in classify
    assert "only follows a decision the issue settled or an acceptance criterion" in classify
    assert "git diff --shortstat <accepted_head> HEAD" in classify
    assert "sentence starting `Tests:`" in classify
    verify = re.search(r"- id: verify-classification\n(?:(?!  - id:).)*", feature, re.S)
    assert verify
    assert "repair:\n      session: lead-agent" in verify.group(0)
    # A criterion acceptance did not exercise but a named automated test covers is
    # not a failure: it is yellow and names the covering test, so it does not bury
    # real red items.
    assert "failed or unverified" not in classify
    assert (
        "Yellow: each acceptance criterion acceptance did not exercise but a named "
        "automated test covers, naming that test in its detail"
    ) in classify
