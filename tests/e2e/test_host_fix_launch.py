"""E2E-001: a real host launch through the installed Agent Runner, no model calls."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, launch_supervisor
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from agent_factory.work_kinds.pull_request.kinds import FIX

TOKEN = "dummy-fix-token"
CONTRACT = "factory-fix/1"
SKIP = "Required separate host check: install agent-runner with --session-dir; not passing evidence"

# Records what the launched process sees, then writes the structured outcome.
RECORD_WORKFLOW = """# factory-contract: factory-fix/1
name: factory-fix
description: "Model-free stand-in that records what the host launch exposes."
hidden: true

params:
  - name: issue_file
    required: true
  - name: branch_name
    required: true
  - name: contract_version
    required: true
  - name: artifact_dir
    required: false
    default: /artifacts

steps:
  - id: record
    command: |
      env | cut -d= -f1 | sort > "{{artifact_dir}}/env-names.txt"
      git config --get user.name > "{{artifact_dir}}/git-user.txt"
      git config --get credential.helper > "{{artifact_dir}}/git-helper.txt" || true
      git config --get-regexp '^url\\.' > "{{artifact_dir}}/git-url.txt" || true
      git config --get http.extraHeader > "{{artifact_dir}}/git-header.txt" || true
      printf '%s\\n' "$PWD" > "{{artifact_dir}}/cwd.txt"
      printf '%s\\n' "$HOME" > "{{artifact_dir}}/home.txt"
      cp "{{issue_file}}" "{{artifact_dir}}/issue-copy.json"
      printf '%s\\n' "{{branch_name}}" > "{{artifact_dir}}/branch.txt"
      printf '{"contract":"factory-fix/1","outcome":"failed","reasons":["model-free host test workflow"],"validator":{"status":"skipped"}}' > "{{artifact_dir}}/fix-outcome.json"
"""

# Blocks after recording the PIDs of the step and a grandchild it spawns.
BLOCK_WORKFLOW = """# factory-contract: factory-fix/1
name: factory-fix
description: "Model-free stand-in that blocks so the supervisor has something to cancel."
hidden: true

params:
  - name: issue_file
    required: true
  - name: branch_name
    required: true
  - name: contract_version
    required: true
  - name: artifact_dir
    required: false
    default: /artifacts

steps:
  - id: block
    command: |
      sleep 300 &
      echo $! > "{{artifact_dir}}/grandchild.pid"
      echo $$ > "{{artifact_dir}}/step.pid"
      wait
"""

# A user-level workflow of the same name that must lose to the clone's project scope.
PLANTED_USER_WORKFLOW = RECORD_WORKFLOW.replace(
    'env | cut -d= -f1 | sort > "{{artifact_dir}}/env-names.txt"',
    'touch "{{artifact_dir}}/planted-user-workflow-ran"',
)

PLANTED_GITCONFIG = """[user]
\tname = Operator Real Name
\temail = operator@example.invalid
[credential]
\thelper = osxkeychain
[url "git@github.com:"]
\tinsteadOf = https://github.com/
[http]
\textraHeader = Authorization: Basic planted
"""

GH_STUB = """#!/bin/sh
case "$1 $2" in
  "api user") echo fixbot ;;
  "auth status") [ -n "${GH_TOKEN:-}" ] && exit 0 || exit 1 ;;
  *) exit 1 ;;
esac
"""


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _installed_runner() -> str | None:
    found = shutil.which("agent-runner")
    if found is None:
        return None
    done = subprocess.run([found, "run", "--help"], capture_output=True, text=True, check=False)
    return found if "--session-dir" in done.stdout + done.stderr else None


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class HostFixture:
    """A temporary HOME, a target clone, a fix credential, and the recording stubs."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workflow: str) -> None:
        runner = _installed_runner()
        if runner is None:
            pytest.skip(SKIP)
        self.home = tmp_path / "home"
        (self.home / ".agent-runner" / "workflows").mkdir(parents=True)
        (self.home / ".agent-runner" / "settings.yaml").write_text(
            "theme: light\nautonomous_backend: headless\nautonomous_permission_mode: yolo\n"
            "setup:\n    completed_at: 2026-05-24T13:56:56Z\n"
            "splash:\n    dismissed: 2026-05-26T02:25:41Z\n"
            "onboarding:\n    dismissed: 2026-05-29T02:17:10Z\n"
        )
        (self.home / ".agent-runner" / "workflows" / launch.WORKFLOW_FILE).write_text(
            PLANTED_USER_WORKFLOW
        )
        (self.home / ".gitconfig").write_text(PLANTED_GITCONFIG)
        self.before = _snapshot(self.home)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(GH_STUB)
        (bin_dir / "gh").chmod(0o755)
        self.docker_log = tmp_path / "docker-calls.log"
        (bin_dir / "docker").write_text(f'#!/bin/sh\necho "$@" >> "{self.docker_log}"\nexit 1\n')
        (bin_dir / "docker").chmod(0o755)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        self.clone = tmp_path / "clones" / "claim" / "0" / "repo"
        self.clone.mkdir(parents=True)
        _git(self.clone, "init", "-q", "-b", "main")
        (self.clone / "README.md").write_text("fixture\n")
        _git(self.clone, "add", "README.md")
        _git(
            self.clone,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.state = tmp_path / "state.sqlite3"
        self.evidence_root = tmp_path / "evidence"
        with closing(ClaimStore(self.state)) as store:
            claim = store.create_claim(
                ClaimDraft(
                    "example/work",
                    1,
                    "I1",
                    "P1",
                    "fix",
                    "fp",
                    {"revisions": {"runner": "a" * 40, "skills": "b" * 40}},
                )
            )
            run = store.reserve_run(
                claim.id, "fix", reason="initial", evidence_path=str(self.evidence_root)
            )
        self.run_id = run.id
        self.evidence = self.evidence_root / "attempt-1"
        self.evidence.mkdir(parents=True)
        launch.write_issue_input(
            self.evidence, {"repository": "example/work", "number": 1, "title": "t", "body": "b"}
        )
        self.private = tmp_path / "storage" / "private" / run.id
        self.private.mkdir(parents=True, mode=0o700)
        credential = self.private / "fix.env"
        credential.write_text(f"GH_TOKEN={TOKEN}\n")
        credential.chmod(0o600)
        self.plan = launch.build_host_plan(
            evidence=self.evidence,
            repo_clone=self.clone,
            credential_copy=credential,
            roles={"lead": "codex:m:high", "implementor": "codex:m:high", "tester": "codex:m:high"},
            branch=f"factory/fix-1-{claim.id[:8]}",
            contract=CONTRACT,
            recorded_revisions={"runner": "a" * 40, "skills": "b" * 40},
        )
        # The stand-in takes the packaged workflow's place in the clone's project catalog.
        (self.clone / launch.PROJECT_WORKFLOWS / launch.WORKFLOW_FILE).write_text(workflow)
        self.runner = runner

    def launch(self, limits: SupervisionLimits) -> subprocess.Popen[bytes]:
        return launch_supervisor(self.state, self.run_id, self.plan, limits)

    def home_unchanged(self) -> None:
        assert _snapshot(self.home) == self.before
        assert not (self.home / ".agent-runner" / "projects").exists()


def _wait_file(path: Path, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert path.exists(), path


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout
    return out.strip().replace("Z", "") != ""


@pytest.mark.darwin
def test_e2e_001_host_launch_runs_the_installed_runner_without_touching_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = HostFixture(tmp_path, monkeypatch, RECORD_WORKFLOW)
    watcher = fixture.launch(SupervisionLimits(120, 300, 300))
    try:
        watcher.wait(timeout=300)
    finally:
        if watcher.poll() is None:
            watcher.kill()
    evidence = fixture.evidence
    log = (
        (evidence / "logs" / "agent-runner.log").read_text(errors="replace")[-3000:]
        if (evidence / "logs" / "agent-runner.log").exists()
        else ""
    )
    assert (evidence / "fix-outcome.json").exists(), log
    assert json.loads((evidence / "fix-outcome.json").read_text())["outcome"] == "failed"
    session = evidence / "agent-runner-session"
    assert session.is_dir(), log
    assert any(session.iterdir()), "the Runner wrote nothing into the supplied session directory"
    assert not (evidence / "planted-user-workflow-ran").exists(), (
        "user-level workflow shadowed the clone's"
    )
    names = (evidence / "env-names.txt").read_text().split()
    assert "GH_TOKEN" in names and "GITHUB_TOKEN" in names
    assert "AGENT_RUNNER_NO_TUI" in names and "GIT_CONFIG_GLOBAL" in names
    assert (evidence / "git-user.txt").read_text().strip() == "fixbot"
    assert (evidence / "git-helper.txt").read_text().strip() == ""
    assert (evidence / "git-url.txt").read_text().strip() == ""
    assert (evidence / "git-header.txt").read_text().strip() == ""
    assert Path((evidence / "cwd.txt").read_text().strip()).resolve() == fixture.clone.resolve()
    assert (evidence / "home.txt").read_text().strip() == str(fixture.home)
    assert json.loads((evidence / "issue-copy.json").read_text())["repository"] == "example/work"
    fixture.home_unchanged()
    assert not fixture.docker_log.exists()
    # Nothing outside the private directory carries the token.
    for path in evidence.rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(errors="replace"), path
    with closing(ClaimStore(fixture.state)) as store:
        finished = store.get_run(fixture.run_id)
        assert finished is not None
        assert finished.status in {"interrupted", "completed"}, finished.result
        assert "container" not in finished.progress
        assert TOKEN not in json.dumps(finished.plan)
        shared = SharedConfig.from_toml(Path("config/codagent.toml").read_text())
        local = LocalConfig.from_toml(
            f'shared_config = "x"\nstorage_root = "{tmp_path / "storage"}"\n'
            f'[repositories]\nagent_evals = "{tmp_path}"\nagent_runner = "{tmp_path}"\nagent_skills = "{tmp_path}"\n'
            '[schedule]\ntimezone = "UTC"\npoll_seconds = 60\nstart_hour = 0\nstop_hour = 15\n'
            "[limits]\nminimum_free_gib = 0\ninactivity_seconds = 1\nexecution_seconds = 1\ntotal_seconds = 1\ncodex_reset_fallback_seconds = 1\n"
            f'[credentials]\ngithub_app_key = "{tmp_path}"\nsuite_environment = "{tmp_path}"\n'
        )
        result = PullRequestHandler(FIX, shared, local).read_result(finished)
    assert result.product_verdict == "failed"
    assert result.result["sandbox"] == "host"
    assert result.result["runner_executable"] == os.path.abspath(fixture.runner)
    assert result.result["runner_version"]
    assert result.result["session_dir"] == str(session)
    provenance = json.loads((evidence / launch.HOST_PROVENANCE_FILE).read_text())
    assert provenance["recorded_revisions_executed"] is False


@pytest.mark.darwin
def test_e2e_001_cancelling_a_host_attempt_stops_the_runner_and_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = HostFixture(tmp_path, monkeypatch, BLOCK_WORKFLOW)
    assert (fixture.evidence / launch.HOST_PROVENANCE_FILE).exists(), (
        "provenance must exist before launch"
    )
    watcher = fixture.launch(SupervisionLimits(120, 300, 300))
    try:
        _wait_file(fixture.evidence / "grandchild.pid")
        step = int((fixture.evidence / "step.pid").read_text())
        grandchild = int((fixture.evidence / "grandchild.pid").read_text())
        with closing(ClaimStore(fixture.state)) as store:
            running = store.get_run(fixture.run_id)
            assert running is not None
            pid_value = running.process["pid"]
            assert isinstance(pid_value, int)
            runner_pid = pid_value  # the wrapper process that owns the Runner
            assert _pid_alive(runner_pid)
            store.request_cancellation(fixture.run_id)
        watcher.wait(timeout=60)
        deadline = time.monotonic() + 5
        while (
            any(_pid_alive(pid) for pid in (runner_pid, step, grandchild))
            and time.monotonic() < deadline
        ):
            time.sleep(0.05)
        assert not _pid_alive(runner_pid), "Runner survived cancellation"
        assert not _pid_alive(step), "workflow step survived cancellation"
        assert not _pid_alive(grandchild), "agent-like grandchild survived cancellation"
        with closing(ClaimStore(fixture.state)) as store:
            finished = store.get_run(fixture.run_id)
        assert finished is not None and finished.status == "cancelled", finished
        assert (fixture.evidence / launch.HOST_PROVENANCE_FILE).exists()
        assert not (fixture.evidence / "fix-outcome.json").exists()
        assert not fixture.docker_log.exists()
        fixture.home_unchanged()
    finally:
        if watcher.poll() is None:
            watcher.kill()
        subprocess.run([sys.executable, "-c", "pass"], check=False)
