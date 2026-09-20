from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, LocalConfig
from agent_factory.work_kinds.eval import EvalDefaults, parse_request


def _local(extra: str = "") -> str:
    return (
        """\
shared_config = "/tmp/shared.toml"
storage_root = "/tmp/factory"
[repositories]
agent_evals = "/tmp/evals"
agent_runner = "/tmp/runner"
agent_skills = "/tmp/skills"
[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 0
[limits]
minimum_free_gib = 1
inactivity_seconds = 1
execution_seconds = 1
total_seconds = 1
codex_reset_fallback_seconds = 1
[credentials]
github_app_key = "/tmp/key"
suite_environment = "/tmp/env"
"""
        + extra
    )


def test_fly_configuration_defaults_and_required_settings() -> None:
    config = LocalConfig.from_toml(
        _local("""
[eval]
execution = "fly"
[fly]
app = "factory"
image = "registry.fly.io/factory:base"
token_file = "/tmp/fly-token"
""")
    )

    assert config.eval_execution == "fly"
    assert config.fly is not None
    assert (config.fly.region, config.fly.cpus, config.fly.memory_mb) == ("ewr", 4, 8192)
    with pytest.raises(ConfigurationError, match="fly.image"):
        LocalConfig.from_toml(_local('[eval]\nexecution = "fly"\n[fly]\napp = "factory"\n'))


def test_fly_refuses_cursor_from_defaults_and_overrides() -> None:
    defaults = EvalDefaults(
        "main",
        "main",
        {
            "lead": "cursor:agent:medium",
            "implementor": "codex:x:medium",
            "tester": "claude:x:medium",
        },
        False,
        1,
        execution="fly",
    )
    with pytest.raises(ValueError, match="lead.*Cursor is unavailable on Fly"):
        parse_request("```eval\n# defaults\n```", defaults)
    defaults = EvalDefaults(
        "main",
        "main",
        {"lead": "codex:x:medium", "implementor": "codex:x:medium", "tester": "claude:x:medium"},
        False,
        1,
        execution="fly",
    )
    with pytest.raises(ValueError, match="tester.*Cursor is unavailable on Fly"):
        parse_request("```eval\ntester = 'cursor:agent:medium'\n```", defaults)


def test_machine_client_create_shape_and_safe_error(tmp_path: Path) -> None:
    from agent_factory.fly.api import FlyApiError, FlyMachinesClient

    (tmp_path / "token").write_text("secret-token\n", encoding="utf-8")
    client = FlyMachinesClient("factory", tmp_path / "token", base_url="http://127.0.0.1:1")
    with pytest.raises(FlyApiError) as raised:
        client.get_machine("machine-id")
    assert "machine-id" in str(raised.value)
    assert "token" not in repr(raised.value).lower()
