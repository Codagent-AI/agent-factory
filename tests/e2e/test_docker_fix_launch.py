"""E2E-004: real Docker fix launches from factory-prepared Runner clones, no model calls."""

# ruff: noqa: E501

from __future__ import annotations

import json
import os
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, launch_supervisor
from agent_factory.work_kinds.fix import launch
from agent_factory.work_kinds.fix.workspace import FixWorkspace
from agent_factory.work_kinds.images import remove_images

TEST_WORKFLOW = """# factory-contract: factory-fix/1
name: factory-fix
description: "Model-free stand-in that records what the sandbox exposes."
hidden: true

params:
  - name: issue_file
    required: true
  - name: branch_name
    required: true
  - name: contract_version
    required: true

steps:
  - id: record
    command: |
      env | cut -d= -f1 | sort > /artifacts/env-names.txt
      printf '%s\\n' "${AGENT_RUNNER_SOURCE_COMMIT:-}" > /artifacts/source-commit.txt
      if touch /workspace/repo/.factory-writable 2>/dev/null; then echo writable; else echo readonly; fi > /artifacts/repo-mode.txt
      if touch /workspace/skills/.factory-writable 2>/dev/null; then echo writable; else echo readonly; fi > /artifacts/skills-mode.txt
      cp "{{issue_file}}" /artifacts/issue-copy.json
      printf '%s\\n' "{{branch_name}}" > /artifacts/branch.txt
      printf '{"contract":"factory-fix/1","outcome":"failed","reasons":["model-free test workflow"],"validator":{"status":"skipped"}}' > /artifacts/fix-outcome.json
"""


def _run(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip()


def _git(path: Path, *args: str) -> str:
    return _run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=Factory Test",
            "-c",
            "user.email=test@example.invalid",
            *args,
        ]
    )


@pytest.mark.docker
def test_e2e_004_real_docker_fix_launches_are_isolated_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = os.environ.get("AGENT_FACTORY_DOCKER_SOURCES")
    if not root:
        pytest.skip(
            "Required separate Docker check: set AGENT_FACTORY_DOCKER_SOURCES; not passing evidence"
        )
    sources = Path(root)
    runner = tmp_path / "runner"
    _run(["git", "clone", "--quiet", "--no-hardlinks", str(sources / "agent-runner"), str(runner)])
    workflow = runner / "workflows" / "core" / "factory-fix-v1.0.yaml"
    workflow.write_text(TEST_WORKFLOW, encoding="utf-8")
    # A planted default secrets file must never reach the container.
    (runner / ".sandbox-secrets.env").write_text("PLANTED_SECRET=leak\n", encoding="utf-8")
    _git(runner, "add", "workflows/core/factory-fix-v1.0.yaml")
    _git(runner, "commit", "-q", "-m", "test: model-free factory-fix stand-in")
    runner_sha = _git(runner, "rev-parse", "HEAD")
    skills = tmp_path / "skills"
    _run(["git", "clone", "--quiet", "--no-hardlinks", str(sources / "agent-skills"), str(skills)])
    skills_sha = _git(skills, "rev-parse", "HEAD")
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "-q", "-b", "main")
    (target / "README.md").write_text("fixture\n")
    _git(target, "add", "README.md")
    _git(target, "commit", "-q", "-m", "fixture")
    target_sha = _git(target, "rev-parse", "HEAD")
    storage = tmp_path / "storage"
    (storage / "mirrors").mkdir(parents=True)
    _run(
        [
            "git",
            "clone",
            "--quiet",
            "--mirror",
            str(target),
            str(storage / "mirrors" / "example__work.git"),
        ]
    )
    workspace = FixWorkspace(storage, runner, skills)
    credential = tmp_path / "fix.env"
    credential.write_text("GH_TOKEN=dummy-fix-token\n")
    credential.chmod(0o600)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("{}")
    revisions = {"target": target_sha, "runner": runner_sha, "skills": skills_sha}
    launched: list[tuple[Path, Path, str, subprocess.Popen[bytes]]] = []
    tags: list[str] = []
    monkeypatch.setenv("HOME", str(home))
    try:
        # One state file per launch: the per-kind slot allows one fix run per factory, and
        # this test proves build isolation between two concurrent sandbox launches.
        for index in range(2):
            state = tmp_path / f"state-{index}.sqlite3"
            with closing(ClaimStore(state)) as store:
                claim = store.create_claim(
                    ClaimDraft(
                        "example/work", index + 1, f"I{index}", f"P{index}", "fix", f"fp{index}", {}
                    )
                )
                run = store.reserve_run(
                    claim.id,
                    "fix",
                    reason="initial",
                    evidence_path=str(tmp_path / f"evidence-{index}"),
                )
                clones = workspace.prepare_clones(claim.id, 0, "example/work", revisions)
                evidence = Path(run.evidence_path) / "attempt-1"
                evidence.mkdir(parents=True)
                launch.write_issue_input(
                    evidence,
                    {"repository": "example/work", "number": index + 1, "title": "t", "body": "b"},
                )
                plan = launch.build_plan(
                    run_id=run.id,
                    evidence=evidence,
                    clones=clones,
                    credential_copy=credential,
                    roles={
                        "lead": "codex:m:high",
                        "implementor": "codex:m:high",
                        "tester": "codex:m:high",
                    },
                    branch=f"factory/fix-{index + 1}-{claim.id[:8]}",
                    contract="factory-fix/1",
                    bootstrap_skills=False,
                )
                tags.append(launch.image_tag(run.id))
                watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(600, 900, 900))
                launched.append((state, evidence, run.id, watcher))
        for state, evidence, run_id, watcher in launched:
            watcher.wait(timeout=900)
            with closing(ClaimStore(state)) as store:
                finished = store.get_run(run_id)
            assert finished is not None, run_id
            log = (evidence / "factory-suite.log").read_text(errors="replace")[-3000:]
            assert (evidence / "fix-outcome.json").exists(), log
            outcome = json.loads((evidence / "fix-outcome.json").read_text())
            assert outcome["outcome"] == "failed"
            assert (evidence / "source-commit.txt").read_text().strip() == runner_sha
            assert (evidence / "repo-mode.txt").read_text().strip() == "writable"
            assert (evidence / "skills-mode.txt").read_text().strip() == "readonly"
            names = (evidence / "env-names.txt").read_text().split()
            assert "GH_TOKEN" in names
            assert "PLANTED_SECRET" not in names
            assert (
                json.loads((evidence / "issue-copy.json").read_text())["repository"]
                == "example/work"
            )
            # The sandbox writes fix-outcome.json, not the suite result the supervisor
            # polls for, so the run ends as an exit the handler then reads the outcome of.
            assert finished.status in {"interrupted", "completed"}, finished.result
            assert finished.progress["image_tag"] == launch.image_tag(run_id)
            container = finished.progress.get("container")
            assert isinstance(container, dict), finished.progress
            assert container["artifact_path"] == str(evidence)
            image_id = _run(
                ["docker", "image", "inspect", "--format", "{{.Id}}", launch.image_tag(run_id)]
            )
            assert container["image"] == image_id
        assert len(set(tags)) == 2
    finally:
        for _state, _evidence, _run_id, watcher in launched:
            if watcher.poll() is None:
                watcher.kill()
        errors = remove_images(tags)
        assert not errors, errors
        assert all(
            subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0
            for tag in tags
        )
