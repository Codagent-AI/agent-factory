"""INT-004: feature configuration and registration at the runtime boundary."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.work_kinds import handlers
from agent_factory.work_kinds.pull_request import kinds, readiness
from agent_factory.work_kinds.pull_request.readiness import _validate_diagnostic, check_readiness
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE


def test_feature_settings_load_and_share_fix_targets() -> None:
    shared = SharedConfig.from_toml(
        _SHARED_BASE
        + '\n[feature]\ncontract = "factory-feature/1"\n'
        + '[feature.defaults]\nlead = "codex:lead:high"\n'
        + 'implementor = "codex:impl:high"\ntester = "codex:test:high"\n'
        + 'crosscheck = "claude:check:high"\n'
    )
    shared = replace(shared, fix=FixConfig(targets=(FixTarget("example/work"),)))
    local = LocalConfig.from_toml(
        _LOCAL_BASE
        + '[feature]\nexecution = "host"\nminimum_free_gib = 2\n'
        + "[feature.limits]\ninactivity_seconds = 90\nexecution_seconds = 180\n"
        + "total_seconds = 240\n"
    )
    feature = handlers(shared, local)["feature"]
    assert shared.feature is not None
    assert shared.routing.feature_type == "Feature"
    assert kinds.FEATURE.targets(shared) == shared.fix.targets
    assert kinds.FEATURE.reconcile is kinds.ReconcilePolicy.RESUME_FROM_OWN_BRANCH
    assert kinds.FEATURE.roles == ("lead", "implementor", "tester", "crosscheck")
    assert feature.execution_mode(local) == "host"
    assert feature.limits(local).total_seconds == 240
    assert local.feature.minimum_free_gib == 2


def test_feature_registration_survives_disabled_configuration() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE)
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert shared.feature is None
    assert "feature" in handlers(shared, local)
    assert local.feature.limits.inactivity_seconds == 1800
    assert local.feature.limits.execution_seconds == 21600
    assert local.feature.limits.total_seconds == 28800


@pytest.mark.parametrize("mode", ["docker", "fly"])
def test_feature_rejects_non_host_execution(mode: str) -> None:
    with pytest.raises(ConfigurationError, match=mode):
        LocalConfig.from_toml(_LOCAL_BASE + f'[feature]\nexecution = "{mode}"\n')


def test_feature_doctor_names_missing_verify_change(tmp_path: Path) -> None:
    runner = tmp_path / "agent-runner"
    runner.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *factory-feature-v1.0.yaml*) echo "unknown core/verify-change" >&2; exit 1;;\n'
        "esac\nexit 0\n"
    )
    runner.chmod(0o755)
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n")
    result = _validate_diagnostic(str(runner), shared, kinds.FEATURE)
    assert not result.available
    assert result.group == "feature-host"
    assert "core/verify-change" in result.detail


def test_disabled_feature_has_no_readiness_gate() -> None:
    shared = replace(
        SharedConfig.from_toml(_SHARED_BASE),
        fix=FixConfig(targets=(FixTarget("example/work"),)),
    )
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert check_readiness(local, shared, definition=kinds.FEATURE) == []


def test_enabled_feature_without_fix_targets_reports_feature_host_failure() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n")
    local = LocalConfig.from_toml(_LOCAL_BASE)
    diagnostics = check_readiness(local, shared, definition=kinds.FEATURE)
    assert any(
        not check.available and check.group == "feature-host" and "target" in check.detail
        for check in diagnostics
    )


def test_feature_doctor_reports_missing_openspec_as_informational(tmp_path: Path) -> None:
    shared = replace(
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        fix=FixConfig(targets=(FixTarget("example/work"),)),
    )
    local = replace(
        LocalConfig.from_toml(_LOCAL_BASE),
        repositories=replace(
            LocalConfig.from_toml(_LOCAL_BASE).repositories,
            working_clones={"example/work": tmp_path},
        ),
    )
    checks = readiness._openspec_diagnostics(local, shared)
    assert len(checks) == 1
    assert checks[0].available
    assert checks[0].group == "feature-host"
    assert "openspec/" in checks[0].detail


def test_packaged_define_workflow_declares_feature_contract() -> None:
    resource = (
        files("agent_factory.work_kinds.pull_request") / "workflow" / "factory-define-v1.0.yaml"
    )
    assert resource.read_text().splitlines()[0] == "# factory-contract: factory-feature/1"
