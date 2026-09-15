"""Fail-closed checks that must pass before any fix attempt launches."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from typing import cast

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.operations import ADAPTER_EXECUTABLES, Diagnostic, DiagnosticGroup, fix_floor_gib
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.fix import launch

_TOKEN_LINE = re.compile(r"^GH_TOKEN=(.+)$")

# Every executable host-mode fixes need on PATH, beyond the configured role CLIs.
_HOST_BASE_EXECUTABLES: tuple[str, ...] = (
    "agent-runner",
    "git",
    "gh",
    "jq",
    "python3",
    "agent-validator",
)


def check_readiness(
    local: LocalConfig,
    shared: SharedConfig,
    *,
    installation_token: str | None = None,
    docker_diagnostic: Diagnostic | None = None,
) -> list[Diagnostic]:
    """Diagnostics gating fix admission for the configured execution mode only."""
    if not shared.fix.targets:
        return []
    group: DiagnosticGroup = "fix-host" if local.fix.execution == "host" else "fix-sandbox"
    diagnostics: list[Diagnostic] = []
    if local.fix.execution == "host":
        diagnostics.extend(_host_diagnostics(local, shared))
    else:
        diagnostics.append(_launch_diagnostic(local, shared, docker_diagnostic=docker_diagnostic))
    diagnostics.append(_free_space_diagnostic(local, group=group))
    diagnostics.append(_credential_diagnostic(local, installation_token, group=group))
    diagnostics.append(_contract_diagnostic(local, shared, group=group))
    return diagnostics


def _free_space_diagnostic(local: LocalConfig, *, group: DiagnosticGroup) -> Diagnostic:
    from agent_factory.operations import _free_space  # pyright: ignore[reportPrivateUsage]

    return _free_space(local, floor_gib=fix_floor_gib(local), group=group)


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
    local: LocalConfig, shared: SharedConfig, *, group: DiagnosticGroup = "fix-sandbox"
) -> Diagnostic:
    """The packaged workflow declares the contract and the Runner branch head can run it."""
    name = "fix workflow contract"
    marker = launch.contract_marker(shared.fix.contract)
    try:
        launch.packaged_workflow_text(shared.fix.contract)
    except ReadinessError as error:
        return Diagnostic(
            name,
            False,
            str(error),
            f"Reinstall the factory; its packaged {launch.WORKFLOW_FILE} must start with "
            f"{marker!r}.",
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
        f"workflow contract {shared.fix.contract} is packaged and runnable at {where}",
        "",
        group=group,
    )


def _host_diagnostics(local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
    """Everything host-mode fixes need: the installed Runner, host CLIs, and Runner settings."""
    diagnostics: list[Diagnostic] = []
    diagnostics.append(_executable_diagnostic("agent-runner", ("agent-runner", "-version")))
    diagnostics.append(_session_dir_flag_diagnostic())
    diagnostics.append(_validate_diagnostic(shared))
    for executable in ("git", "gh", "jq", "python3", "agent-validator"):
        diagnostics.append(_which_diagnostic(executable))
    diagnostics.append(_gh_auth_status_diagnostic(local))
    diagnostics.extend(_role_cli_diagnostics(shared))
    diagnostics.append(_runner_settings_diagnostic())
    return diagnostics


def _which_diagnostic(executable: str) -> Diagnostic:
    name = f"fix host {executable} on PATH"
    found = shutil.which(executable)
    if found is None:
        return Diagnostic(
            name,
            False,
            f"{executable} is not on PATH",
            f"Install {executable} and put it on the service PATH.",
            group="fix-host",
        )
    return Diagnostic(name, True, f"{executable} resolves to {found}", "", group="fix-host")


def _executable_diagnostic(name: str, command: tuple[str, ...]) -> Diagnostic:
    label = f"fix host {name} on PATH"
    found = shutil.which(command[0])
    if found is None:
        return Diagnostic(
            label,
            False,
            f"{command[0]} is not on PATH",
            f"Install {command[0]} and put it on the service PATH.",
            group="fix-host",
        )
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            label,
            False,
            f"{' '.join(command)} could not run: {error}",
            "Repair the installation.",
            group="fix-host",
        )
    if completed.returncode != 0:
        return Diagnostic(
            label,
            False,
            f"{' '.join(command)} failed",
            "Repair the installed Agent Runner.",
            group="fix-host",
        )
    return Diagnostic(
        label, True, f"{found} responds to {' '.join(command[1:])}", "", group="fix-host"
    )


def _session_dir_flag_diagnostic() -> Diagnostic:
    name = "fix host agent-runner --session-dir support"
    found = shutil.which("agent-runner")
    if found is None:
        return Diagnostic(
            name,
            False,
            "agent-runner is not on PATH",
            "Install agent-runner and put it on the service PATH.",
            group="fix-host",
        )
    try:
        completed = subprocess.run(
            ["agent-runner", "run", "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            name,
            False,
            f"agent-runner run --help could not run: {error}",
            "Repair the installation.",
            group="fix-host",
        )
    text = completed.stdout + completed.stderr
    if "--session-dir" not in text:
        return Diagnostic(
            name,
            False,
            "installed agent-runner run --help does not list --session-dir",
            "Update the installed Agent Runner to a build that supports --session-dir.",
            group="fix-host",
        )
    return Diagnostic(
        name, True, "installed agent-runner supports --session-dir", "", group="fix-host"
    )


def _validate_diagnostic(shared: SharedConfig) -> Diagnostic:
    name = "fix host workflow validation"
    found = shutil.which("agent-runner")
    if found is None:
        return Diagnostic(
            name,
            False,
            "agent-runner is not on PATH",
            "Install agent-runner and put it on the service PATH.",
            group="fix-host",
        )
    try:
        workflow_text = launch.packaged_workflow_text(shared.fix.contract)
    except ReadinessError as error:
        return Diagnostic(
            name, False, str(error), "Reinstall the factory package.", group="fix-host"
        )
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        workflow_path = _Path(tmp) / "factory-fix-v1.0.yaml"
        workflow_path.write_text(workflow_text, encoding="utf-8")
        try:
            completed = subprocess.run(
                ["agent-runner", "-validate", str(workflow_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return Diagnostic(
                name,
                False,
                f"agent-runner -validate could not run: {error}",
                "Repair the installation.",
                group="fix-host",
            )
    if completed.returncode != 0:
        return Diagnostic(
            name,
            False,
            f"agent-runner -validate rejected the packaged workflow: {completed.stderr.strip()}",
            "Repair the packaged fix workflow or the installed Runner.",
            group="fix-host",
        )
    return Diagnostic(name, True, "packaged workflow validates", "", group="fix-host")


def _gh_auth_status_diagnostic(local: LocalConfig) -> Diagnostic:
    name = "fix host gh auth status"
    path = local.credentials.fix_environment
    token = None
    if path is not None and path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                match = _TOKEN_LINE.match(line.strip())
                if match:
                    token = match.group(1)
                    break
        except (OSError, UnicodeError):
            token = None
    if shutil.which("gh") is None:
        return Diagnostic(
            name,
            False,
            "gh is not on PATH",
            "Install gh and put it on the service PATH.",
            group="fix-host",
        )
    if token is None:
        return Diagnostic(
            name,
            False,
            "no GH_TOKEN found to check gh auth status against",
            "Configure credentials.fix_environment with a GH_TOKEN line.",
            group="fix-host",
        )
    try:
        completed = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            check=False,
            timeout=15,
            env={"GH_TOKEN": token, "PATH": os.environ.get("PATH", "")},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            name,
            False,
            f"gh auth status could not run: {error}",
            "Repair the gh installation.",
            group="fix-host",
        )
    if completed.returncode != 0:
        return Diagnostic(
            name,
            False,
            "gh auth status failed with the configured fix credential",
            "Verify the fix credential token is valid.",
            group="fix-host",
        )
    return Diagnostic(
        name, True, "gh auth status succeeded with the fix credential", "", group="fix-host"
    )


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
    "cursor": (("agent", "status"), ("agent", "plugin", "list"), "codagent", False),
}


def _role_cli_diagnostics(shared: SharedConfig) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    adapters = sorted(
        {str(value).split(":", 1)[0] for value in shared.fix.defaults.values() if value}
    )
    for adapter in adapters:
        executable = ADAPTER_EXECUTABLES.get(adapter)
        name = f"fix host {adapter} CLI"
        if executable is None:
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"unsupported fix role CLI: {adapter}",
                    "Use a supported CLI adapter.",
                    group="fix-host",
                )
            )
            continue
        found = shutil.which(executable)
        if found is None:
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"{executable} is not on PATH",
                    f"Install and log in to {adapter} ({executable}) and put it on the "
                    "service PATH.",
                    group="fix-host",
                )
            )
            continue
        checks = _ADAPTER_CHECKS.get(adapter)
        if checks is None:
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"no host readiness probe is defined for adapter {adapter!r}",
                    "Use a supported CLI adapter.",
                    group="fix-host",
                )
            )
            continue
        auth_command, plugin_command, plugin_name, plugin_is_json = checks
        auth_failure = _run_adapter_check(auth_command)
        if auth_failure is not None:
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"{' '.join(auth_command)} failed: {auth_failure}",
                    f"Log in to {adapter} ({executable}) on this Mac, then rerun doctor.",
                    group="fix-host",
                )
            )
            continue
        plugin_output, plugin_error = _run_adapter_check_output(plugin_command)
        if plugin_error is not None:
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"{' '.join(plugin_command)} failed: {plugin_error}",
                    f"Install the {plugin_name} plugin in {adapter} ({executable}), then rerun "
                    "doctor.",
                    group="fix-host",
                )
            )
            continue
        if not _plugin_installed(plugin_output, plugin_name, json_format=plugin_is_json):
            diagnostics.append(
                Diagnostic(
                    name,
                    False,
                    f"{' '.join(plugin_command)} does not list an installed {plugin_name} plugin",
                    f"Install the {plugin_name} plugin in {adapter} ({executable}), then rerun "
                    "doctor.",
                    group="fix-host",
                )
            )
            continue
        diagnostics.append(
            Diagnostic(
                name,
                True,
                f"{executable} is authenticated and carries the {plugin_name} plugin",
                "",
                group="fix-host",
            )
        )
    return diagnostics


def _run_adapter_check(command: tuple[str, ...]) -> str | None:
    """Run a noninteractive readiness command; return None on success, else the failure text."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return str(error)
    if completed.returncode != 0:
        return (completed.stderr or completed.stdout or "non-zero exit").strip()[:300]
    return None


def _run_adapter_check_output(command: tuple[str, ...]) -> tuple[str, str | None]:
    """Run a listing command; return its combined output, or an error on failure."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "", str(error)
    if completed.returncode != 0:
        return "", (completed.stderr or completed.stdout or "non-zero exit").strip()[:300]
    return completed.stdout + completed.stderr, None


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
        token = line.strip().lstrip("-*• \t")
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


def _runner_settings_diagnostic() -> Diagnostic:
    name = "fix host Runner user settings"
    from pathlib import Path as _Path

    settings_path = _Path.home() / ".agent-runner" / "settings.json"
    if not settings_path.is_file():
        return Diagnostic(
            name,
            False,
            f"Runner user settings are unavailable: {settings_path}",
            "Set autonomous_backend=headless and autonomous_permission_mode=yolo in the "
            "Runner user settings.",
            group="fix-host",
        )
    try:
        import json as _json

        settings = _json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
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
            "Set autonomous_backend=headless and autonomous_permission_mode=yolo in the "
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
