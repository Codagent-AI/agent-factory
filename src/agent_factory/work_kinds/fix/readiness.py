"""Fail-closed checks that must pass before any fix attempt launches."""

from __future__ import annotations

import re
import stat

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.operations import Diagnostic
from agent_factory.suites.and_scene import ReadinessError

_CONTRACT_PATH = "workflows/core/factory-fix-v1.0.yaml"
_TOKEN_LINE = re.compile(r"^GH_TOKEN=(.+)$")


def check_readiness(
    local: LocalConfig, shared: SharedConfig, *, installation_token: str | None = None
) -> list[Diagnostic]:
    """Diagnostics gating fix admission: the credential file and the workflow contract."""
    if not shared.fix.targets:
        return []
    diagnostics = [_credential_diagnostic(local, installation_token)]
    diagnostics.append(_contract_diagnostic(local, shared))
    return diagnostics


def _credential_diagnostic(local: LocalConfig, installation_token: str | None) -> Diagnostic:
    name = "fix credential"
    action = (
        "Create a private file at credentials.fix_environment containing exactly one "
        "line: GH_TOKEN=<a token distinct from the App installation token>."
    )
    path = local.credentials.fix_environment
    if path is None:
        return Diagnostic(name, False, "credentials.fix_environment is not configured", action)
    try:
        mode = path.stat().st_mode
    except OSError as error:
        return Diagnostic(name, False, f"cannot read {path}: {error}", action)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return Diagnostic(name, False, f"{path} must not be group- or world-accessible", action)
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError as error:
        return Diagnostic(name, False, f"cannot read {path}: {error}", action)
    if len(lines) != 1:
        return Diagnostic(name, False, f"{path} must contain exactly one assignment", action)
    match = _TOKEN_LINE.match(lines[0].strip())
    if match is None:
        return Diagnostic(name, False, f"{path} must assign GH_TOKEN, found: {lines[0]!r}", action)
    token = match.group(1)
    if not token:
        return Diagnostic(name, False, f"{path} GH_TOKEN value is empty", action)
    if installation_token is not None and token == installation_token:
        return Diagnostic(
            name, False, f"{path} GH_TOKEN must not equal the App installation token", action
        )
    return Diagnostic(name, True, f"fix credential is present and well-formed: {path}", "")


def _contract_diagnostic(local: LocalConfig, shared: SharedConfig) -> Diagnostic:
    name = "fix workflow contract"
    action = (
        f"Publish {_CONTRACT_PATH} on the configured Runner branch with the first line "
        f'"# factory-contract: {shared.fix.contract}".'
    )
    from agent_factory import runtime

    try:
        sha = runtime._resolve_revision(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, shared.fix.branches.runner
        )
        text = runtime._git_show(  # pyright: ignore[reportPrivateUsage]
            local.repositories.agent_runner, sha, _CONTRACT_PATH
        )
    except ReadinessError as error:
        return Diagnostic(name, False, str(error), action)
    marker = f"# factory-contract: {shared.fix.contract}"
    first_line = text.splitlines()[0] if text else ""
    if first_line.strip() != marker:
        where = f"{shared.fix.branches.runner}@{sha[:7]}"
        return Diagnostic(
            name, False, f"{_CONTRACT_PATH} at {where} does not declare {marker!r}", action
        )
    return Diagnostic(name, True, f"workflow contract {shared.fix.contract} is present", "")
