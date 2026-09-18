"""INT-001: the host plan stages into the clone, holds paths only, and leaves HOME alone."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from agent_factory.suites.and_scene import ReadinessError
from agent_factory.supervisor import _plan_document  # pyright: ignore[reportPrivateUsage]
from agent_factory.work_kinds.fix import launch

TOKEN = "dummy-fix-token"
CONTRACT = "factory-fix/1"
RUNNER_STUB = """#!/bin/sh
case "$1" in
  -version) echo "stub-runner 1.2.3" ;;
  run) echo "Usage: agent-runner run <workflow> [--session-dir <path>] [--param key=value]" ;;
  *) exit 1 ;;
esac
"""
ROLES = {"lead": "cursor:m:high", "implementor": "claude:m:high", "tester": "codex:m:low"}


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _clone(tmp_path: Path) -> Path:
    clone = tmp_path / "clones" / "claim" / "0" / "repo"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q", "-b", "main")
    (clone / "README.md").write_text("fixture\n")
    _git(clone, "add", "README.md")
    _git(clone, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "i")
    return clone


class Built:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        monkeypatch.setenv("HOME", str(self.home))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self.runner = bin_dir / "agent-runner"
        self.runner.write_text(RUNNER_STUB)
        self.runner.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        self.clone = _clone(tmp_path)
        self.evidence = tmp_path / "storage" / "artifacts" / "claim" / "attempt-1"
        self.evidence.mkdir(parents=True)
        launch.write_issue_input(self.evidence, {"repository": "example/work", "number": 7})
        self.private = tmp_path / "storage" / "private" / "run-1"
        self.private.mkdir(parents=True, mode=0o700)
        self.credential = self.private / "fix.env"
        self.credential.write_text(f"GH_TOKEN={TOKEN}\n")
        self.credential.chmod(0o600)
        self.plan = launch.build_host_plan(
            evidence=self.evidence,
            repo_clone=self.clone,
            credential_copy=self.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
            recorded_revisions={"runner": "a" * 40, "skills": "b" * 40},
        )
        self.wrapper = self.private / "host-run.sh"


def test_host_plan_document_and_wrapper_hold_paths_never_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    document = json.dumps(_plan_document(built.plan))
    wrapper = built.wrapper.read_text()
    assert TOKEN not in document
    assert TOKEN not in wrapper
    assert TOKEN not in " ".join(built.plan.argv)
    assert str(built.credential) in document
    assert str(built.credential) in wrapper
    assert built.plan.argv == ("/bin/bash", str(built.wrapper))
    assert built.plan.allowed_environment == {}
    assert built.plan.credential_files == (str(built.credential),)
    assert built.plan.working_directory == str(built.clone.resolve())
    # Nothing outside private/ carries the token either.
    for path in (tmp_path / "storage" / "artifacts").rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(errors="replace"), path


def test_host_wrapper_execs_the_resolved_runner_with_session_and_artifact_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    wrapper = built.wrapper.read_text()
    evidence = built.evidence.resolve()
    exec_line = next(
        line
        for line in wrapper.splitlines()
        if line.startswith(str(built.runner.resolve())) and " run " in line
    )
    assert exec_line.startswith(f"{built.runner.resolve()} run factory-fix ")
    assert f"--session-dir {evidence / 'agent-runner-session'}" in exec_line
    assert f"--param artifact_dir={evidence}" in exec_line
    assert f"--param issue_file={evidence / 'input' / 'issue.json'}" in exec_line
    assert "--param branch_name=factory/fix-7-claim" in exec_line
    assert "--param contract_version=factory-fix/1" in exec_line
    assert "AGENT_RUNNER_NO_TUI=1" in wrapper
    assert "GIT_TERMINAL_PROMPT=0" in wrapper
    assert "GIT_CONFIG_NOSYSTEM=1" in wrapper
    assert f"export GIT_CONFIG_GLOBAL={built.private / 'gitconfig'}" in wrapper
    assert f"export GIT_ASKPASS={built.private / 'askpass.sh'}" in wrapper
    assert f"cd {built.clone.resolve()}" in wrapper
    # The container-only steps are absent: no HOME redirect, settings write, or bootstrap.
    for absent in ("ln -s", "settings.yaml", "marketplace", "plugin install", "docker"):
        assert absent not in wrapper, absent


def test_host_plan_private_files_are_owner_only_and_git_is_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    modes = {
        name: stat.S_IMODE((built.private / name).stat().st_mode)
        for name in ("host-run.sh", "askpass.sh", "gitconfig", "fix.env")
    }
    assert modes == {
        "host-run.sh": 0o700,
        "askpass.sh": 0o700,
        "gitconfig": 0o600,
        "fix.env": 0o600,
    }
    assert stat.S_IMODE(built.private.stat().st_mode) == 0o700
    gitconfig = built.private / "gitconfig"
    env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
    }

    def config(key: str) -> str:
        done = subprocess.run(
            ["git", "config", "--global", "--get-all", key],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        return done.stdout.strip()

    assert config("credential.helper") == ""
    assert config("core.askPass") == str(built.private / "askpass.sh")
    assert config("user.name") == "agent-factory"
    askpass = built.private / "askpass.sh"
    answer = subprocess.run(
        [str(askpass), "Password for 'https://github.com':"],
        capture_output=True,
        text=True,
        env={"GH_TOKEN": TOKEN, "PATH": os.environ["PATH"]},
        check=True,
    )
    assert answer.stdout.strip() == TOKEN
    user = subprocess.run(
        [str(askpass), "Username for 'https://github.com':"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"]},
        check=True,
    )
    assert user.stdout.strip() == "x-access-token"


def test_host_plan_stages_the_workflow_only_in_the_clone_and_excludes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    catalog = built.clone / ".agent-runner" / "workflows"
    assert (catalog / launch.WORKFLOW_FILE).read_text() == launch.packaged_workflow_text(CONTRACT)
    for name in launch.WORKFLOW_SCRIPTS:
        assert os.access(catalog / name, os.X_OK), name
    config = (built.clone / ".agent-runner" / "config.yaml").read_text()
    assert "\n  factory:\n    agents:\n" in config
    assert "active_profile" not in config
    assert "--profile factory" in built.wrapper.read_text()
    assert "cli: cursor" in config and "cli: claude" in config and "cli: codex" in config
    exclude = (built.clone / ".git" / "info" / "exclude").read_text().splitlines()
    assert "/.agent-runner/workflows/" in exclude
    assert "/.agent-runner/config.yaml" in exclude
    assert _git(built.clone, "status", "--porcelain") == ""
    # Staging again is idempotent and does not duplicate exclude entries.
    launch.build_host_plan(
        evidence=built.evidence,
        repo_clone=built.clone,
        credential_copy=built.credential,
        roles=ROLES,
        branch="factory/fix-7-claim",
        contract=CONTRACT,
    )
    assert (built.clone / ".git" / "info" / "exclude").read_text().splitlines() == exclude
    assert not (built.home / ".agent-runner").exists()
    assert not (built.evidence / launch.STAGED_WORKFLOWS).exists()
    assert list(built.home.iterdir()) == []


def test_host_plan_hints_progress_and_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    evidence = built.evidence.resolve()
    session = evidence / "agent-runner-session"
    hints = dict(built.plan.ownership_hints)
    assert hints == {
        "artifact_path": str(evidence),
        "sandbox": "host",
        "branch_name": "factory/fix-7-claim",
        "runner_executable": str(built.runner.resolve()),
        "runner_version": "stub-runner 1.2.3",
        "session_dir": str(session),
    }
    assert "image_tag" not in hints
    progress = built.plan.progress_sources
    assert str(evidence / "factory-suite.log") in progress
    assert str(evidence / "logs" / "agent-runner.log") in progress
    for pattern in (
        f"glob:{session}/state.json",
        f"glob:{session}/audit.log",
        f"glob:{session}/output/*",
    ):
        assert pattern in progress
    assert any("agent-session-state/cursor" in p for p in progress)
    assert any("agent-session-state/claude" in p for p in progress)
    provenance = json.loads((evidence / launch.HOST_PROVENANCE_FILE).read_text())
    assert provenance["execution"] == "host"
    assert provenance["runner_executable"] == str(built.runner.resolve())
    assert provenance["runner_version"] == "stub-runner 1.2.3"
    assert provenance["session_dir"] == str(session)
    assert provenance["recorded_revisions"] == {"runner": "a" * 40, "skills": "b" * 40}
    assert provenance["recorded_revisions_executed"] is False
    assert "not the versions that executed" in provenance["note"]


def test_host_plan_refuses_without_an_installed_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    credential = tmp_path / "private" / "run" / "fix.env"
    credential.parent.mkdir(parents=True)
    credential.write_text(f"GH_TOKEN={TOKEN}\n")
    with pytest.raises(ReadinessError, match="agent-runner is not on PATH"):
        launch.build_host_plan(
            evidence=tmp_path / "evidence",
            repo_clone=clone,
            credential_copy=credential,
            roles=ROLES,
            branch="b",
            contract=CONTRACT,
        )


def test_host_wrapper_exports_the_token_as_data_not_shell_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    marker = tmp_path / "injected"
    hostile = f"abc$(touch {marker})`touch {marker}`;touch {marker}"
    built.credential.write_text(f"GH_TOKEN={hostile}\n")
    seen = tmp_path / "seen-token.txt"
    # A stand-in Runner that records the token it was handed instead of running anything.
    built.runner.write_text(f"#!/bin/sh\nprintf '%s' \"$GH_TOKEN\" > {seen}\n")
    done = subprocess.run(
        ["/bin/bash", str(built.wrapper)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    assert seen.read_text() == hostile
    assert not marker.exists(), "token contents were executed by the wrapper"


def test_host_plan_keeps_the_tree_clean_when_the_target_tracks_its_runner_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targets such as Codagent-AI/agent-runner commit their own .agent-runner/config.yaml.
    The launcher's profile config must not surface as a modified tracked file: the packaged
    workflow refuses to triage on a dirty tree, and finalize-pr must never commit it."""
    built = Built(tmp_path, monkeypatch)
    config = built.clone / ".agent-runner" / "config.yaml"
    config.write_text("active_profile: theirs\nprofiles:\n  theirs:\n    agents: {}\n")
    _git(built.clone, "add", "-f", ".agent-runner/config.yaml")
    _git(
        built.clone,
        "-c",
        "user.name=T",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-qm",
        "track",
    )
    assert _git(built.clone, "status", "--porcelain") == ""
    launch.build_host_plan(
        evidence=built.evidence,
        repo_clone=built.clone,
        credential_copy=built.credential,
        roles=ROLES,
        branch="factory/fix-7-claim",
        contract=CONTRACT,
    )
    staged = config.read_text()
    assert "\n  theirs:\n" in staged and "\n  factory:\n" in staged
    assert _git(built.clone, "status", "--porcelain") == ""
    _git(built.clone, "add", "-A")
    assert _git(built.clone, "diff", "--cached", "--name-only") == ""
    # After the attempt the wrapper puts the tracked file back and clears the bit.
    built.runner.write_text("#!/bin/sh\nexit 0\n")
    subprocess.run(["/bin/bash", str(built.wrapper)], capture_output=True, check=True)
    assert config.read_text() == "active_profile: theirs\nprofiles:\n  theirs:\n    agents: {}\n"
    assert not any(
        line.startswith("S ") for line in _git(built.clone, "ls-files", "-v").splitlines()
    )


def test_container_script_hides_a_tracked_runner_config_from_git() -> None:
    script = launch.container_script(
        {"lead": ("codex", "m", "high")}, branch="b", contract=CONTRACT, bootstrap_skills=False
    )
    assert "update-index --skip-worktree .agent-runner/config.yaml" in script
    assert "update-index --no-skip-worktree .agent-runner/config.yaml" in script
    assert "trap restore_tracked_config EXIT" in script


@pytest.mark.parametrize("linked", [".agent-runner", ".agent-runner/workflows", "config"])
def test_host_plan_refuses_staging_through_a_symlink_the_target_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    """A target repository controls the clone's contents; a committed symlink must never
    make host planning write outside the disposable clone as the operator."""
    built = Built(tmp_path, monkeypatch)
    shutil.rmtree(built.clone / ".agent-runner")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "config.yaml"
    victim.write_text("keep\n")
    if linked == "config":
        (built.clone / ".agent-runner").mkdir()
        (built.clone / ".agent-runner" / "config.yaml").symlink_to(victim)
    else:
        link = built.clone / linked
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReadinessError, match="symlink"):
        launch.build_host_plan(
            evidence=built.evidence,
            repo_clone=built.clone,
            credential_copy=built.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
        )
    assert sorted(path.name for path in outside.iterdir()) == ["config.yaml"]
    assert victim.read_text() == "keep\n"


@pytest.mark.parametrize("name", [launch.WORKFLOW_FILE, *launch.WORKFLOW_SCRIPTS])
def test_host_plan_refuses_to_overwrite_a_workflow_file_the_target_tracks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Staging over a tracked catalog file would dirty the clone and fail the workflow's
    clean-tree gate, so planning refuses before copying anything."""
    built = Built(tmp_path, monkeypatch)
    shutil.rmtree(built.clone / ".agent-runner")
    catalog = built.clone / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    (catalog / name).write_text("theirs\n")
    _git(built.clone, "add", "-f", f".agent-runner/workflows/{name}")
    _git(
        built.clone, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "t"
    )
    with pytest.raises(ReadinessError, match=name):
        launch.build_host_plan(
            evidence=built.evidence,
            repo_clone=built.clone,
            credential_copy=built.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
        )
    assert (catalog / name).read_text() == "theirs\n"
    assert _git(built.clone, "status", "--porcelain") == ""


def test_host_plan_reports_a_failed_skip_worktree_update_as_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    config = built.clone / ".agent-runner" / "config.yaml"
    config.write_text("active_profile: theirs\n")
    _git(built.clone, "add", "-f", ".agent-runner/config.yaml")
    _git(
        built.clone, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "t"
    )
    (built.clone / ".git" / "index.lock").write_text("")
    with pytest.raises(ReadinessError, match="skip-worktree"):
        launch.build_host_plan(
            evidence=built.evidence,
            repo_clone=built.clone,
            credential_copy=built.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
        )
    # The launch never starts, so the token copy must not stay on disk until Done.
    assert not built.credential.exists()


def test_host_plan_failure_keeps_its_cause_when_the_token_copy_cannot_be_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    shutil.rmtree(built.clone / ".agent-runner")
    (built.clone / ".agent-runner").symlink_to(tmp_path, target_is_directory=True)

    def refuse_unlink(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    with pytest.raises(ReadinessError, match="symlink"):
        launch.build_host_plan(
            evidence=built.evidence,
            repo_clone=built.clone,
            credential_copy=built.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
        )


_PROFILES = {"lead": ("cursor", "m", "high")}
_FACTORY_SET = (
    "  factory:\n"
    "    agents:\n"
    "      lead:\n"
    "        default_mode: autonomous\n"
    "        cli: cursor\n"
    "        model: m\n"
    "        effort: high\n"
)


def test_staged_config_without_a_tracked_file_holds_only_the_factory_set() -> None:
    assert launch.staged_config_text(None, _PROFILES) == "profiles:\n" + _FACTORY_SET


def test_staged_config_keeps_a_tracked_file_and_adds_the_factory_set_at_its_indent() -> None:
    tracked = (
        "# Runner profiles for this repository\n"
        "profiles:\n"
        "    smoke_test:\n"
        "        extends: default\n"
        "        agents: {}\n"
        "\n"
        "other: value"
    )
    staged = launch.staged_config_text(tracked, _PROFILES)
    assert staged == (
        "# Runner profiles for this repository\n"
        "profiles:\n"
        "    smoke_test:\n"
        "        extends: default\n"
        "        agents: {}\n"
        "    factory:\n"
        "        agents:\n"
        "            lead:\n"
        "                default_mode: autonomous\n"
        "                cli: cursor\n"
        "                model: m\n"
        "                effort: high\n"
        "\n"
        "other: value\n"
    )


def test_staged_config_adds_a_profiles_block_when_the_tracked_file_has_none() -> None:
    staged = launch.staged_config_text("active_profile: theirs\n", _PROFILES)
    assert staged == "active_profile: theirs\nprofiles:\n" + _FACTORY_SET


@pytest.mark.parametrize(
    "tracked",
    [
        "profiles: {}\n",
        "profiles:\n  factory:\n    agents: {}\n",
    ],
)
def test_staged_config_refuses_a_tracked_file_it_cannot_merge(tracked: str) -> None:
    with pytest.raises(ReadinessError, match="config.yaml"):
        launch.staged_config_text(tracked, _PROFILES)


def _track_runner_config(clone: Path, text: str) -> Path:
    config = clone / ".agent-runner" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(text)
    _git(clone, "add", "-f", ".agent-runner/config.yaml")
    _git(clone, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "t")
    return config


_THEIRS = "profiles:\n  smoke_test:\n    extends: default\n    agents: {}\n"


def test_host_plan_keeps_the_targets_profile_sets_and_selects_the_factory_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codagent-AI/agent-runner's own tests run `--profile smoke_test` against its tracked
    config; replacing that file made every host fix attempt fail its validator."""
    built = Built(tmp_path, monkeypatch)
    config = _track_runner_config(built.clone, _THEIRS)
    for _ in range(2):  # re-planning must merge into the committed file, not the staged one
        launch.build_host_plan(
            evidence=built.evidence,
            repo_clone=built.clone,
            credential_copy=built.credential,
            roles=ROLES,
            branch="factory/fix-7-claim",
            contract=CONTRACT,
        )
    staged = config.read_text()
    assert staged.startswith(_THEIRS)
    assert staged.count("\n  factory:\n") == 1
    assert "--profile factory" in built.wrapper.read_text()


def test_docker_plan_keeps_the_targets_profile_sets_and_selects_the_factory_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = Built(tmp_path, monkeypatch)
    _track_runner_config(built.clone, _THEIRS)
    plan = launch.build_plan(
        run_id="run-1",
        evidence=built.evidence,
        clones={"repo": str(built.clone), "runner": str(tmp_path), "skills": str(tmp_path)},
        credential_copy=built.credential,
        roles=ROLES,
        branch="factory/fix-7-claim",
        contract=CONTRACT,
        bootstrap_skills=False,
    )
    script = plan.argv[-1]
    assert "--profile factory" in script
    staged = launch.staged_config_text(_THEIRS, launch.role_profiles(ROLES))
    assert shlex.quote(staged) in script


def test_staged_config_refuses_a_tracked_file_whose_whole_document_is_indented() -> None:
    """Top-level keys then start past column 0; appending a column-0 `profiles` block would
    add a second mapping instead of merging into the first."""
    with pytest.raises(ReadinessError, match="config.yaml"):
        launch.staged_config_text("  profiles:\n    theirs:\n      agents: {}\n", _PROFILES)


def test_tracked_config_text_distinguishes_an_untracked_config_from_a_git_failure(
    tmp_path: Path,
) -> None:
    clone = _clone(tmp_path)
    assert launch.tracked_config_text(clone) is None
    _track_runner_config(clone, _THEIRS)
    assert launch.tracked_config_text(clone) == _THEIRS
    not_a_repository = tmp_path / "not-a-repository"
    not_a_repository.mkdir()
    with pytest.raises(ReadinessError, match="config.yaml"):
        launch.tracked_config_text(not_a_repository)


def test_staged_config_adds_a_profiles_block_to_a_tracked_file_holding_only_comments() -> None:
    staged = launch.staged_config_text("# Runner profiles live elsewhere\n", _PROFILES)
    assert staged == "# Runner profiles live elsewhere\nprofiles:\n" + _FACTORY_SET
