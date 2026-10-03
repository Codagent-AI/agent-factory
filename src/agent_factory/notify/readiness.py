"""Read-only prerequisites for the Claude notifier."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from agent_factory.operations import Diagnostic
from agent_factory.watch.session import inherited_environment

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig


def diagnostics(local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
    if not shared.notify.enabled:
        return []
    checks: list[Diagnostic] = []
    claude = shutil.which("claude")
    checks.append(
        Diagnostic(
            "claude",
            bool(claude),
            claude or "not on PATH",
            "Install Claude CLI on the service PATH.",
            "notify",
        )
    )
    ps = shutil.which("ps")
    checks.append(
        Diagnostic("ps", bool(ps), ps or "not on PATH", "Install ps on the service PATH.", "notify")
    )
    if claude:
        for name, arguments, expected in (
            ("Claude authentication", ("auth", "status"), None),
            (
                "Claude notifier flags",
                ("--help",),
                ("--tools", "--allowedTools", "--json-schema", "--strict-mcp-config"),
            ),
        ):
            try:
                result = subprocess.run(
                    [claude, *arguments],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                    env=inherited_environment(),
                )
                good = result.returncode == 0 and (
                    expected is None or all(flag in result.stdout for flag in expected)
                )
                detail = (
                    "available"
                    if good
                    else (result.stderr.strip()[:200] or "missing required CLI support")
                )
            except (OSError, subprocess.SubprocessError) as error:
                good, detail = False, str(error)
            checks.append(
                Diagnostic(
                    name,
                    good,
                    detail,
                    "Authenticate Claude CLI and update it to support notifier flags.",
                    "notify",
                )
            )
    root = Path.home() / ".claude" / "sessions"
    available = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
    checks.append(
        Diagnostic(
            "Claude session registry",
            available,
            "empty" if available and not any(root.glob("*.json")) else str(root),
            "Start a local Claude Code session and ensure its registry is readable.",
            "notify",
        )
    )
    return checks
