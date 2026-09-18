"""Builds the sandbox invocation for one fix attempt and the inputs it reads."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from collections.abc import Mapping
from importlib.resources import as_file, files
from pathlib import Path

from agent_factory.config import LocalConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.suites.and_scene import ReadinessError

WORKFLOW_NAME = "factory-fix"
WORKFLOW_FILE = "factory-fix-v1.0.yaml"
WORKFLOW_SCRIPTS = ("record-triage.sh", "read-regression-marker.sh", "record-outcome.sh")
REVIEW_WORKFLOW_FILE = "factory-review-v1.0.yaml"
IMPLEMENT_WORKFLOW_FILE = "factory-implement-v1.0.yaml"
REVIEW_WORKFLOW_SCRIPTS = ("record-review-triage.sh", "record-review-outcome.sh")
# The Runner finds user-level workflows under $HOME/.agent-runner/workflows; the sandbox
# links $HOME/.agent-runner to /artifacts/agent-runner, so staging under the evidence
# directory publishes the workflow without another mount.
STAGED_WORKFLOWS = Path("agent-runner") / "workflows"
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
    destination.touch(mode=0o600, exist_ok=True)
    destination.chmod(0o600)
    destination.write_text(f"GH_TOKEN={match.group(1)}\n", encoding="utf-8")
    return destination


def write_input(evidence: Path, filename: str, payload: Mapping[str, object]) -> Path:
    path = evidence / "input" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_issue_input(evidence: Path, payload: Mapping[str, object]) -> Path:
    return write_input(evidence, "issue.json", payload)


def write_review_input(evidence: Path, payload: Mapping[str, object]) -> Path:
    return write_input(evidence, "review.json", payload)


def contract_marker(contract: str) -> str:
    return f"# factory-contract: {contract}"


def packaged_workflow_text(contract: str) -> str:
    """The fix workflow shipped with this package; it must declare ``contract`` first."""
    filename = REVIEW_WORKFLOW_FILE if contract == "factory-review/1" else WORKFLOW_FILE
    resource = files("agent_factory.work_kinds.fix") / "workflow" / filename
    try:
        text = resource.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReadinessError(f"the packaged fix workflow cannot be read: {error}") from error
    first_line = text.splitlines()[0].strip() if text.strip() else ""
    if first_line != contract_marker(contract):
        raise ReadinessError(f"the packaged fix workflow does not declare {contract!r}")
    return text


def stage_workflow(evidence: Path, contract: str) -> Path:
    """Copy the packaged workflow and its scripts where the sandboxed Runner looks them up."""
    packaged_workflow_text(contract)
    destination = evidence / STAGED_WORKFLOWS
    destination.mkdir(parents=True, exist_ok=True)
    package = files("agent_factory.work_kinds.fix") / "workflow"
    names = (WORKFLOW_FILE, REVIEW_WORKFLOW_FILE, IMPLEMENT_WORKFLOW_FILE,
             *WORKFLOW_SCRIPTS, *REVIEW_WORKFLOW_SCRIPTS)
    for name in names:
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
    packaged_workflow_text(contract)
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
                profiles, branch=branch, contract=contract, bootstrap_skills=bootstrap_skills
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
) -> str:
    """The bash body run inside the sandbox after the Runner build.

    ``bootstrap_skills`` is switched off only by model-free launch tests: installing the
    Skills clone into each CLI needs that CLI's authentication, which those tests lack.
    """
    adapters = (
        sorted({cli for cli, _model, _effort in profiles.values()}) if bootstrap_skills else []
    )
    config_lines = ["active_profile: factory", "profiles:", "  factory:", "    agents:"]
    for role, (cli, model, effort) in profiles.items():
        config_lines.extend(
            (
                f"      {role}:",
                "        default_mode: autonomous",
                f"        cli: {cli}",
                f"        model: {model}",
                f"        effort: {effort}",
            )
        )
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
    review = contract == "factory-review/1"
    run_command = " ".join(
        (
            f"agent-runner run {'factory-review' if review else WORKFLOW_NAME}",
            f"--param {'review_file=/artifacts/input/review.json' if review else 'issue_file=/artifacts/input/issue.json'}",
            f"--param branch_name={shlex.quote(branch)}",
            f"--param contract_version={shlex.quote(contract)}",
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
        "cat > /workspace/repo/.agent-runner/config.yaml <<'PROFILES'",
        *config_lines,
        "PROFILES",
        "printf '%s\\n' /.agent-runner/config.yaml >> /workspace/repo/.git/info/exclude",
        "cd /workspace/repo",
        f"{run_command} 2>&1 | tee /artifacts/logs/agent-runner.log",
    ]
    return "\n".join(lines) + "\n"
