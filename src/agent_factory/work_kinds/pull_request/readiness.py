"""Fail-closed checks that must pass before any fix attempt launches."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.operations import (
    ADAPTER_EXECUTABLES,
    HOST_BASE_EXECUTABLES,
    Diagnostic,
    DiagnosticGroup,
    free_space,
    read_fix_token,
)
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import FIX, PullRequestKind

_TOKEN_LINE = re.compile(r"^GH_TOKEN=(.+)$")
_PROBE_TIMEOUT = 15


def check_readiness(
    local: LocalConfig,
    shared: SharedConfig,
    *,
    installation_token: str | None = None,
    docker_diagnostic: Diagnostic | None = None,
    definition: PullRequestKind = FIX,
) -> list[Diagnostic]:
    """Diagnostics gating fix admission for the configured execution mode only."""
    if definition.kind == "feature" and shared.feature is None:
        return []
    if not definition.targets(shared):
        if definition.kind == "feature":
            return [
                Diagnostic(
                    "feature targets",
                    False,
                    "feature work has no configured fix target repositories",
                    "Configure at least one [[fix.targets]] entry.",
                    group="feature-host",
                )
            ]
        return []
    mode = definition.local(local).execution
    group = definition.doctor_groups[mode]
    diagnostics: list[Diagnostic] = []
    if mode == "host":
        diagnostics.extend(_host_diagnostics(local, shared, definition))
    else:
        diagnostics.append(_launch_diagnostic(local, shared, docker_diagnostic=docker_diagnostic))
    floor = definition.local(local).minimum_free_gib
    diagnostics.append(
        free_space(
            local,
            floor_gib=floor if floor is not None else local.limits.minimum_free_gib,
            group=group,
        )
    )
    diagnostics.append(_credential_diagnostic(local, installation_token, group=group))
    diagnostics.append(_contract_diagnostic(local, shared, definition))
    if definition.kind == "feature":
        diagnostics.extend(_openspec_diagnostics(local, shared))
    if definition is not FIX:
        diagnostics = [
            dataclasses.replace(
                d,
                name=d.name.replace(FIX.kind, definition.kind).replace(FIX.noun, definition.noun),
                group=group if d.group in FIX.doctor_groups.values() else d.group,
            )
            for d in diagnostics
        ]
    return diagnostics


def _openspec_diagnostics(local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for target in shared.fix.targets:
        clone = local.repositories.working_clones.get(target.repository)
        if clone is not None and not (clone / "openspec").is_dir():
            diagnostics.append(
                Diagnostic(
                    f"feature target {target.repository} OpenSpec",
                    True,
                    f"{target.repository} has no openspec/ in its working clone; "
                    "feature preflight will request initialization (informational)",
                    "",
                    group="feature-host",
                )
            )
    return diagnostics


def _launch_diagnostic(
    local: LocalConfig, shared: SharedConfig, *, docker_diagnostic: Diagnostic | None = None
) -> Diagnostic:
    """The Runner branch head must ship a sandbox launcher that can contain the credential."""
    name = "fix sandbox launch"
    action = (
        "Update the configured Runner branch to a commit whose scripts/sandbox-run.sh supports "
        "--no-default-secrets, --env-file, --docker-run-arg, and --image."
    )
    from agent_factory import runtime

    try:
        sha = runtime._resolve_revision(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, shared.fix.branches.runner, fetch=False
        )
        text = runtime._git_show(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, sha, "scripts/sandbox-run.sh"
        )
    except ReadinessError as error:
        return Diagnostic(name, False, str(error), action, group="fix-sandbox")
    missing = [
        flag
        for flag in ("--no-default-secrets", "--env-file", "--docker-run-arg", "--image")
        if flag not in text
    ]
    if missing:
        return Diagnostic(
            name,
            False,
            f"scripts/sandbox-run.sh at {shared.fix.branches.runner}@{sha[:7]} lacks "
            + ", ".join(missing),
            action,
            group="fix-sandbox",
        )
    if shutil.which("docker") is None:
        return Diagnostic(
            name, False, "docker is not on PATH", "Install or start Docker.", group="fix-sandbox"
        )
    if docker_diagnostic is not None and not docker_diagnostic.available:
        return Diagnostic(
            name,
            False,
            f"Docker is not running: {docker_diagnostic.detail}",
            "Start Docker Desktop, then rerun doctor.",
            group="fix-sandbox",
        )
    return Diagnostic(
        name,
        True,
        f"sandbox launcher at {sha[:7]} supports contained launches",
        "",
        group="fix-sandbox",
    )


def _credential_diagnostic(
    local: LocalConfig, installation_token: str | None, *, group: DiagnosticGroup = "fix-sandbox"
) -> Diagnostic:
    name = "fix credential"
    action = (
        "Create a private file at credentials.fix_environment containing exactly one "
        "line: GH_TOKEN=<a token distinct from the App installation token>."
    )
    path = local.credentials.fix_environment
    if path is None:
        return Diagnostic(
            name, False, "credentials.fix_environment is not configured", action, group=group
        )
    try:
        mode = path.stat().st_mode
    except OSError as error:
        return Diagnostic(name, False, f"cannot read {path}: {error}", action, group=group)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return Diagnostic(
            name, False, f"{path} must not be group- or world-accessible", action, group=group
        )
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError) as error:
        return Diagnostic(name, False, f"cannot read {path}: {error}", action, group=group)
    if len(lines) != 1:
        return Diagnostic(
            name, False, f"{path} must contain exactly one assignment", action, group=group
        )
    match = _TOKEN_LINE.match(lines[0].strip())
    if match is None:
        found_key = lines[0].split("=", 1)[0].strip()
        return Diagnostic(
            name,
            False,
            f"{path} must assign GH_TOKEN, found variable: {found_key!r}",
            action,
            group=group,
        )
    token = match.group(1)
    if not token:
        return Diagnostic(name, False, f"{path} GH_TOKEN value is empty", action, group=group)
    if installation_token is not None and token == installation_token:
        return Diagnostic(
            name,
            False,
            f"{path} GH_TOKEN must not equal the App installation token",
            action,
            group=group,
        )
    return Diagnostic(
        name, True, f"fix credential is present and well-formed: {path}", "", group=group
    )


def _contract_diagnostic(
    local: LocalConfig, shared: SharedConfig, definition: PullRequestKind = FIX
) -> Diagnostic:
    """The packaged workflow declares the contract and, in Docker mode, the recorded Runner
    branch head can run it. Host admission never depends on the recorded Runner commit,
    because that commit does not execute on the host."""
    name = "fix workflow contract"
    group = definition.doctor_groups[definition.local(local).execution]
    # The review workflow ships beside the fix workflow and is checked the same way.
    for contract, filename in (
        (definition.contract(shared), definition.workflow_file),
        (launch.REVIEW_CONTRACT, launch.REVIEW_WORKFLOW_FILE),
    ):
        marker = launch.contract_marker(contract)
        try:
            launch.check_packaged_workflow(contract, definition)
        except ReadinessError as error:
            return Diagnostic(
                name,
                False,
                str(error),
                f"Reinstall the factory; its packaged {filename} must start with "
                f"{marker!r} and take its artifact directory as the "
                f"{launch.ARTIFACT_DIR_PARAM} parameter.",
                group=group,
            )
    if definition.local(local).execution == "host":
        return Diagnostic(
            name,
            True,
            f"workflow contracts {definition.contract(shared)} and {launch.REVIEW_CONTRACT} "
            "are packaged "
            f"with {launch.ARTIFACT_DIR_PARAM}",
            "",
            group=group,
        )
    action = (
        f"Update the configured Runner branch to a commit whose {launch.FINALIZE_PR_PATH} "
        f"declares the {launch.FINALIZE_PR_PARAM} parameter."
    )
    from agent_factory import runtime

    try:
        sha = runtime._resolve_revision(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, shared.fix.branches.runner
        )
        text = runtime._git_show(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, sha, launch.FINALIZE_PR_PATH
        )
    except ReadinessError as error:
        return Diagnostic(name, False, str(error), action, group=group)
    where = f"{shared.fix.branches.runner}@{sha[:7]}"
    if not launch.finalize_pr_accepts_fix_cycles(text):
        return Diagnostic(
            name,
            False,
            f"{launch.FINALIZE_PR_PATH} at {where} does not accept {launch.FINALIZE_PR_PARAM}",
            action,
            group=group,
        )
    return Diagnostic(
        name,
        True,
        f"workflow contract {definition.contract(shared)} is packaged and runnable at {where}",
        "",
        group=group,
    )


def _host_diagnostics(
    local: LocalConfig, shared: SharedConfig, definition: PullRequestKind = FIX
) -> list[Diagnostic]:
    """Everything host-mode fixes need: the installed Runner, host CLIs, and Runner settings."""
    diagnostics: list[Diagnostic] = []
    runner = shutil.which("agent-runner")
    if runner is None:
        diagnostics.append(_missing_executable("fix host agent-runner on PATH", "agent-runner"))
    else:
        diagnostics.append(_runner_version_diagnostic(runner))
        diagnostics.append(_session_dir_flag_diagnostic(runner))
        diagnostics.append(_validate_diagnostic(runner, shared, definition))
    for executable in HOST_BASE_EXECUTABLES:
        if executable != "agent-runner":
            diagnostics.append(_which_diagnostic(executable))
    diagnostics.append(_gh_auth_status_diagnostic(local))
    diagnostics.extend(
        _role_cli_diagnostic(adapter)
        for adapter in sorted(
            {
                str(profile).split(":", 1)[0]
                for profile in definition.defaults(shared).values()
                if profile
            }
        )
    )
    diagnostics.append(_runner_settings_diagnostic())
    return diagnostics


def _host_failure(name: str, detail: str, action: str) -> Diagnostic:
    return Diagnostic(name, False, detail, action, group="fix-host")


def _host_pass(name: str, detail: str) -> Diagnostic:
    return Diagnostic(name, True, detail, "", group="fix-host")


def _missing_executable(name: str, executable: str) -> Diagnostic:
    return _host_failure(
        name,
        f"{executable} is not on PATH",
        f"Install {executable} and put it on the service PATH.",
    )


def _probe(
    command: tuple[str, ...], *, env: Mapping[str, str] | None = None
) -> tuple[str, str | None]:
    """Run a read-only probe; return its combined output and, on failure, the failure text."""
    try:
        # The env keyword is passed only when set so test doubles of subprocess.run that
        # take the plain probe signature keep working.
        completed = (
            subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=_PROBE_TIMEOUT
            )
            if env is None
            else subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT,
                env=dict(env),
            )
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return "", f"{' '.join(command)} could not run: {error}"
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        return output, (completed.stderr or completed.stdout or "non-zero exit").strip()[:300]
    return output, None


def _which_diagnostic(executable: str) -> Diagnostic:
    name = f"fix host {executable} on PATH"
    found = shutil.which(executable)
    if found is None:
        return _missing_executable(name, executable)
    return _host_pass(name, f"{executable} resolves to {found}")


def _runner_version_diagnostic(runner: str) -> Diagnostic:
    name = "fix host agent-runner on PATH"
    output, failure = _probe((runner, "-version"))
    if failure is not None:
        return _host_failure(name, failure, "Repair the installed Agent Runner.")
    return _host_pass(name, f"{runner} reports version {output.strip() or 'unknown'}")


def _session_dir_flag_diagnostic(runner: str) -> Diagnostic:
    name = "fix host agent-runner --session-dir support"
    output, failure = _probe((runner, "run", "--help"))
    if failure is not None:
        return _host_failure(name, failure, "Repair the installed Agent Runner.")
    if "--session-dir" not in output:
        return _host_failure(
            name,
            "installed agent-runner run --help does not list --session-dir",
            "Update the installed Agent Runner to a build that supports --session-dir.",
        )
    return _host_pass(name, "installed agent-runner supports --session-dir")


def _validate_diagnostic(
    runner: str, shared: SharedConfig, definition: PullRequestKind = FIX
) -> Diagnostic:
    name = f"{definition.kind} host workflow validation"
    group = definition.doctor_groups["host"]
    workflow_files = [definition.workflow_file, launch.REVIEW_WORKFLOW_FILE]
    if definition.kind == "feature":
        workflow_files.append("factory-define-v1.0.yaml")
    with tempfile.TemporaryDirectory() as tmp:
        catalog = Path(tmp)
        try:
            launch.stage_workflow_into(catalog, definition.contract(shared), definition)
        except ReadinessError as error:
            return Diagnostic(name, False, str(error), "Reinstall the factory package.", group)
        for filename in workflow_files:
            _output, failure = _probe((runner, "-validate", str(catalog / filename)))
            if failure is not None:
                prerequisite = (
                    " (requires core/verify-change)" if definition.kind == "feature" else ""
                )
                return Diagnostic(
                    name,
                    False,
                    f"agent-runner -validate rejected packaged {filename}{prerequisite}: {failure}",
                    "Repair the packaged fix/review workflows or the installed Runner.",
                    group,
                )
    return Diagnostic(
        name, True, f"packaged {definition.kind} and review workflows validate", "", group
    )


def _gh_auth_status_diagnostic(local: LocalConfig) -> Diagnostic:
    name = "fix host gh auth status"
    if shutil.which("gh") is None:
        return _missing_executable(name, "gh")
    token = read_fix_token(local.credentials.fix_environment)
    if token is None:
        return _host_failure(
            name,
            "no GH_TOKEN found to check gh auth status against",
            "Configure credentials.fix_environment with a GH_TOKEN line.",
        )
    _output, failure = _probe(
        ("gh", "auth", "status"), env={"GH_TOKEN": token, "PATH": os.environ.get("PATH", "")}
    )
    if failure is not None:
        return _host_failure(
            name,
            "gh auth status failed with the configured fix credential",
            "Verify the fix credential token is valid.",
        )
    return _host_pass(name, "gh auth status succeeded with the fix credential")


# adapter -> (auth-status command, installed-plugin-listing command, expected plugin name,
# whether the listing command emits JSON). Each listing command reports the CLI's own
# installed plugins, not available marketplaces, so a match proves the plugin is installed.
_ADAPTER_CHECKS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, bool]] = {
    "claude": (("claude", "auth", "status"), ("claude", "plugin", "list"), "codagent", False),
    "codex": (
        ("codex", "login", "status"),
        ("codex", "plugin", "list", "--json"),
        "codagent",
        True,
    ),
    # Cursor's `agent` CLI has no installed-plugin listing; `agent plugin marketplace list`
    # names the marketplaces registered for the account, and the codagent marketplace is
    # what the Skills plugin is installed from.
    "cursor": (("agent", "status"), ("agent", "plugin", "marketplace", "list"), "codagent", False),
}


def _role_cli_diagnostic(adapter: str) -> Diagnostic:
    """One configured role CLI: installed, authenticated, and carrying the codagent plugin."""
    name = f"fix host {adapter} CLI"
    fail: Callable[[str, str], Diagnostic] = lambda detail, action: _host_failure(  # noqa: E731
        name, detail, action
    )
    executable = ADAPTER_EXECUTABLES.get(adapter)
    checks = _ADAPTER_CHECKS.get(adapter)
    if executable is None or checks is None:
        return fail(f"unsupported fix role CLI: {adapter}", "Use a supported CLI adapter.")
    if shutil.which(executable) is None:
        return fail(
            f"{executable} is not on PATH",
            f"Install and log in to {adapter} ({executable}) and put it on the service PATH.",
        )
    auth_command, plugin_command, plugin_name, plugin_is_json = checks
    _output, auth_failure = _probe(auth_command)
    if auth_failure is not None:
        return fail(
            f"{' '.join(auth_command)} failed: {auth_failure}",
            f"Log in to {adapter} ({executable}) on this Mac, then rerun doctor.",
        )
    install_action = (
        f"Install the {plugin_name} plugin in {adapter} ({executable}), then rerun doctor."
    )
    plugin_output, plugin_failure = _probe(plugin_command)
    if plugin_failure is not None:
        return fail(f"{' '.join(plugin_command)} failed: {plugin_failure}", install_action)
    if not _plugin_installed(plugin_output, plugin_name, json_format=plugin_is_json):
        return fail(
            f"{' '.join(plugin_command)} does not list an installed {plugin_name} plugin",
            install_action,
        )
    return _host_pass(name, f"{executable} is authenticated and carries the {plugin_name} plugin")


def _plugin_installed(output: str, plugin_name: str, *, json_format: bool) -> bool:
    """Require an exact installed-plugin match; a raw substring can match help text or a
    marketplace suggestion for an uninstalled plugin, so this never uses ``in``."""
    if json_format:
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return False
        return _json_names_plugin(data, plugin_name)
    for line in output.splitlines():
        token = line.strip().lstrip("-*•❯> \t")
        if not token:
            continue
        token = token.split()[0]
        # Strip a "name@scope" or "name(version)" suffix so "codagent@codagent" still
        # matches the bare plugin name, without letting "codagent-extra" match it.
        token = token.split("@", 1)[0].split("(", 1)[0]
        if token == plugin_name:
            return True
    return False


def _json_names_plugin(data: object, plugin_name: str) -> bool:
    """Search parsed plugin-list JSON for an entry whose id or name is exactly the plugin."""
    if isinstance(data, Mapping):
        mapping = cast(Mapping[str, object], data)
        for key in ("name", "id"):
            value = mapping.get(key)
            if isinstance(value, str) and (
                value == plugin_name or value.split("@", 1)[0] == plugin_name
            ):
                return True
        return any(_json_names_plugin(value, plugin_name) for value in mapping.values())
    if isinstance(data, list):
        return any(_json_names_plugin(item, plugin_name) for item in cast(list[object], data))
    return False


_TOP_LEVEL_SETTING = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")


def runner_user_settings(text: str) -> dict[str, str]:
    """Top-level scalar keys of the Runner's ``settings.yaml``.

    The factory has no YAML parser; the keys it checks are flat ``key: value`` lines at
    column zero, and nested blocks (``setup:``, ``splash:``) are skipped by construction.
    """
    settings: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith((" ", "\t", "#")) or not line.strip():
            continue
        match = _TOP_LEVEL_SETTING.match(line.split(" #", 1)[0])
        if match is not None:
            settings[match.group(1)] = match.group(2).strip("'\"")
    return settings


def _runner_settings_diagnostic() -> Diagnostic:
    name = "fix host Runner user settings"
    settings_path = Path.home() / ".agent-runner" / "settings.yaml"
    if not settings_path.is_file():
        return Diagnostic(
            name,
            False,
            f"Runner user settings are unavailable: {settings_path}",
            "Set autonomous_backend: headless and autonomous_permission_mode: yolo in the "
            "Runner user settings.",
            group="fix-host",
        )
    try:
        settings = runner_user_settings(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        return Diagnostic(
            name,
            False,
            f"cannot read {settings_path}: {error}",
            "Repair the Runner user settings.",
            group="fix-host",
        )
    backend = settings.get("autonomous_backend")
    permission = settings.get("autonomous_permission_mode")
    if backend != "headless" or permission != "yolo":
        return Diagnostic(
            name,
            False,
            f"Runner user settings are autonomous_backend={backend!r}, "
            f"autonomous_permission_mode={permission!r}",
            "Set autonomous_backend: headless and autonomous_permission_mode: yolo in the "
            "Runner user settings.",
            group="fix-host",
        )
    return Diagnostic(
        name,
        True,
        "Runner user settings select headless backend and yolo permission mode",
        "",
        group="fix-host",
    )
