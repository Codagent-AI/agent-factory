"""[notify] defaults and explicit configuration errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, SharedConfig


def _base() -> str:
    text = Path("config/codagent.toml").read_text()
    head, rest = text.split("\n[notify]\n", 1)
    return head + "\n[watch]\n" + rest.split("\n[watch]\n", 1)[1]


def test_notify_defaults_and_checked_in_profile() -> None:
    assert not SharedConfig.from_toml(_base()).notify.enabled
    configured = SharedConfig.from_file(Path("config/codagent.toml")).notify
    assert configured.enabled and configured.agent == "claude:claude-haiku-4-5-20251001:low"
    assert configured.settle_seconds == 360 and configured.daily_sessions == 30


@pytest.mark.parametrize("agent", ["sonnet", "codex:gpt-5:low", "claude::low", ""])
def test_notify_rejects_invalid_agent(agent: str) -> None:
    with pytest.raises(ConfigurationError, match="notify.agent"):
        SharedConfig.from_toml(_base() + f'\n[notify]\nenabled = true\nagent = "{agent}"\n')


@pytest.mark.parametrize(
    "setting,value",
    [
        ("settle_seconds", "-1"),
        ("watch_wait_minutes", "-1"),
        ("daily_sessions", "-1"),
        ("timeout_minutes", "0"),
    ],
)
def test_notify_rejects_invalid_limit(setting: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=f"notify.{setting}"):
        SharedConfig.from_toml(
            _base() + f'\n[notify]\nenabled = true\nagent = "claude:m:low"\n{setting} = {value}\n'
        )
