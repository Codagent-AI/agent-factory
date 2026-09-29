# pyright: reportPrivateUsage=false
"""Launch prerequisites shared by doctor and watch dispatch."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from agent_factory.github import AppCredentials, InstallationTokenProvider
from agent_factory.operations import Diagnostic
from agent_factory.watch.session import inherited_environment
from agent_factory.work_kinds.pull_request import readiness as host
from agent_factory.work_kinds.pull_request.launch import staged_config_text
from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import InstallationTokenProvider


def diagnostics(
    local: LocalConfig,
    shared: SharedConfig,
    token_provider: InstallationTokenProvider | None = None,
) -> list[Diagnostic]:
    if not shared.watch.enabled:
        return []
    checks: list[Diagnostic] = []
    runner = shutil.which("agent-runner")
    if runner:
        checks.extend(
            (host._runner_version_diagnostic(runner), host._session_dir_flag_diagnostic(runner))
        )
    else:
        checks.append(host._which_diagnostic("agent-runner"))
    checks.extend(host._which_diagnostic(name) for name in ("git", "gh"))
    adapters = {
        profile.split(":", 1)[0] for profile in (shared.watch.agent, *shared.watch.agents.values())
    }
    checks.extend(host._role_cli_diagnostic(name) for name in sorted(adapters))
    checks.append(host._runner_settings_diagnostic())
    workflow = Path(__file__).parent / "workflow" / "factory-watch-v1.0.yaml"
    checks.append(
        Diagnostic(
            "watch workflow",
            workflow.is_file() and "# factory-contract: factory-watch/1" in workflow.read_text(),
            str(workflow),
            "Install the packaged watch workflow.",
            "watch",
        )
    )
    if runner and workflow.is_file():
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory)
            config = catalog / ".agent-runner" / "config.yaml"
            config.parent.mkdir(parents=True)
            cli, model, effort = shared.watch.agent.split(":")
            config.write_text(
                staged_config_text(None, {"watcher": (cli, model, effort)}),
                encoding="utf-8",
            )
            for name in ("factory-watch-v1.0.yaml", "check-contract.sh", "check-result.sh"):
                shutil.copyfile(workflow.parent / name, catalog / name)
            try:
                validated = subprocess.run(
                    [
                        runner,
                        "-C",
                        str(catalog),
                        "--profile",
                        "factory",
                        "-validate",
                        str(catalog / workflow.name),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                available = validated.returncode == 0
                detail = validated.stderr.strip() or "packaged workflow validates"
            except (OSError, subprocess.TimeoutExpired) as error:
                available, detail = False, str(error)
            checks.append(
                Diagnostic(
                    "watch workflow validation",
                    available,
                    detail,
                    "Repair the packaged workflow or installed Runner.",
                    "watch",
                )
            )
    workspace = PullRequestWorkspace(
        local.storage_root, local.repositories.agent_runner, local.repositories.agent_skills
    )
    try:
        if token_provider is None:
            token_provider = InstallationTokenProvider(
                AppCredentials(
                    shared.app_id, shared.installation_id, local.credentials.github_app_key
                )
            )
        workspace.fetch_mirror(shared.watch.repository, token_provider())
        workspace.resolve_mirror(shared.watch.repository, "main")
        checks.append(Diagnostic("watch mirror", True, shared.watch.repository, "", "watch"))
    except Exception as error:
        checks.append(
            Diagnostic(
                "watch mirror", False, str(error), "Repair the watch repository mirror.", "watch"
            )
        )
    try:
        login = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env=inherited_environment(),
        ).stdout.strip()
        push = subprocess.run(
            ["gh", "api", f"repos/{shared.watch.repository}", "-q", ".permissions.push"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            env=inherited_environment(),
        ).stdout.strip()
        valid = bool(login) and login.lower() != shared.bot_login.lower() and push == "true"
        checks.append(
            Diagnostic(
                "watch writer login",
                valid,
                f"{login}; push={push}",
                "Authenticate a writer with push permission in gh.",
                "watch",
            )
        )
    except (OSError, subprocess.SubprocessError) as error:
        checks.append(
            Diagnostic(
                "watch writer login", False, str(error), "Authenticate gh as a writer.", "watch"
            )
        )
    return [dataclasses.replace(check, group="watch") for check in checks]
