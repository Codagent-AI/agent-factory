"""Readiness implementation for the Fly Machine backend."""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agent_factory.backends import Disposal, Probe
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.fly.api import FlyApiError, FlyMachinesClient
from agent_factory.operations import Diagnostic


class FlyMachineBackend:
    name = "fly-machine"

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        del shared
        if local.fly is None:
            return [
                Diagnostic(
                    "Fly configuration",
                    False,
                    "Fly settings are missing",
                    "Configure [fly] settings.",
                    "eval-fly",
                )
            ]
        fly = local.fly
        token_check = _token_diagnostic(fly.token_file)
        result = [token_check]
        if token_check.available:
            client = FlyMachinesClient(fly.app, fly.token_file)
            for name, call, action in (
                (
                    "Fly app API",
                    client.get_app,
                    f"Verify deploy-token access to Fly app {fly.app}.",
                ),
                (
                    "Fly image manifest",
                    lambda: client.resolve_manifest(fly.image),
                    f"Push or configure a resolvable Fly image: {fly.image}.",
                ),
            ):
                try:
                    call()
                    result.append(
                        Diagnostic(name, True, "check succeeded", "No action required.", "eval-fly")
                    )
                except FlyApiError as error:
                    result.append(Diagnostic(name, False, str(error), action, "eval-fly"))
        result.append(_flyctl_diagnostic(fly.app))
        return result

    def identity_from_plan(self, plan: object, run: object) -> Mapping[str, object] | None:
        raise NotImplementedError

    def probe(self, identity: Mapping[str, object]) -> Probe:
        raise NotImplementedError

    def terminate(self, identity: Mapping[str, object]) -> bool:
        raise NotImplementedError

    def dispose(self, identity: Mapping[str, object], decision: Disposal) -> None:
        raise NotImplementedError

    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]:
        raise NotImplementedError

    def reconcile(self, store: object) -> list[str]:
        raise NotImplementedError

    def provenance(self, identity: Mapping[str, object]) -> Mapping[str, object]:
        raise NotImplementedError


def _token_diagnostic(path: Path) -> Diagnostic:
    try:
        metadata = path.stat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return Diagnostic(
            "Fly deploy token",
            False,
            f"token file is unavailable: {error}",
            "Create an owner-readable, single-line Fly deploy token file.",
            "eval-fly",
        )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or len(content.splitlines()) != 1
        or not content.strip()
    ):
        return Diagnostic(
            "Fly deploy token",
            False,
            "token file must be private and contain exactly one token",
            "chmod 600 the token file and leave only the deploy token.",
            "eval-fly",
        )
    return Diagnostic(
        "Fly deploy token",
        True,
        "private single-line token is available",
        "No action required.",
        "eval-fly",
    )


def _flyctl_diagnostic(app: str) -> Diagnostic:
    executable = os.environ.get("PATH", "")
    if not any(
        os.access(os.path.join(part, "flyctl"), os.X_OK) for part in executable.split(os.pathsep)
    ):
        return Diagnostic(
            "flyctl transport",
            False,
            "flyctl is not executable on the service PATH",
            "Install flyctl and add it to the launch service PATH.",
            "eval-fly",
        )
    try:
        completed = subprocess.run(
            ("flyctl", "ssh", "issue", "--app", app), capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            "flyctl transport",
            False,
            f"ssh check could not run: {error}",
            "Restore flyctl SSH access to the Fly app.",
            "eval-fly",
        )
    return Diagnostic(
        "flyctl transport",
        completed.returncode == 0,
        "ssh transport check succeeded"
        if completed.returncode == 0
        else "flyctl ssh cannot reach the app",
        "Restore flyctl SSH access to the Fly app.",
        "eval-fly",
    )
