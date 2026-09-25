"""Feature host command includes every required workflow input and role."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import FEATURE


def test_feature_host_command_starts_fresh_with_change_name() -> None:
    command = launch.host_script(
        runner="/bin/agent-runner",
        repo_clone=Path("/repo"),
        evidence=Path("/evidence"),
        credential_copy=Path("/private/feature.env"),
        gitconfig=Path("/private/gitconfig"),
        askpass=Path("/private/askpass.sh"),
        branch="factory/feature-42-abcd1234",
        contract="factory-feature/1",
        definition=FEATURE,
        change_name="feature-42-abcd1234",
    )
    for expected in (
        "run factory-feature",
        "--param issue_file=/evidence/input/issue.json",
        "--param change_name=feature-42-abcd1234",
        "--param resume_from=''",
        "--param prior_branch=''",
        "--param contract_version=factory-feature/1",
        "--session-dir /evidence/agent-runner-session",
    ):
        assert expected in command


def test_feature_host_command_derives_change_name_when_not_supplied() -> None:
    command = launch.host_script(
        runner="/bin/agent-runner",
        repo_clone=Path("/repo"),
        evidence=Path("/evidence"),
        credential_copy=Path("/private/feature.env"),
        gitconfig=Path("/private/gitconfig"),
        askpass=Path("/private/askpass.sh"),
        branch="factory/feature-42-abcd1234",
        contract="factory-feature/1",
        definition=FEATURE,
    )
    assert "--param change_name=feature-42-abcd1234" in command


def test_feature_profiles_include_crosscheck() -> None:
    profiles = launch.role_profiles(
        {
            "lead": "codex:l:high",
            "implementor": "codex:i:high",
            "tester": "codex:t:high",
            "crosscheck": "claude:c:high",
        },
        FEATURE,
    )
    assert profiles["crosscheck"] == ("claude", "c", "high")


def test_feature_launch_refuses_runner_without_verify_change(tmp_path: Path) -> None:
    runner = tmp_path / "agent-runner"
    runner.write_text('#!/bin/sh\necho "unknown core/verify-change" >&2\nexit 1\n')
    runner.chmod(0o755)
    workflow = tmp_path / "factory-feature-v1.0.yaml"
    workflow.write_text("name: factory-feature\n")
    with pytest.raises(ReadinessError, match="core/verify-change"):
        launch.check_host_runner_workflow(str(runner), workflow, FEATURE)


def test_feature_launch_refuses_incompatible_define_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = files("agent_factory.work_kinds.pull_request") / "workflow"
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    (workflow / FEATURE.workflow_file).write_text((package / FEATURE.workflow_file).read_text())
    (workflow / "factory-define-v1.0.yaml").write_text(
        "# factory-contract: factory-feature/2\nname: factory-define\n"
    )

    def package_files(_package: str) -> Path:
        return tmp_path

    monkeypatch.setattr(launch, "files", package_files)
    with pytest.raises(ReadinessError, match="factory-define"):
        launch.check_packaged_workflow("factory-feature/1", FEATURE)
