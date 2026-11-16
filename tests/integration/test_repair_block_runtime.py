"""INT-007 and INT-008: fresh installed-Runner audits and resumed plan publication."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.repair_block.harness import execute, sandbox
from tests.integration.test_feature_workflow_scripts import PACKAGE, git, repository, run


def test_installed_runner_repair_block(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    runner, env, catalog = sandbox(repo, tmp_path)
    (catalog / "nested-v1.0.yaml").write_text("""name: nested
hidden: true
description: audit contract
steps:
  - id: check
    command: exit 1
    repair:
      agent: lead
      prompt: STUB_BLOCK
""")
    (catalog / "block-v1.0.yaml").write_text("""name: block
hidden: true
description: audit contract
steps:
  - id: outer
    workflow: nested-v1.0.yaml
""")
    session = tmp_path / "session"
    result = execute(
        runner, repo, env, "run", "block", "--profile", "stub", "--session-dir", str(session)
    )
    assert result.returncode != 0
    audit = (session / "audit.log").read_text()
    assert "repair_blocked" in audit, result.stdout + result.stderr + audit
    result = run(
        "python3",
        str(PACKAGE / "repair-block.py"),
        "run",
        "--session-dir",
        str(session),
        "--artifact-dir",
        str(tmp_path / "artifacts"),
        "--branch",
        "claim",
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads((tmp_path / "artifacts/feature-outcome.json").read_text())
    assert outcome["blocked_step"] == "outer, sub:nested, check"
    assert outcome["reasons"] == ["A human must resolve the test blocker."]


@pytest.mark.parametrize("resume", ["", "design"])
def test_definition_publishes_regenerated_plan(tmp_path: Path, resume: str) -> None:
    repo, remote = repository(tmp_path)
    (repo / ".validator").mkdir()
    (repo / ".validator/config.yml").write_text("entry_points: []\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "test config")
    git(repo, "push", "origin", "main")
    if resume:
        git(repo, "checkout", "-b", "claim")
        change = repo / "openspec/changes/runtime-plan"
        (change / "specs/runtime").mkdir(parents=True)
        (change / "proposal.md").write_text("prior proposal\n")
        (change / "specs/runtime/spec.md").write_text("prior specs\n")
        (change / "design.md").write_text("old design\n")
        (change / "test-plan.md").write_text("old test plan\n")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "definition stop")
        git(repo, "push", "origin", "claim")
        git(repo, "checkout", "main")
    runner, env, _ = sandbox(repo, tmp_path)
    artifacts = tmp_path / "artifacts"
    issue = artifacts / "issue.json"
    issue.write_text("{}")
    result = execute(
        runner,
        repo,
        env,
        "run",
        "factory-feature",
        "--profile",
        "stub",
        "--until",
        "define",
        "--session-dir",
        str(tmp_path / "session"),
        "--param",
        f"issue_file={issue}",
        "--param",
        "branch_name=claim",
        "--param",
        "change_name=runtime-plan",
        "--param",
        "contract_version=factory-feature/1",
        "--param",
        f"artifact_dir={artifacts}",
        "--param",
        f"resume_from={resume}",
        "--param",
        "prior_branch=",
        "--param",
        "base_head=",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    produced = [
        json.loads(line) for line in (artifacts / "producers.jsonl").read_text().splitlines()
    ]
    assert produced == (
        ["design", "test-plan", "write-tasks"]
        if resume
        else ["proposal", "specs", "design", "test-plan", "write-tasks"]
    )
    tasks = git(remote, "show", "refs/heads/claim:openspec/changes/runtime-plan/tasks.md")
    assert "design\ntest-plan" in tasks
    assert "old design" not in tasks
    assert "Factory-Checkpoint: planned" in git(remote, "log", "-1", "--format=%B", "claim")
    assert not (artifacts / "resume.json").exists()
