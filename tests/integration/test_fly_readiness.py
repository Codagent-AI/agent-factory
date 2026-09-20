from __future__ import annotations

from pathlib import Path
from typing import cast

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


def test_image_manifest_resolves_with_the_registry_basic_auth_scheme(tmp_path: Path) -> None:
    import base64

    from agent_factory.fly.api import FlyMachinesClient
    from tests.fixtures.fly.api import FakeMachinesApi

    token = tmp_path / "token"
    token.write_text("deploy-token\n", encoding="utf-8")
    with FakeMachinesApi(manifest_digest="sha256:abc") as api:
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        assert client.resolve_manifest("registry.fly.io/app:base") == "sha256:abc"
        request = next(r for r in api.requests if r["method"] == "HEAD")
    headers = {str(k).lower(): str(v) for k, v in cast(dict[str, str], request["headers"]).items()}
    assert headers["authorization"] == "Basic " + base64.b64encode(b"x:deploy-token").decode()
    # Without manifest media types the registry cannot resolve an OCI image.
    assert "application/vnd.oci.image.index.v1+json" in headers["accept"]
    assert request["path"] == "/v2/app/manifests/base"


def test_rate_limited_request_is_retried_until_fly_accepts_it(tmp_path: Path) -> None:
    """Fly limits requests per Machine; back-to-back metadata writes draw HTTP 429."""
    from agent_factory.fly.api import FlyApiError, FlyMachinesClient
    from tests.fixtures.fly.api import FakeMachinesApi

    token = tmp_path / "token"
    token.write_text("deploy-token\n", encoding="utf-8")
    waits: list[float] = []
    with FakeMachinesApi() as api:
        client = FlyMachinesClient("app", token, base_url=api.base_url, sleep=waits.append)
        api.machines["machine-1"] = {"id": "machine-1", "state": "started", "config": {}}
        api.post_failures.extend([429, 429])
        client.set_metadata("machine-1", "run_id", "run-2")
        config = cast(dict[str, dict[str, str]], api.machines["machine-1"]["config"])
        assert config["metadata"]["run_id"] == "run-2"
        assert len(waits) == 2 and waits[1] > waits[0] > 0

        # A limit that never lifts is still reported, with its status, not retried forever.
        api.post_failures.extend([429] * 10)
        with pytest.raises(FlyApiError) as raised:
            client.set_metadata("machine-1", "run_id", "run-3")
        assert raised.value.status == 429
