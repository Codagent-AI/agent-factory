"""INT-001 to INT-006: real host wrapper, git publication and archive delegation."""

from __future__ import annotations

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_factory import audit as factory_audit
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import FEATURE
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome
from tests.fixtures.repair_block.audits import FIXTURE, PATH, REASON, blocked
from tests.integration.test_feature_workflow_scripts import PACKAGE, git, repository, run
from tests.integration.test_host_launch import ROLES, Built

rb: Any = SimpleNamespace(**runpy.run_path(str(PACKAGE / "repair-block.py")))


def prepare(repo: Path, artifacts: Path, resume: str = "", prior: str = "") -> None:
    artifacts.mkdir(exist_ok=True)
    result = run(
        "sh",
        str(PACKAGE / "prepare-branch.sh"),
        "claim",
        git(repo, "rev-parse", "main"),
        resume,
        prior,
        str(artifacts),
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == resume


def record(repo: Path, artifacts: Path, audit: str = "") -> dict[str, Any] | None:
    session = artifacts / "session"
    session.mkdir(exist_ok=True)
    (session / "audit.log").write_text(audit or blocked())
    result = run(
        "python3",
        str(PACKAGE / "repair-block.py"),
        "run",
        "--session-dir",
        str(session),
        "--artifact-dir",
        str(artifacts),
        "--branch",
        "claim",
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    outcome = artifacts / "feature-outcome.json"
    if not outcome.exists():
        return None
    assert read_interpreted_outcome(artifacts, "factory-feature/1").outcome is not None
    assert (
        run(
            "python3", str(PACKAGE / "verify-feature-outcome.py"), str(outcome), cwd=repo
        ).returncode
        == 0
    )
    return json.loads(outcome.read_text())


@pytest.mark.parametrize(
    "phase,resume,expected",
    [
        ("planned", "", "implement"),
        ("archived", "", "verify"),
        ("implemented", "", "archive"),
        ("", "design", "design"),
        ("", "", ""),
    ],
)
def test_published_progress(tmp_path: Path, phase: str, resume: str, expected: str) -> None:
    repo, remote = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    if resume:
        git(repo, "push", "origin", "HEAD:refs/heads/claim")
    prepare(repo, artifacts, resume)
    if phase == "implemented":
        change = repo / "openspec/changes/runtime"
        change.mkdir(parents=True)
        (change / "tasks.md").write_text("- [ ] Implement runtime task\n")
    if phase:
        assert (
            run(
                "sh", str(PACKAGE / "checkpoint.sh"), phase, "claim", "runtime", cwd=repo
            ).returncode
            == 0
        )
    pushed = git(remote, "rev-parse", "refs/heads/claim") if phase or resume else ""
    (repo / "partial.txt").write_text("unpublished work")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "unpublished")
    outcome = record(repo, artifacts)
    assert outcome is not None
    assert outcome.get("stopped_step", "") == expected
    assert outcome.get("branch", "") == ("claim" if pushed else "")
    if pushed:
        assert git(remote, "rev-parse", "refs/heads/claim") == pushed
    else:
        assert "fresh definition" in outcome["direction_summary"]
    git(repo, "checkout", "main")
    second = tmp_path / "second"
    prepare(repo, second, expected)
    assert not (second / "resume.json").exists()
    if expected == "design":
        for step in ("design", "test-plan"):
            assert (
                run(
                    "sh", str(PACKAGE / "factory-resume-skip.sh"), "design", step, cwd=repo
                ).returncode
                == 1
            )


def test_start_excludes_merged_target_checkpoints(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    initial = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "claim")
    git(repo, "commit", "--allow-empty", "-m", "draft")
    git(repo, "push", "origin", "claim")
    git(repo, "checkout", "main")
    git(repo, "commit", "--allow-empty", "-m", "target squash\n\nFactory-Checkpoint: archived")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = run(
        "sh",
        str(PACKAGE / "prepare-branch.sh"),
        "claim",
        git(repo, "rev-parse", "main"),
        "design",
        "",
        str(artifacts),
        initial,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    outcome = record(repo, artifacts)
    assert outcome is not None and outcome["stopped_step"] == "design"
    (artifacts / "feature-outcome.json").unlink()
    (artifacts / "attempt-start.json").unlink()
    outcome = record(repo, artifacts)
    assert outcome is not None and "stopped_step" not in outcome


@pytest.mark.parametrize("reject", [False, True])
def test_unpublished_continuation(tmp_path: Path, reject: bool) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "prior")
    git(repo, "commit", "--allow-empty", "-m", "prior\n\nFactory-Checkpoint: planned")
    prior_head = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    artifacts = tmp_path / "artifacts"
    prepare(repo, artifacts, "implement", "prior")
    assert json.loads((artifacts / "attempt-start.json").read_text())["prior_head"] == prior_head
    git(repo, "commit", "--allow-empty", "-m", "partial local work")
    if reject:
        hook = remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
    outcome = record(repo, artifacts)
    if reject:
        assert outcome is None
    else:
        assert outcome is not None and outcome["stopped_step"] == "implement"
        assert git(remote, "rev-parse", "refs/heads/claim") == prior_head
        git(repo, "checkout", "main")
        prepare(repo, tmp_path / "second", "implement")
        assert not (tmp_path / "second/resume.json").exists()


@pytest.mark.parametrize("planned", [False, True])
def test_wrapper_records_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planned: bool
) -> None:
    monkeypatch.setattr(factory_audit, "AUDIT_ENABLED", True)
    built = Built(tmp_path, monkeypatch)
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(built.clone, "remote", "add", "origin", str(remote))
    git(built.clone, "push", "origin", "main")
    prepare(built.clone, built.evidence)
    if planned:
        assert (
            run(
                "sh", str(PACKAGE / "checkpoint.sh"), "planned", "claim", cwd=built.clone
            ).returncode
            == 0
        )
    built.runner.write_text(
        built.runner.read_text().replace("  -version)", "  -validate) exit 0 ;;\n  -version)")
    )
    launch.build_host_plan(
        evidence=built.evidence,
        repo_clone=built.clone,
        credential_copy=built.credential,
        roles={**ROLES, "crosscheck": ROLES["lead"]},
        branch="claim",
        contract="factory-feature/1",
        definition=FEATURE,
    )
    session = built.evidence / launch.SESSION_DIR_NAME
    session.mkdir()
    audit = blocked() if planned else blocked("define, sub:factory-define, specs, check")
    built.runner.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(session / 'audit.log')!r}).write_text({audit!r})\nraise SystemExit(1)\n"
    )
    result = subprocess.run(["/bin/bash", str(built.wrapper)], text=True, capture_output=True)
    assert result.returncode == 1, result.stdout + result.stderr
    outcome = json.loads((built.evidence / "feature-outcome.json").read_text())
    assert outcome.get("stopped_step", "") == ("implement" if planned else "")
    assert outcome.get("branch", "") == ("claim" if planned else "")
    assert outcome["reasons"] == [REASON]
    assert outcome["blocked_step"] == (
        PATH if planned else "define, sub:factory-define, specs, check"
    )
    assert read_interpreted_outcome(built.evidence, "factory-feature/1").outcome is not None
    assert (
        run(
            "python3",
            str(PACKAGE / "verify-feature-outcome.py"),
            str(built.evidence / "feature-outcome.json"),
            cwd=built.clone,
        ).returncode
        == 0
    )
    wrapper = built.wrapper.read_text()
    assert wrapper.index("repair-block.py") < wrapper.index("agent_factory.audit host")
    assert " -I " in wrapper and "|| true" in wrapper


def test_portable_real_runner_fixture(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    outcome = record(repo, artifacts, FIXTURE.read_text())
    assert outcome is not None and outcome["blocked_step"] == "outer, sub:nested, check"
    assert outcome["reasons"] == [REASON]
    assert len(FIXTURE.with_name("source-revision").read_text().strip()) == 40


def test_existing_outcome_is_untouched(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    path = artifacts / "feature-outcome.json"
    original = json.dumps(
        {
            "contract": "factory-feature/1",
            "outcome": "failed",
            "reasons": ["verify failed"],
            "branch": "claim",
        }
    )
    path.write_text(original)
    record(repo, artifacts)
    assert path.read_text() == original


def test_ls_remote_failure_falls_back_to_tracking_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = repository(tmp_path)
    git(repo, "push", "origin", "HEAD:refs/heads/claim")
    monkeypatch.chdir(repo)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    assert rb.published("claim")
    assert not rb.published("absent")


@pytest.mark.parametrize("payload", [False, True])
def test_archive_delegates_without_run_end(tmp_path: Path, payload: bool) -> None:
    repo, _ = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = artifacts / "session"
    session.mkdir()
    path = "archive, sub:archive-change, archive-transition"
    (session / "audit.log").write_text(blocked(path).rsplit("2026-01-01T00:00:00Z run_end", 1)[0])
    script = str(PACKAGE / "record-archive-block.sh")
    if payload:
        done = run(
            "sh",
            script,
            cwd=repo,
            input=json.dumps(
                {
                    "artifact_dir": str(artifacts),
                    "branch_name": "claim",
                    "session_dir": str(session),
                }
            ),
        )
    else:
        done = run("sh", script, str(artifacts), "claim", str(session), cwd=repo)
    assert done.returncode == 0, done.stderr
    outcome = json.loads((artifacts / "feature-outcome.json").read_text())
    assert outcome == {
        "contract": "factory-feature/1",
        "outcome": "needs-input",
        "stopped_step": "archive",
        "branch": "claim",
        "reasons": [REASON],
        "questions": [REASON],
        "blocked_step": path,
        "direction_summary": rb.ARCHIVE_SUMMARY,
    }


@pytest.mark.parametrize(
    "audit",
    [
        blocked(failure_kind="infrastructure"),
        blocked(declaration=False),
        blocked(response="REPAIR_BLOCKED"),
        "",
    ],
)
def test_unexplained_failure_writes_nothing(tmp_path: Path, audit: str) -> None:
    repo, _ = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = artifacts / "session"
    session.mkdir()
    (session / "audit.log").write_text(audit)
    assert (
        run(
            "python3",
            str(PACKAGE / "repair-block.py"),
            "run",
            "--session-dir",
            str(session),
            "--artifact-dir",
            str(artifacts),
            "--branch",
            "claim",
            cwd=repo,
        ).returncode
        == 0
    )
    assert not (artifacts / "feature-outcome.json").exists()


def test_invalid_attempt_start_writes_nothing(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "attempt-start.json").write_text("invalid json")
    session = artifacts / "session"
    session.mkdir()
    (session / "audit.log").write_text(blocked())
    done = run(
        "python3",
        str(PACKAGE / "repair-block.py"),
        "run",
        "--session-dir",
        str(session),
        "--artifact-dir",
        str(artifacts),
        "--branch",
        "claim",
        cwd=repo,
    )
    assert done.returncode == 0
    assert "repair-block:" in done.stderr and "Expecting value" in done.stderr
    assert not (artifacts / "feature-outcome.json").exists()
    assert sorted(path.name for path in artifacts.iterdir()) == ["attempt-start.json", "session"]
