"""Feature host command includes every required workflow input and role."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest

from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import FEATURE
from tests.integration.test_host_launch import ROLES, Built, validator_fixture


def test_feature_host_plan_records_reported_validator_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    built.runner.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        "  -validate) exit 0 ;;\n"
        '  -version) echo "stub-runner 1.2.3" ;;\n'
        '  run) echo "Usage: agent-runner run <workflow> '
        '[--session-dir <path>] [--param key=value]" ;;\n'
        "esac\n"
    )
    checkout, old, executable = validator_fixture(tmp_path, built.runner.parent, "echo placeholder")
    executable.write_text(f"#!/bin/sh\necho '{old[:7]} old'\n")
    monkeypatch.setenv("PATH", f"{built.runner.parent}:/usr/bin:/bin")
    plan = launch.build_host_plan(
        evidence=built.evidence,
        repo_clone=built.clone,
        credential_copy=built.credential,
        roles={**ROLES, "crosscheck": "codex:m:high"},
        branch="factory/feature-7-claim",
        contract="factory-feature/1",
        definition=FEATURE,
        change_name="feature-7-claim",
        validator_checkout=checkout,
    )
    provenance = json.loads((built.evidence / "host-provenance.json").read_text())
    assert plan.ownership_hints["validator_commit"] == old
    assert provenance["validator_commit"] == old
    assert provenance["validator_executable"] == str(executable.resolve())
    assert old in provenance["note"]


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
        "--param base_head=''",
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


@pytest.mark.parametrize(
    ("contract", "workflow_file"),
    [
        ("factory-feature/1", "factory-feature-v1.0.yaml"),
        ("factory-review/1", "factory-review-v1.0.yaml"),
    ],
)
def test_feature_host_command_passes_only_params_its_workflow_declares(
    contract: str, workflow_file: str
) -> None:
    """The Runner rejects an undeclared --param, so a feature review round must not
    receive the feature workflow's change_name, resume_from, or prior_branch."""
    import re

    command = launch.host_script(
        runner="/bin/agent-runner",
        repo_clone=Path("/repo"),
        evidence=Path("/evidence"),
        credential_copy=Path("/private/feature.env"),
        gitconfig=Path("/private/gitconfig"),
        askpass=Path("/private/askpass.sh"),
        branch="factory/feature-42-abcd1234",
        contract=contract,
        definition=FEATURE,
        change_name="feature-42-abcd1234",
    )
    text = (files("agent_factory.work_kinds.pull_request") / "workflow" / workflow_file).read_text()
    params_block = re.search(r"^params:\n((?:[ -].*\n)+)", text, re.MULTILINE)
    assert params_block is not None
    declared = set(re.findall(r"^  - name: (\w+)", params_block.group(1), re.MULTILINE))
    passed = set(re.findall(r"--param (\w+)=", command))
    assert passed <= declared, passed - declared


def test_int007_controller_records_moved_base_and_holds_fetch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import BranchInfo, PullRequestInfo
    from agent_factory.store import ClaimDraft, ClaimStore
    from agent_factory.work_kinds.pull_request import handler
    from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace
    from tests.integration.test_feature_workflow_scripts import git, repository
    from tests.integration.test_fix_gestures import _LOCAL_BASE, _SHARED_BASE

    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "claim")
    (repo / "plan.txt").write_text("planned\n")
    git(repo, "add", "plan.txt")
    git(repo, "commit", "-m", "plan", "-m", "Factory-Checkpoint: planned")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "main")
    (repo / "main.txt").write_text("moved\n")
    git(repo, "add", "main.txt")
    git(repo, "commit", "-m", "main moved")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "main")
    runner = tmp_path / "runner"
    skills = tmp_path / "skills"
    for path in (runner, skills):
        path.mkdir()
        git(path, "init", "-b", "main")
        git(path, "config", "user.name", "Test")
        git(path, "config", "user.email", "test@example.com")
        (path / "version").write_text("frozen\n")
        git(path, "add", "version")
        git(path, "commit", "-m", "frozen")
    runner_frozen, skills_frozen = (git(path, "rev-parse", "HEAD") for path in (runner, skills))
    for path in (runner, skills):
        (path / "version").write_text("new head\n")
        git(path, "add", "version")
        git(path, "commit", "-m", "new head")
    storage = tmp_path / "storage"
    mirror = storage / "mirrors/example__work.git"
    mirror.parent.mkdir(parents=True)
    git(tmp_path, "clone", "--mirror", str(remote), str(mirror))
    store = ClaimStore(tmp_path / "state.sqlite3")
    roles = {role: "codex:test:high" for role in FEATURE.roles}
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            7,
            "I7",
            "P7",
            "feature",
            "fp",
            {
                "target": {"repository": "example/work", "branch": "main"},
                "revisions": {
                    "target": admission,
                    "runner": runner_frozen,
                    "skills": skills_frozen,
                },
                "roles": roles,
            },
        )
    )
    branch = f"factory/feature-7-{claim.id[:8]}"
    git(repo, "push", "origin", f"{head}:refs/heads/{branch}")
    store.set_preparation(claim.id, {"resume": {"branch": branch, "head_sha": head}})

    class GitHub:
        def get_branch(self, repository: str, name: str) -> BranchInfo | None:
            return BranchInfo(name, head) if name == branch else None

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            return []

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    credential = tmp_path / "credential.env"
    credential.write_text("GH_TOKEN=test-token\n")
    local = LocalConfig.from_toml(_LOCAL_BASE)
    local = replace(
        local,
        storage_root=storage,
        credentials=replace(local.credentials, fix_environment=credential),
        repositories=replace(local.repositories, agent_runner=runner, agent_skills=skills),
    )
    workspace = PullRequestWorkspace(storage, runner, skills)
    feature = handler.PullRequestHandler(
        FEATURE, SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"), local, workspace=workspace
    )
    feature.attach_store(store)
    feature.attach_github(GitHub())  # type: ignore[arg-type]

    def issue_input(_claim: object) -> dict[str, object]:
        return {"repository": "example/work", "number": 7}

    def authentication(_roles: Mapping[str, str]) -> list[object]:
        return []

    monkeypatch.setattr(feature, "_issue_input", issue_input)
    monkeypatch.setattr(handler, "model_authentication", authentication)
    prepared = feature.prepare(store.get_claim(claim.id) or claim)
    assert prepared.payload["base_head"] == base
    assert prepared.payload["resume_from"] == "implement"
    stored = store.get_claim(claim.id)
    assert stored is not None
    assert stored.preparation["base_head"] == base
    clones = cast(Mapping[str, str], prepared.payload["clones"])
    assert git(Path(clones["runner"]), "rev-parse", "HEAD") == runner_frozen
    assert git(Path(clones["skills"]), "rev-parse", "HEAD") == skills_frozen
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "agent-runner"
    executable.write_text(
        '#!/bin/sh\ncase "$1" in\n'
        '  -version) echo "stub 1" ;;\n'
        "  -validate) exit 0 ;;\n"
        '  run) echo "Usage: agent-runner run <workflow> [--param key=value]" ;;\n'
        "esac\n"
    )
    executable.chmod(0o755)

    def runner_executable(_value: str | None = None) -> str:
        return str(executable)

    monkeypatch.setattr(launch, "resolve_runner_executable", runner_executable)
    run = store.reserve_run(
        claim.id, "feature", lane="low", reason="initial", evidence_path=str(tmp_path / "evidence")
    )
    plan = feature.plan(store.get_claim(claim.id) or claim, run, prepared)
    assert plan.argv[0] == "/bin/bash"
    evidence = Path(run.evidence_path) / "attempt-1"
    provenance = json.loads((evidence / "host-provenance.json").read_text())
    assert provenance["target_at_admission"] == admission
    assert provenance["base_head"] == base
    assert f"--param base_head={base}" in (storage / "private" / run.id / "host-run.sh").read_text()
    stored = store.get_claim(claim.id)
    assert stored is not None
    assert stored.frozen_spec["revisions"] == {
        "target": admission,
        "runner": runner_frozen,
        "skills": skills_frozen,
    }
    assert any(f"merges main@{base[:7]}" in event.body for event in store.pending_events(claim.id))
    runs_before = len(store.runs_for_claim(claim.id))
    recovery_before = store.recovery_attempts(claim.id, "feature")

    def fail_fetch(repository: str, token: str | None) -> None:
        raise ReadinessError("mirror fetch failed")

    monkeypatch.setattr(workspace, "fetch_mirror", fail_fetch)
    with pytest.raises(ReadinessError, match="mirror fetch failed"):
        feature.prepare(store.get_claim(claim.id) or claim)
    assert len(store.runs_for_claim(claim.id)) == runs_before
    assert store.recovery_attempts(claim.id, "feature") == recovery_before
