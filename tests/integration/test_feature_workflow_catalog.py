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


def test_implement_validator_repair_leaves_out_of_scope_checks_for_a_human() -> None:
    text = (PACKAGE / "factory-implement-v1.0.yaml").read_text()
    repair = text.split("  - id: repair-validation\n", 1)[1].split(
        "  - id: recheck-validation\n", 1
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
        assert phrase in repair, phrase


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
        "git diff against the merge base",
        "the failing check's definition and configuration",
        "git, URL, or fork overrides or resolutions",
        "even if the branch caused the failure",
        "leave the check failing",
        "Out-of-scope failures needing a human decision",
        "what remedy a human would need to approve",
    ):
        assert phrase in prompt, (workflow, phrase)
