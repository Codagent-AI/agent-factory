from __future__ import annotations

import pytest

from agent_factory.config import ConfigurationError, LocalConfig, SharedConfig

_SHARED_BASE = """\
[github]
organization = "Example Org"
bot_login = "example-factory[bot]"
app_id = "123"
installation_id = "456"

[project]
id = "PVT_example"
number = 7

[fields.status]
id = "status-field"
[fields.status.options]
backlog = "backlog-option"
ready = "ready-option"
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"

[fields.refs]
id = "refs-field"

[fields.verdict]
id = "verdict-field"
[fields.verdict.options]
pending-human-review = "pending-option"
failed = "failed-option"
quota-deferred = "quota-option"
infra-error = "infra-option"

[routing]
eval_source = "example/evals"
general_sources = ["example/evals", "example/work"]
eval_label = "run-eval"
eval_type = "Eval"

[eval]
harness_ref = "main"
suite = "and-scene"
repetitions = 3
"""

_LOCAL_BASE = """\
shared_config = "/opt/agent-factory/config/codagent.toml"
storage_root = "~/.agent-factory"

[repositories]
agent_evals = "/srv/src/agent-evals"
agent_runner = "/srv/src/agent-runner"
agent_skills = "/srv/src/agent-skills"

[schedule]
timezone = "America/New_York"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 8
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "/etc/agent-factory/github-app.pem"
suite_environment = "/etc/agent-factory/suite.env"
"""


def test_shared_config_defaults_fix_when_section_absent() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE)
    assert shared.fix.targets == ()
    assert shared.fix.branches.runner == "main"
    assert shared.fix.branches.skills == "main"
    assert shared.fix.contract == "factory-fix/1"


def test_shared_config_parses_fix_targets_and_defaults() -> None:
    text = (
        _SHARED_BASE
        + """
[fix]
branches = { runner = "main", skills = "main" }
contract = "factory-fix/1"

[[fix.targets]]
repository = "Codagent-AI/agent-runner"

[[fix.targets]]
repository = "Codagent-AI/agent-skills"
branch = "release"

[fix.defaults]
lead = "cursor:cursor-grok-4.6-high:high"
implementor = "cursor:cursor-grok-4.6-high:high"
tester = "cursor:composer-2.5:high"
"""
    )
    shared = SharedConfig.from_toml(text)
    assert [target.repository for target in shared.fix.targets] == [
        "Codagent-AI/agent-runner",
        "Codagent-AI/agent-skills",
    ]
    assert shared.fix.targets[0].branch == "main"
    assert shared.fix.targets[1].branch == "release"
    assert shared.fix.defaults["lead"] == "cursor:cursor-grok-4.6-high:high"


def test_shared_config_rejects_fix_target_without_repository() -> None:
    text = _SHARED_BASE + '\n[[fix.targets]]\nbranch = "main"\n'
    with pytest.raises(ConfigurationError, match="fix.targets"):
        SharedConfig.from_toml(text)


def test_shared_config_rejects_non_string_fix_target_branch() -> None:
    text = _SHARED_BASE + '\n[[fix.targets]]\nrepository = "Codagent-AI/agent-runner"\nbranch = 0\n'
    with pytest.raises(ConfigurationError, match="fix.targets"):
        SharedConfig.from_toml(text)


def test_shared_config_rejects_empty_fix_target_branch() -> None:
    text = (
        _SHARED_BASE + '\n[[fix.targets]]\nrepository = "Codagent-AI/agent-runner"\nbranch = ""\n'
    )
    with pytest.raises(ConfigurationError, match="fix.targets"):
        SharedConfig.from_toml(text)


def test_shared_config_rejects_non_string_fix_branches() -> None:
    text = _SHARED_BASE + "\n[fix.branches]\nrunner = 123\n"
    with pytest.raises(ConfigurationError, match="fix.branches"):
        SharedConfig.from_toml(text)


def test_local_config_defaults_fix_when_section_absent() -> None:
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert local.fix.limits.inactivity_seconds == 900
    assert local.fix.limits.execution_seconds == 7200
    assert local.fix.limits.total_seconds == 10800
    assert local.fix.schedule is None
    assert local.limits.memory_reservation_gib == 3
    assert local.credentials.fix_environment is None
    assert local.repositories.working_clones == {}


def test_local_config_parses_fix_section() -> None:
    text = (
        _LOCAL_BASE.replace(
            "[limits]\nminimum_free_gib = 8",
            "[limits]\nminimum_free_gib = 8\nmemory_reservation_gib = 5",
        ).replace(
            'suite_environment = "/etc/agent-factory/suite.env"',
            'suite_environment = "/etc/agent-factory/suite.env"\n'
            'fix_environment = "/etc/agent-factory/fix.env"',
        )
        + """
[fix.limits]
inactivity_seconds = 60
execution_seconds = 120
total_seconds = 180

[fix.schedule]
timezone = "America/New_York"
poll_seconds = 60

[repositories.working_clones]
"Codagent-AI/agent-runner" = "/srv/working/agent-runner"
"""
    )
    local = LocalConfig.from_toml(text)
    assert local.fix.limits.inactivity_seconds == 60
    assert local.fix.limits.execution_seconds == 120
    assert local.fix.limits.total_seconds == 180
    assert local.fix.schedule is not None
    assert local.fix.schedule.always_open is True
    assert local.limits.memory_reservation_gib == 5
    assert local.credentials.fix_environment is not None
    assert local.credentials.fix_environment.name == "fix.env"
    assert "Codagent-AI/agent-runner" in local.repositories.working_clones


def test_local_config_rejects_malformed_fix_limit() -> None:
    text = _LOCAL_BASE + "\n[fix.limits]\ninactivity_seconds = -1\n"
    with pytest.raises(ConfigurationError, match="fix.limits.inactivity_seconds"):
        LocalConfig.from_toml(text)
