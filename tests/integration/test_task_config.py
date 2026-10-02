"""Task configuration reaches the runtime and keeps existing kind defaults."""

# pyright: reportPrivateUsage=false

from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, LocalConfig, SharedConfig
from agent_factory.work_kinds import handlers
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.kinds import TASK, registered
from agent_factory.work_kinds.pull_request.readiness import check_readiness
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE


def test_task_configuration_and_registration() -> None:
    disabled = SharedConfig.from_toml(_SHARED_BASE)
    shared = SharedConfig.from_toml(_SHARED_BASE + '\n[task]\ncontract = "factory-task/1"\n')
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert not TASK.enabled(disabled)
    assert TASK.enabled(shared)
    assert shared.routing.task_type == "Task"
    assert TASK.contract(shared) == "factory-task/1"
    assert TASK.targets(shared) == shared.fix.targets
    assert (
        local.task.limits.inactivity_seconds,
        local.task.limits.execution_seconds,
        local.task.limits.total_seconds,
    ) == (900, 7200, 10800)
    assert local.task.execution == "host"
    assert registered()[-1] is TASK
    assert "factory-task-v1.0.yaml" in launch.STAGED_FILES
    assert "factory-task-guard-v1.0.yaml" in launch.STAGED_FILES
    assert set(handlers(shared, local)) >= {"fix", "feature", "task", "eval"}
    assert local.feature.limits.inactivity_seconds == 1800
    assert local.feature.limits.execution_seconds == 21600
    assert local.feature.limits.total_seconds == 28800
    committed = SharedConfig.from_file(Path("config/codagent.toml"))
    assert TASK.enabled(committed)
    assert committed.task is not None
    assert committed.task.defaults["lead"].startswith("claude:claude-sonnet-")
    custom = _SHARED_BASE.replace('eval_type = "Eval"', 'eval_type = "Eval"\ntask_type = "Chore"')
    assert SharedConfig.from_toml(custom + "\n[task]\n").routing.task_type == "Chore"
    for mode in ("docker", "fly"):
        with pytest.raises(ConfigurationError, match="task.execution"):
            LocalConfig.from_toml(_LOCAL_BASE + f'\n[task]\nexecution = "{mode}"\n')
    with pytest.raises(ConfigurationError, match="task.limits.total_seconds"):
        LocalConfig.from_toml(_LOCAL_BASE + '\n[task.limits]\ntotal_seconds = "large"\n')


def test_task_doctor_diagnostic_uses_task_host_group() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[task]\n")
    diagnostics = check_readiness(LocalConfig.from_toml(_LOCAL_BASE), shared, definition=TASK)
    assert len(diagnostics) == 1
    assert diagnostics[0].group == "task-host"
    assert diagnostics[0].name == "task targets"
