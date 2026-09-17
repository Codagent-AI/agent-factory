"""Builds the sandbox or host invocation for one fix attempt and the inputs it reads."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from importlib.resources import as_file, files
from pathlib import Path

from agent_factory.config import LocalConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.suites.and_scene import ReadinessError

WORKFLOW_NAME = "factory-fix"
WORKFLOW_FILE = "factory-fix-v1.0.yaml"
WORKFLOW_SCRIPTS = ("record-triage.sh", "record-outcome.sh")
# The Runner finds user-level workflows under $HOME/.agent-runner/workflows; the sandbox
# links $HOME/.agent-runner to /artifacts/agent-runner, so staging under the evidence
# directory publishes the workflow without another mount.
STAGED_WORKFLOWS = Path("agent-runner") / "workflows"
# On the host the Runner runs from the attempt's target clone and consults the clone's own
# project-scope catalog first, so staging there publishes the workflow without touching
# the operator's ~/.agent-runner and removes it together with the clone.
PROJECT_WORKFLOWS = Path(".agent-runner") / "workflows"
PROJECT_CONFIG = Path(".agent-runner") / "config.yaml"
# The profile set the factory adds to the project config and selects with --profile.
FACTORY_PROFILE = "factory"
ARTIFACT_DIR_PARAM = "artifact_dir"
CONTAINER_ARTIFACTS = "/artifacts"
SESSION_DIR_NAME = "agent-runner-session"
HOST_PROVENANCE_FILE = "host-provenance.json"
HOST_NOTE = (
    "This attempt ran on the host with the operator's installed Agent Runner and Skills "
    "plugin; the claim's recorded Runner and Skills commits were not the versions that "
    "executed."
)
FINALIZE_PR_PATH = "workflows/core/finalize-pr-v1.0.yaml"
FINALIZE_PR_PARAM = "ci_fix_cycles"
IMAGE_PREFIX = "agent-runner-factory"
_TOKEN_LINE = re.compile(r"^GH_TOKEN=(.+)$")
_AUTH_FLAGS = {
    "claude": "--mount-claude-auth",
    "codex": "--mount-codex-auth",
    "cursor": "--mount-cursor-auth",
}
_PROFILE = re.compile(r"^([a-z]+):([^:]*):([^:]*)$")


def image_tag(run_id: str) -> str:
    return f"{IMAGE_PREFIX}:{run_id}"


def branch_name(issue_number: int, claim_id: str) -> str:
    return f"factory/fix-{issue_number}-{claim_id[:8]}"


def validated_credential_copy(local: LocalConfig, destination: Path) -> Path:
    """Copy the single `GH_TOKEN=` line so the sandbox can forward nothing else."""
    source = local.credentials.fix_environment
    if source is None:
        raise ReadinessError("credentials.fix_environment is not configured")
    try:
        lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"cannot read the fix credential file: {error}") from error
    match = _TOKEN_LINE.match(lines[0].strip()) if len(lines) == 1 else None
    if match is None or not match.group(1):
        raise ReadinessError("the fix credential file must contain exactly one GH_TOKEN= line")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    return _private_file(destination, f"GH_TOKEN={match.group(1)}\n", 0o600)


def _private_file(path: Path, text: str, mode: int) -> Path:
    path.touch(mode=mode, exist_ok=True)
    path.chmod(mode)
    path.write_text(text, encoding="utf-8")
    return path


def write_issue_input(evidence: Path, payload: Mapping[str, object]) -> Path:
    path = evidence / "input" / "issue.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def contract_marker(contract: str) -> str:
    return f"# factory-contract: {contract}"


def packaged_workflow_text(contract: str) -> str:
    """The fix workflow shipped with this package; it must declare ``contract`` first."""
    resource = files("agent_factory.work_kinds.fix") / "workflow" / WORKFLOW_FILE
    try:
        text = resource.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"the packaged fix workflow cannot be read: {error}") from error
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    if first_line != contract_marker(contract):
        raise ReadinessError(f"the packaged fix workflow does not declare {contract!r}")
    return text


_ARTIFACT_DIR_DECLARATION = re.compile(
    r"^\s*-\s*name:\s*" + ARTIFACT_DIR_PARAM + r"\s*$\n(?:^\s+(?!-)\S.*$\n)*?"
    r"^\s+default:\s*" + re.escape(CONTAINER_ARTIFACTS) + r"\s*$",
    re.MULTILINE,
)
_ARTIFACT_DIR_DEFAULT_LINE = re.compile(
    r"^\s*default:\s*" + re.escape(CONTAINER_ARTIFACTS) + r"\s*$"
)


def check_packaged_workflow(contract: str) -> str:
    """The packaged workflow must declare the contract and take its artifact directory as a
    parameter: one workflow serves the sandbox (``/artifacts``) and the host (the attempt's
    evidence directory), so a hardcoded container path would break host attempts."""
    text = packaged_workflow_text(contract)
    if _ARTIFACT_DIR_DECLARATION.search(text) is None:
        raise ReadinessError(
            f"the packaged fix workflow does not declare the {ARTIFACT_DIR_PARAM} parameter "
            f"with default {CONTAINER_ARTIFACTS}"
        )
    stray = [
        str(number)
        for number, line in enumerate(text.splitlines(), start=1)
        if CONTAINER_ARTIFACTS in _YAML_COMMENT.sub("", line)
        and _ARTIFACT_DIR_DEFAULT_LINE.match(line) is None
    ]
    if stray:
        raise ReadinessError(
            f"the packaged fix workflow hardcodes {CONTAINER_ARTIFACTS} on line(s) "
            f"{', '.join(stray)} instead of using the {ARTIFACT_DIR_PARAM} parameter"
        )
    return text


def stage_workflow(evidence: Path, contract: str) -> Path:
    """Copy the packaged workflow and its scripts where the sandboxed Runner looks them up."""
    return stage_workflow_into(evidence / STAGED_WORKFLOWS, contract)


def stage_workflow_into(destination: Path, contract: str) -> Path:
    """Copy the packaged workflow and its scripts into a Runner workflow catalog directory."""
    packaged_workflow_text(contract)
    destination.mkdir(parents=True, exist_ok=True)
    package = files("agent_factory.work_kinds.fix") / "workflow"
    for name in (WORKFLOW_FILE, *WORKFLOW_SCRIPTS):
        with as_file(package / name) as source:
            target = destination / name
            shutil.copyfile(source, target)
            target.chmod(0o755 if name.endswith(".sh") else 0o644)
    return destination


_YAML_COMMENT = re.compile(r"(^|\s)#.*$")
_PARAM_NAME = re.compile(
    r"(?<![\w-])name\s*:\s*[\"']?" + re.escape(FINALIZE_PR_PARAM) + r"[\"']?(?=[\s,}\]]|$)"
)


def finalize_pr_accepts_fix_cycles(text: str) -> bool:
    """Whether a Runner ``finalize-pr`` definition declares the parameter the workflow passes.

    The factory has no YAML parser, so this isolates the top-level ``params`` block after
    dropping comments (whole-line and trailing) and any document-level indentation, then
    accepts the parameter in block form (``- name: ci_fix_cycles``), inline-map form, or
    flow-sequence form.
    """
    lines = [
        stripped
        for stripped in (_YAML_COMMENT.sub("", line).rstrip() for line in text.splitlines())
        if stripped.strip()
    ]
    if not lines:
        return False
    indent = min(len(line) - len(line.lstrip()) for line in lines)
    lines = [line[indent:] for line in lines]
    block: list[str] = []
    for line in lines:
        if not line.startswith((" ", "\t")):
            if block:
                break
            if line.startswith("params:"):
                block.append(line)
            continue
        if block:
            block.append(line)
    return any(_PARAM_NAME.search(line) for line in block)


def check_target_catalog(repo_clone: Path) -> None:
    """The target repository must not shadow the staged workflow with its own ``factory-fix``.

    The sandboxed Runner consults the project's ``.agent-runner/workflows`` before the
    staged user-level catalog, so a target that ships a workflow of the same logical name
    would run instead of the packaged one, with this attempt's credential.
    """
    catalog = repo_clone / ".agent-runner" / "workflows"
    shadows = sorted(
        path.name
        for path in catalog.glob(f"{WORKFLOW_NAME}-v*")
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    )
    if shadows:
        raise ReadinessError(
            f"the target repository's .agent-runner/workflows would shadow the packaged "
            f"{WORKFLOW_NAME} workflow: {', '.join(shadows)}"
        )


def check_runner_contract(runner_clone: Path, contract: str) -> None:
    """The packaged workflow must declare the contract and the Runner clone must support it."""
    check_packaged_workflow(contract)
    finalize = runner_clone / FINALIZE_PR_PATH
    try:
        text = finalize.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReadinessError(
            f"{FINALIZE_PR_PATH} is missing from the recorded Runner commit: {error}"
        ) from error
    if not finalize_pr_accepts_fix_cycles(text):
        raise ReadinessError(
            f"{FINALIZE_PR_PATH} at the recorded Runner commit does not accept "
            f"{FINALIZE_PR_PARAM}; the recorded Runner commit is incompatible with {contract}"
        )
    launcher = runner_clone / "scripts" / "sandbox-run.sh"
    try:
        text = launcher.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"scripts/sandbox-run.sh is unavailable: {error}") from error
    for flag in ("--no-default-secrets", "--env-file", "--docker-run-arg", "--image"):
        if flag not in text:
            raise ReadinessError(
                f"scripts/sandbox-run.sh at the recorded Runner commit lacks {flag}; "
                "the recorded Runner commit is incompatible with the fix contract"
            )


def role_profiles(roles: Mapping[str, object]) -> dict[str, tuple[str, str, str]]:
    profiles: dict[str, tuple[str, str, str]] = {}
    for role in ("lead", "implementor", "tester"):
        value = roles.get(role)
        match = _PROFILE.match(value) if isinstance(value, str) else None
        if match is None:
            raise ReadinessError(f"fix role {role} is not a cli:model:effort profile")
        profiles[role] = (match.group(1), match.group(2), match.group(3))
    return profiles


def build_plan(
    *,
    run_id: str,
    evidence: Path,
    clones: Mapping[str, str],
    credential_copy: Path,
    roles: Mapping[str, object],
    branch: str,
    contract: str,
    bootstrap_skills: bool = True,
) -> ExecutionPlan:
    profiles = role_profiles(roles)
    runner = Path(clones["runner"])
    tag = image_tag(run_id)
    argv: list[str] = [
        str(runner / "scripts" / "sandbox-run.sh"),
        "--image",
        tag,
        "--artifact-dir",
        str(evidence),
        "--no-default-secrets",
        "--env-file",
        str(credential_copy),
    ]
    for flag in sorted({_auth_flag(cli) for cli, _model, _effort in profiles.values()}):
        argv.append(flag)
    argv.extend(
        (
            "--docker-run-arg",
            "--mount",
            "--docker-run-arg",
            f"type=bind,source={clones['repo']},target=/workspace/repo",
            "--docker-run-arg",
            "--mount",
            "--docker-run-arg",
            f"type=bind,source={clones['skills']},target=/workspace/skills,readonly",
            "--",
            container_script(
                profiles,
                branch=branch,
                contract=contract,
                bootstrap_skills=bootstrap_skills,
                config_text=staged_config_text(tracked_config_text(Path(clones["repo"])), profiles),
            ),
        )
    )
    progress = tuple(
        str(evidence / name) for name in ("factory-suite.log", "logs/agent-runner.log")
    ) + (
        f"glob:{evidence}/agent-runner/projects/*/runs/*/state.json",
        f"glob:{evidence}/agent-runner/projects/*/runs/*/audit.log",
        f"glob:{evidence}/agent-runner/projects/*/runs/*/output/*",
        f"glob:{evidence}/.runtime/agent-session-state/cursor/chats/*/*/store.db*",
        f"glob:{evidence}/.runtime/agent-session-state/claude/projects/*/*.jsonl",
    )
    return ExecutionPlan(
        tuple(argv),
        str(runner),
        {},  # The sandbox loads --env-file itself; the credential never enters the plan.
        (str(credential_copy),),
        progress,
        {
            "artifact_path": str(evidence),
            "image_tag": tag,
            "sandbox": "docker",
            "branch_name": branch,
        },
        False,
    )


def _auth_flag(cli: str) -> str:
    flag = _AUTH_FLAGS.get(cli)
    if flag is None:
        raise ReadinessError(f"fix roles select an unsupported CLI adapter: {cli}")
    return flag


def container_script(
    profiles: Mapping[str, tuple[str, str, str]],
    *,
    branch: str,
    contract: str,
    bootstrap_skills: bool = True,
    config_text: str | None = None,
) -> str:
    """The bash body run inside the sandbox after the Runner build.

    ``config_text`` is the project Runner config to stage (see ``staged_config_text``);
    without it the script stages the factory profile set alone.

    ``bootstrap_skills`` is switched off only by model-free launch tests: installing the
    Skills clone into each CLI needs that CLI's authentication, which those tests lack.
    """
    adapters = (
        sorted({cli for cli, _model, _effort in profiles.values()}) if bootstrap_skills else []
    )
    staged_config = config_text if config_text is not None else staged_config_text(None, profiles)
    bootstrap: list[str] = []
    for adapter in adapters:
        if adapter == "claude":
            bootstrap.extend(
                (
                    "claude plugin marketplace remove codagent >/dev/null 2>&1 || true",
                    "claude plugin marketplace add /workspace/skills",
                    "claude plugin install codagent@codagent",
                )
            )
        elif adapter == "codex":
            bootstrap.extend(
                (
                    "codex plugin marketplace add /workspace/skills --json",
                    "codex plugin add codagent@codagent --json",
                )
            )
        elif adapter == "cursor":
            bootstrap.append("cursor plugins install /workspace/skills")
    run_command = " ".join(
        (
            f"agent-runner run {WORKFLOW_NAME}",
            f"--profile {FACTORY_PROFILE}",
            "--param issue_file=/artifacts/input/issue.json",
            f"--param branch_name={shlex.quote(branch)}",
            f"--param contract_version={shlex.quote(contract)}",
            f"--param {ARTIFACT_DIR_PARAM}={CONTAINER_ARTIFACTS}",
        )
    )
    lines = [
        "set -euo pipefail",
        "mkdir -p /artifacts/logs /artifacts/agent-runner",
        "echo 'factory-fix: preparing sandbox' | tee -a /artifacts/factory-suite.log",
        'rm -rf "$HOME/.agent-runner"',
        'ln -s /artifacts/agent-runner "$HOME/.agent-runner"',
        'printf \'%s\\n\' "autonomous_backend: headless" "autonomous_permission_mode: yolo"'
        ' > "$HOME/.agent-runner/settings.yaml"',
        "export AGENT_RUNNER_NO_TUI=1",
        'if [ -z "${GH_TOKEN:-}" ]; then echo "GH_TOKEN is not set" >&2; exit 2; fi',
        "cat > \"$HOME/.git-askpass\" <<'ASKPASS'",
        "#!/usr/bin/env sh",
        'case "$1" in',
        "  *sername*) printf '%s\\n' x-access-token ;;",
        "  *assword*) printf '%s\\n' \"${GH_TOKEN:-}\" ;;",
        "  *) printf '\\n' ;;",
        "esac",
        "ASKPASS",
        'chmod 700 "$HOME/.git-askpass"',
        'export GIT_ASKPASS="$HOME/.git-askpass" GIT_TERMINAL_PROMPT=0',
        'login="$(gh api user -q .login 2>/dev/null || printf agent-factory)"',
        'git config --global user.name "$login"',
        'git config --global user.email "${login}@users.noreply.github.com"',
        *bootstrap,
        "mkdir -p /workspace/repo/.agent-runner",
        f"printf '%s' {shlex.quote(staged_config)} > /workspace/repo/.agent-runner/config.yaml",
        "printf '%s\\n' /.agent-runner/config.yaml >> /workspace/repo/.git/info/exclude",
        "cd /workspace/repo",
        # A target that tracks its own Runner config must not see ours as a modification.
        "if git ls-files --error-unmatch .agent-runner/config.yaml >/dev/null 2>&1; then "
        "git update-index --skip-worktree .agent-runner/config.yaml; fi",
        *_RESTORE_TRACKED_CONFIG_LINES,
        f"{run_command} 2>&1 | tee /artifacts/logs/agent-runner.log",
    ]
    return "\n".join(lines) + "\n"


def staged_config_text(tracked: str | None, profiles: Mapping[str, tuple[str, str, str]]) -> str:
    """The project Runner config a fix attempt runs with.

    A target may commit its own ``.agent-runner/config.yaml`` whose profile sets its own
    tests select (Codagent-AI/agent-runner's smoke tests run ``--profile smoke_test``).
    That file is kept byte for byte and the factory profile set is added to its
    ``profiles`` block, at the block's own indentation; the workflow then selects the
    set with ``--profile``. The factory has no YAML parser, so a tracked file it cannot
    extend safely is refused rather than guessed at.
    """
    if tracked is None or not tracked.strip():
        return "profiles:\n" + _factory_profile_block(profiles, "  ")
    lines = tracked.splitlines()
    first_content = next(
        (line for line in lines if line.strip() and not line.lstrip().startswith("#")), None
    )
    if first_content is not None and first_content[0].isspace():
        raise ReadinessError(
            f"the target's {PROJECT_CONFIG.as_posix()} indents its top-level keys; "
            "the factory cannot add its profile set to it"
        )
    header = next((i for i, line in enumerate(lines) if _PROFILES_KEY.match(line)), None)
    if header is None:
        return "\n".join(lines) + "\nprofiles:\n" + _factory_profile_block(profiles, "  ")
    if not _PROFILES_BLOCK_HEADER.match(lines[header]):
        raise ReadinessError(
            f"the target's {PROJECT_CONFIG.as_posix()} writes profiles in flow style; "
            "the factory cannot add its profile set to it"
        )
    unit: str | None = None
    last_content = header
    for index in range(header + 1, len(lines)):
        line = lines[index]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            break
        indent = line[: len(line) - len(stripped)]
        if unit is None:
            unit = indent
        if indent == unit and _FACTORY_PROFILE_KEY.match(stripped):
            raise ReadinessError(
                f"the target's {PROJECT_CONFIG.as_posix()} already defines a "
                f"{FACTORY_PROFILE!r} profile set"
            )
        last_content = index
    block = _factory_profile_block(profiles, unit or "  ").rstrip("\n").split("\n")
    merged = [*lines[: last_content + 1], *block, *lines[last_content + 1 :]]
    return "\n".join(merged) + "\n"


_PROFILES_KEY = re.compile(r"^profiles\s*:")
_PROFILES_BLOCK_HEADER = re.compile(r"^profiles\s*:\s*(#.*)?$")
_FACTORY_PROFILE_KEY = re.compile(rf"^{re.escape(FACTORY_PROFILE)}\s*:")


def _factory_profile_block(profiles: Mapping[str, tuple[str, str, str]], unit: str) -> str:
    lines = [f"{unit}{FACTORY_PROFILE}:", f"{unit * 2}agents:"]
    for role, (cli, model, effort) in profiles.items():
        lines.extend(
            (
                f"{unit * 3}{role}:",
                f"{unit * 4}default_mode: autonomous",
                f"{unit * 4}cli: {cli}",
                f"{unit * 4}model: {model}",
                f"{unit * 4}effort: {effort}",
            )
        )
    return "\n".join(lines) + "\n"


def tracked_config_text(repo_clone: Path) -> str | None:
    """The target's committed Runner config, read from HEAD so re-planning an attempt never
    merges into a file an earlier plan already staged. ``None`` means HEAD has no such
    file; any git failure is raised, never mistaken for an untracked config."""
    path = PROJECT_CONFIG.as_posix()
    listed = _git_output(repo_clone, "ls-tree", "--name-only", "HEAD", "--", path)
    if not listed.strip():
        return None
    try:
        return _git_output(repo_clone, "show", f"HEAD:{path}")
    except UnicodeDecodeError as error:
        raise ReadinessError(f"the target's {path} is not valid UTF-8: {error}") from error


def _git_output(repo_clone: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_clone), *args], capture_output=True, check=False
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise ReadinessError(
            f"could not read the target's {PROJECT_CONFIG.as_posix()} from HEAD in "
            f"{repo_clone}: {detail or f'git exited {completed.returncode}'}"
        )
    return completed.stdout.decode("utf-8")


# -- host execution ---------------------------------------------------------------------

_ASKPASS_SCRIPT = """#!/usr/bin/env sh
# Answers git's credential prompts for one factory fix attempt from the process environment.
case "$1" in
  *sername*) printf '%s\\n' x-access-token ;;
  *assword*) printf '%s\\n' "${GH_TOKEN:-}" ;;
  *) printf '\\n' ;;
esac
"""


def resolve_runner_executable(executable: str | None = None) -> str:
    """The installed Agent Runner a host attempt runs, as an absolute path on the factory's PATH."""
    found = executable or shutil.which("agent-runner")
    if found is None:
        raise ReadinessError(
            "agent-runner is not on PATH; host execution needs the installed Runner"
        )
    return os.path.abspath(found)


def runner_version(executable: str) -> str:
    """What the installed Runner reports for ``-version``; failures are recorded, not raised."""
    try:
        completed = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, check=False, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unknown ({error})"
    text = (completed.stdout or completed.stderr).strip()
    return text or "unknown"


def gitconfig_text(askpass: Path) -> str:
    """A complete global git configuration for the launched process.

    Selected through ``GIT_CONFIG_GLOBAL`` (with ``GIT_CONFIG_NOSYSTEM=1``), it replaces the
    operator's ``~/.gitconfig`` entirely, so no inherited credential helper, ``insteadOf``
    rewrite, extra header, or signing setting reaches the attempt. The identity lines are
    filled in by the wrapper once it knows the fix credential's login.
    """
    return (
        "\n".join(
            (
                "# Written by agent-factory for one host fix attempt; never the operator's file.",
                "[user]",
                "\tname = agent-factory",
                "\temail = agent-factory@users.noreply.github.com",
                "[credential]",
                "\thelper =",
                "[core]",
                f"\taskPass = {askpass}",
                "[http]",
                "\textraHeader =",
            )
        )
        + "\n"
    )


def host_script(
    *,
    runner: str,
    repo_clone: Path,
    evidence: Path,
    credential_copy: Path,
    gitconfig: Path,
    askpass: Path,
    branch: str,
    contract: str,
) -> str:
    """The bash wrapper that is the host plan's argv target.

    It reads the private credential copy only at exec time, as data rather than shell
    code, so the token appears in the process environment of the Runner and its agents
    but never in the persisted plan, the wrapper text, or the factory's logs.
    """
    session_dir = evidence / SESSION_DIR_NAME
    run_command = " ".join(
        (
            f"{shlex.quote(runner)} run {WORKFLOW_NAME}",
            f"--profile {FACTORY_PROFILE}",
            f"--session-dir {shlex.quote(str(session_dir))}",
            f"--param issue_file={shlex.quote(str(evidence / 'input' / 'issue.json'))}",
            f"--param branch_name={shlex.quote(branch)}",
            f"--param contract_version={shlex.quote(contract)}",
            f"--param {ARTIFACT_DIR_PARAM}={shlex.quote(str(evidence))}",
        )
    )
    lines = [
        "#!/bin/bash",
        "# Written by agent-factory for one host fix attempt.",
        "set -euo pipefail",
        f"mkdir -p {shlex.quote(str(evidence / 'logs'))}",
        f"exec > >(tee -a {shlex.quote(str(evidence / 'logs' / 'agent-runner.log'))}) 2>&1",
        f"echo 'factory-fix: launching on the host' | tee -a "
        f"{shlex.quote(str(evidence / 'factory-suite.log'))}",
        # The credential copy is read as data, never sourced: a token value is exported
        # literally even if it contains shell syntax.
        f"IFS= read -r credential_line < {shlex.quote(str(credential_copy))} || true",
        'case "$credential_line" in',
        "  GH_TOKEN=?*) ;;",
        '  *) echo "the credential copy has no GH_TOKEN line" >&2; exit 2 ;;',
        "esac",
        'export GH_TOKEN="${credential_line#GH_TOKEN=}"',
        "unset credential_line",
        'export GITHUB_TOKEN="$GH_TOKEN"',
        f"export GIT_CONFIG_GLOBAL={shlex.quote(str(gitconfig))}",
        "export GIT_CONFIG_NOSYSTEM=1",
        f"export GIT_ASKPASS={shlex.quote(str(askpass))}",
        "export GIT_TERMINAL_PROMPT=0",
        "export AGENT_RUNNER_NO_TUI=1",
        'login="$(gh api user -q .login 2>/dev/null || printf agent-factory)"',
        'git config --file "$GIT_CONFIG_GLOBAL" user.name "$login"',
        'git config --file "$GIT_CONFIG_GLOBAL" user.email "${login}@users.noreply.github.com"',
        f"cd {shlex.quote(str(repo_clone))}",
        *_RESTORE_TRACKED_CONFIG_LINES,
        run_command,
    ]
    return "\n".join(lines) + "\n"


# When the target tracks its own Runner config, the launcher hid its overwrite with the
# skip-worktree bit so the workflow's clean-tree gate passes and finalize-pr never commits
# it. The bit must not outlive the attempt: on exit the tree is put back the way the target
# committed it, so nothing stays hidden from a later inspection of the clone.
_RESTORE_TRACKED_CONFIG_LINES = (
    "restore_tracked_config() {",
    "  if git ls-files --error-unmatch .agent-runner/config.yaml >/dev/null 2>&1; then",
    "    git update-index --no-skip-worktree .agent-runner/config.yaml 2>/dev/null || true",
    "    git checkout -- .agent-runner/config.yaml 2>/dev/null || true",
    "  fi",
    "}",
    "trap restore_tracked_config EXIT",
)


def _hide_tracked_file_from_git(repo_clone: Path, relative: str) -> None:
    """A target that commits its own Runner config (Codagent-AI/agent-runner does) would
    otherwise show the factory's profile config as a modified tracked file: the packaged
    workflow refuses to triage on a dirty tree and finalize-pr must never commit it. The
    skip-worktree bit keeps the file out of status and staging without touching HEAD."""
    tracked = subprocess.run(
        ["git", "-C", str(repo_clone), "ls-files", "--error-unmatch", relative],
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0:
        hidden = subprocess.run(
            ["git", "-C", str(repo_clone), "update-index", "--skip-worktree", relative],
            capture_output=True,
            text=True,
            check=False,
        )
        if hidden.returncode != 0:
            raise ReadinessError(
                f"could not set skip-worktree on {relative} in {repo_clone}: "
                f"{hidden.stderr.strip() or f'git exited {hidden.returncode}'}"
            )


def _refuse_symlinked_staging(repo_clone: Path) -> None:
    """The target repository controls the clone's contents, so a committed symlink at a
    staging path would make host planning write outside the clone as the operator."""
    workflows = PROJECT_WORKFLOWS
    candidates = (
        workflows.parent,
        workflows,
        PROJECT_CONFIG,
        *(workflows / name for name in (WORKFLOW_FILE, *WORKFLOW_SCRIPTS)),
    )
    for relative in candidates:
        if (repo_clone / relative).is_symlink():
            raise ReadinessError(
                f"the target repository has a symlink at {relative.as_posix()}; "
                "the factory will not stage its workflow or profile config through it"
            )


def _refuse_tracked_workflow_files(repo_clone: Path) -> None:
    """Staging over a catalog file the target commits would leave a modified tracked file,
    which the workflow's clean-tree gate rejects and finalize-pr could commit."""
    names = [(PROJECT_WORKFLOWS / name).as_posix() for name in (WORKFLOW_FILE, *WORKFLOW_SCRIPTS)]
    listed = subprocess.run(
        ["git", "-C", str(repo_clone), "ls-files", "--", *names],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        raise ReadinessError(
            f"could not list tracked workflow files in {repo_clone}: "
            f"{listed.stderr.strip() or f'git exited {listed.returncode}'}"
        )
    tracked = listed.stdout.split()
    if tracked:
        raise ReadinessError(
            f"the target repository tracks {', '.join(tracked)}; "
            "the factory will not stage its workflow over a committed file"
        )


def _exclude_from_git(repo_clone: Path, entries: tuple[str, ...]) -> None:
    exclude = repo_clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    missing = [entry for entry in entries if entry not in existing]
    if missing:
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{entry}\n" for entry in missing))


def write_host_provenance(
    evidence: Path,
    *,
    runner: str,
    version: str,
    recorded_revisions: Mapping[str, object] | None = None,
) -> Path:
    """Record at plan time what will execute, so the file exists however the attempt ends."""
    payload: dict[str, object] = {
        "execution": "host",
        "runner_executable": runner,
        "runner_version": version,
        "session_dir": str(evidence / SESSION_DIR_NAME),
        "recorded_revisions": dict(recorded_revisions or {}),
        "recorded_revisions_executed": False,
        "note": HOST_NOTE,
    }
    path = evidence / HOST_PROVENANCE_FILE
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_host_plan(
    *,
    evidence: Path,
    repo_clone: Path,
    credential_copy: Path,
    roles: Mapping[str, object],
    branch: str,
    contract: str,
    recorded_revisions: Mapping[str, object] | None = None,
    runner_executable: str | None = None,
) -> ExecutionPlan:
    """Assemble the host launch: workflow and profiles in the clone, secrets and wrapper in
    the attempt's private directory, and a plan document that holds only paths.

    A launch that fails to plan never starts, so its token copy is deleted before the
    error propagates rather than left on disk until the claim's cleanup."""
    try:
        return _assemble_host_plan(
            evidence=evidence,
            repo_clone=repo_clone,
            credential_copy=credential_copy,
            roles=roles,
            branch=branch,
            contract=contract,
            recorded_revisions=recorded_revisions,
            runner_executable=runner_executable,
        )
    except BaseException:
        # Best effort: the claim's cleanup retries the removal, and a failure here must not
        # replace the planning error that explains why the launch never started.
        with contextlib.suppress(OSError):
            credential_copy.unlink(missing_ok=True)
        raise


def _assemble_host_plan(
    *,
    evidence: Path,
    repo_clone: Path,
    credential_copy: Path,
    roles: Mapping[str, object],
    branch: str,
    contract: str,
    recorded_revisions: Mapping[str, object] | None,
    runner_executable: str | None,
) -> ExecutionPlan:
    profiles = role_profiles(roles)
    runner = resolve_runner_executable(runner_executable)
    version = runner_version(runner)
    evidence = evidence.resolve()
    repo_clone = repo_clone.resolve()
    (evidence / "logs").mkdir(parents=True, exist_ok=True)
    _refuse_symlinked_staging(repo_clone)
    _refuse_tracked_workflow_files(repo_clone)
    stage_workflow_into(repo_clone / PROJECT_WORKFLOWS, contract)
    config_path = repo_clone / PROJECT_CONFIG
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        staged_config_text(tracked_config_text(repo_clone), profiles), encoding="utf-8"
    )
    _exclude_from_git(
        repo_clone, (f"/{PROJECT_CONFIG.as_posix()}", f"/{PROJECT_WORKFLOWS.as_posix()}/")
    )
    private = credential_copy.parent
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    askpass = _private_file(private / "askpass.sh", _ASKPASS_SCRIPT, 0o700)
    gitconfig = _private_file(private / "gitconfig", gitconfig_text(askpass), 0o600)
    wrapper = _private_file(
        private / "host-run.sh",
        host_script(
            runner=runner,
            repo_clone=repo_clone,
            evidence=evidence,
            credential_copy=credential_copy,
            gitconfig=gitconfig,
            askpass=askpass,
            branch=branch,
            contract=contract,
        ),
        0o700,
    )
    # Set last among the clone changes so a failure writing the private files above never
    # leaves the bit set without the wrapper whose exit trap clears it.
    _hide_tracked_file_from_git(repo_clone, PROJECT_CONFIG.as_posix())
    write_host_provenance(
        evidence, runner=runner, version=version, recorded_revisions=recorded_revisions
    )
    session_dir = evidence / SESSION_DIR_NAME
    progress = tuple(
        str(evidence / name) for name in ("factory-suite.log", "logs/agent-runner.log")
    ) + (
        f"glob:{session_dir}/state.json",
        f"glob:{session_dir}/audit.log",
        f"glob:{session_dir}/output/*",
        f"glob:{evidence}/.runtime/agent-session-state/cursor/chats/*/*/store.db*",
        f"glob:{evidence}/.runtime/agent-session-state/claude/projects/*/*.jsonl",
    )
    return ExecutionPlan(
        ("/bin/bash", str(wrapper)),
        str(repo_clone),
        {},  # The wrapper reads the credential itself; the token never enters the plan.
        (str(credential_copy),),
        progress,
        {
            "artifact_path": str(evidence),
            "sandbox": "host",
            "branch_name": branch,
            "runner_executable": runner,
            "runner_version": version,
            "session_dir": str(session_dir),
        },
        False,
    )
