"""INT-007: idle-cleanup configuration reaches retention and the human-review handoff."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, LocalConfig


def _local_text(tmp_path: Path, extra_limits: str = "") -> str:
    return f"""\
shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path / "factory"}"

[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"

[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 60
total_seconds = 60
codex_reset_fallback_seconds = 60
{extra_limits}

[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
"""


def test_idle_retention_periods_default_to_three_and_fourteen_days(tmp_path: Path) -> None:
    local = LocalConfig.from_toml(_local_text(tmp_path))

    assert local.limits.abandoned_retention_days == 3
    assert local.limits.settled_retention_days == 14


def test_configured_settled_period_keeps_the_abandoned_default(tmp_path: Path) -> None:
    local = LocalConfig.from_toml(_local_text(tmp_path, "settled_retention_days = 9"))

    assert local.limits.settled_retention_days == 9
    assert local.limits.abandoned_retention_days == 3


@pytest.mark.parametrize("key", ["abandoned_retention_days", "settled_retention_days"])
@pytest.mark.parametrize("value", [0, -1])
def test_non_positive_idle_periods_are_rejected(tmp_path: Path, key: str, value: int) -> None:
    with pytest.raises(ConfigurationError, match=key):
        LocalConfig.from_toml(_local_text(tmp_path, f"{key} = {value}"))


def test_handoff_states_the_configured_settled_period(tmp_path: Path) -> None:
    from agent_factory.config import SharedConfig
    from agent_factory.work_kinds.eval import EvalHandler

    local = LocalConfig.from_toml(_local_text(tmp_path, "settled_retention_days = 9"))
    shared = SharedConfig.from_toml(Path("config/codagent.toml").read_text())
    handler = EvalHandler.from_config(shared, local)
    assert handler.adapter is not None
    script = tmp_path / "human-review.sh"
    script.write_text("#!/bin/sh\n")

    handoff = handler.adapter.review_handoff(
        {"evaluation_status": "pending-human-review"}, script, tmp_path / "rep-1"
    )

    assert handoff is not None
    assert "until the item moves to Done or until 9 days after the request settles" in handoff
    assert "whichever comes first" in handoff
    assert "releases the retained suite worktree, reviewed or not" in handoff
