"""INT-004: feature configuration and registration at the runtime boundary."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import pytest

from agent_factory.config import ConfigurationError, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.github import IssueComment, ReviewActivity
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds import handlers
from agent_factory.work_kinds.pull_request import kinds, readiness
from agent_factory.work_kinds.pull_request.readiness import _validate_diagnostic, check_readiness
from agent_factory.work_kinds.pull_request.review import process_review_claim
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE
from tests.integration.test_fix_gestures import FakeReviewGitHub, _ReviewPreparedHandler


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


def test_codagent_deployment_enables_feature_with_every_role() -> None:
    shared = SharedConfig.from_toml(Path("config/codagent.toml").read_text())
    assert shared.feature is not None
    assert shared.feature.contract == "factory-feature/1"
    assert shared.feature.defaults["lead"] == shared.fix.defaults["lead"]
    assert shared.feature.defaults["implementor"] == shared.fix.defaults["implementor"]
    assert shared.feature.defaults["tester"] == shared.fix.defaults["tester"]
    # The crosscheck family is an operator choice (the design recommends, not requires, a
    # different family from lead); the deployment only needs a valid profile for it.
    assert readiness._role_profiles_diagnostic(shared, kinds.FEATURE).available


def test_feature_registration_survives_disabled_configuration() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE)
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert shared.feature is None
    assert "feature" in handlers(shared, local)
    assert local.feature.limits.inactivity_seconds == 1800
    assert local.feature.limits.execution_seconds == 21600
    assert local.feature.limits.total_seconds == 28800


def test_removed_feature_section_still_allows_existing_claim_review_round(tmp_path: Path) -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE)
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert shared.feature is None
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 64, "I64", "P64", "feature", "fp", {}))
    store.set_claim_lifecycle(
        claim.id,
        "settled",
        {
            "verdict": "pending-human-review",
            "pr": {
                "number": 7,
                "url": "https://github.com/example/work/pull/7",
                "branch": "factory/feature-64-abcd",
                "head_sha": "a" * 40,
            },
            "review_checkpoint": "2026-01-01T00:00:00Z",
        },
    )
    store.set_preparation(claim.id, {"issue": {"title": "Feature", "body": "Body"}})
    feature = _ReviewPreparedHandler(kinds.FEATURE, shared, local)
    feature.attach_store(store)
    client = FakeReviewGitHub(
        ReviewActivity(
            reviews=(),
            threads=(),
            comments=(IssueComment("c1", "Please revise", "writer", "2099-01-01T00:00:00Z"),),
        ),
        {"writer": "write"},
    )
    current = store.get_claim(claim.id)
    assert current is not None
    admitted = process_review_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        feature,
        current,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path,
        now=datetime(2099, 1, 2, tzinfo=UTC),
        local=local,
        readiness=lambda: True,
    )
    assert admitted is not None
    assert admitted[0].kind == "feature"


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
    assert len(checks) == 2
    assert all(check.available and check.group == "feature-host" for check in checks)
    assert "openspec/" in checks[0].detail
    assert ".validator/config.yml" in checks[1].detail
    (tmp_path / "openspec").mkdir()
    (tmp_path / ".validator").mkdir()
    (tmp_path / ".validator" / "config.yml").write_text("entry_points: []\n")
    assert readiness._openspec_diagnostics(local, shared) == []


@pytest.mark.parametrize(
    ("definition", "section"),
    [(kinds.FEATURE, "feature.defaults"), (kinds.FIX, "fix.defaults")],
)
def test_doctor_fails_when_a_kind_lacks_a_role_profile(
    definition: kinds.PullRequestKind, section: str
) -> None:
    complete = {role: "claude:opus:high" for role in definition.roles}
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n")
    field = "feature" if definition is kinds.FEATURE else "fix"
    roles = getattr(shared, field)

    def with_roles(values: dict[str, str]) -> SharedConfig:
        return replace(shared, **{field: replace(roles, defaults=values)})

    passing = readiness._role_profiles_diagnostic(with_roles(complete), definition)
    assert passing.available
    missing = dict(complete)
    missing.pop(definition.roles[-1])
    failing = readiness._role_profiles_diagnostic(with_roles(missing), definition)
    assert not failing.available
    assert definition.roles[-1] in failing.detail and section in failing.action
    malformed = {**complete, definition.roles[0]: "claude"}
    assert not readiness._role_profiles_diagnostic(with_roles(malformed), definition).available


def test_packaged_define_workflow_declares_feature_contract() -> None:
    resource = (
        files("agent_factory.work_kinds.pull_request") / "workflow" / "factory-define-v1.0.yaml"
    )
    assert resource.read_text().splitlines()[0] == "# factory-contract: factory-feature/1"
