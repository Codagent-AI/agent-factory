"""INT-006: disabled watch adds no doctor diagnostics."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_factory.config import (
    CredentialsConfig,
    LimitsConfig,
    LocalConfig,
    RepositoryConfig,
    ScheduleConfig,
    SharedConfig,
    WatchConfig,
)
from agent_factory.watch.readiness import diagnostics


def test_disabled_watch_has_no_launch_checks(tmp_path: Path) -> None:
    local = LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )
    original = SharedConfig.from_file(Path("config/codagent.toml"))
    from dataclasses import replace

    from agent_factory.config import WatchConfig

    assert diagnostics(local, replace(original, watch=WatchConfig())) == []


def test_issue_login_must_differ_from_bot_and_have_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    from dataclasses import replace
    from typing import cast

    from agent_factory.github import InstallationTokenProvider
    from agent_factory.operations import Diagnostic
    from agent_factory.work_kinds.pull_request import readiness as host
    from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace

    local = LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(shared, watch=WatchConfig(True, "o/r", "claude:model:medium"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    login = tmp_path / "login"
    login.write_text(shared.bot_login)
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/bin/sh\nif [ -n "${{GH_TOKEN:-}}" ]; then exit 2; fi\n'
        f'if [ "$2" = user ]; then cat "{login}"; else echo true; fi\n'
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_TOKEN", "must-not-pass")

    def ok(*_args: object) -> Diagnostic:
        return Diagnostic("stub", True, "ready", "", "watch")

    monkeypatch.setattr(host, "_runner_version_diagnostic", ok)
    monkeypatch.setattr(host, "_session_dir_flag_diagnostic", ok)
    monkeypatch.setattr(host, "_which_diagnostic", ok)
    monkeypatch.setattr(host, "_role_cli_diagnostic", ok)
    monkeypatch.setattr(host, "_runner_settings_diagnostic", ok)

    def fetched(self: PullRequestWorkspace, repository: str, token: str | None) -> None:
        return None

    def resolved(self: PullRequestWorkspace, repository: str, branch: str) -> str:
        return "0" * 40

    monkeypatch.setattr(PullRequestWorkspace, "fetch_mirror", fetched)
    monkeypatch.setattr(PullRequestWorkspace, "resolve_mirror", resolved)
    token = cast(InstallationTokenProvider, lambda: "unused")
    bot_checks = diagnostics(local, shared, token)
    assert any(check.name == "watch issue login" and not check.available for check in bot_checks)
    login.write_text("writer")
    writer_checks = diagnostics(local, shared, token)
    assert any(check.name == "watch issue login" and check.available for check in writer_checks)
    assert not any(check.name == "watch writer login" for check in writer_checks)
