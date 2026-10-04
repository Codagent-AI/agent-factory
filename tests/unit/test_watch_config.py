"""Enabled-only watch validation and inert defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, SharedConfig


def test_watch_is_inert_when_absent_or_disabled() -> None:
    text = Path("config/codagent.toml").read_text()
    without = text.split("\n[watch]\n", 1)[0]
    assert not SharedConfig.from_toml(without).watch.enabled
    disabled = without + '\n[watch]\nenabled = false\nagent = "invalid"\n'
    assert not SharedConfig.from_toml(disabled).watch.enabled


def test_enabled_watch_names_invalid_profile_setting() -> None:
    text = (
        Path("config/codagent.toml")
        .read_text()
        .replace('agent = "claude:claude-sonnet-5-5:medium"', 'agent = "invalid"')
    )
    with pytest.raises(ConfigurationError, match="watch.agent"):
        SharedConfig.from_toml(text)
    text = Path("config/codagent.toml").read_text() + '\n[watch.agents]\nFAILURE = "invalid"\n'
    with pytest.raises(ConfigurationError, match="watch.agents.FAILURE"):
        SharedConfig.from_toml(text)


def test_expected_checks_are_configured_per_repository() -> None:
    watch = SharedConfig.from_file(Path("config/codagent.toml")).watch
    assert watch.expected_checks["codagent-ai/agent-evals"].count("check") == 2
    invalid = Path("config/codagent.toml").read_text().replace(
        '"Codagent-AI/agent-factory" = ["CodeRabbit"]',
        '"Codagent-AI/agent-factory" = []',
    )
    with pytest.raises(ConfigurationError, match="watch.expected_checks"):
        SharedConfig.from_toml(invalid)
