from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from agent_factory.config import FixBranches, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.work_kinds.fix.readiness import check_readiness

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

_CONTRACT = "factory-fix/1"
_CONTRACT_PATH = "workflows/core/factory-fix-v1.0.yaml"


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _runner_checkout(tmp_path: Path, *, with_contract: bool) -> Path:
    origin = tmp_path / "runner-origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    if with_contract:
        workflow = origin / "workflows" / "core"
        workflow.mkdir(parents=True)
        (workflow / "factory-fix-v1.0.yaml").write_text(
            f"# factory-contract: {_CONTRACT}\nname: factory-fix\n"
        )
    else:
        (origin / "README.md").write_text("no workflow here\n")
    git(origin, "add", "-A")
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "initial",
    )
    clone = tmp_path / "runner-clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)
    return clone


def _shared(with_targets: bool = True) -> SharedConfig:
    base = SharedConfig.from_toml(_SHARED_BASE)
    return dataclasses.replace(
        base,
        fix=FixConfig(
            targets=(FixTarget("example/work"),) if with_targets else (),
            branches=FixBranches(runner="main", skills="main"),
            defaults={},
            contract=_CONTRACT,
        ),
    )


def _local(tmp_path: Path, runner_checkout: Path, *, fix_environment: Path | None) -> LocalConfig:
    text = f'''\
shared_config = "/opt/agent-factory/config/codagent.toml"
storage_root = "{tmp_path / "storage"}"

[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{runner_checkout}"
agent_skills = "{tmp_path / "skills"}"

[schedule]
timezone = "America/New_York"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "{tmp_path / "app.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
'''
    if fix_environment is not None:
        text += f'fix_environment = "{fix_environment}"\n'
    return LocalConfig.from_toml(text)


def test_no_diagnostics_when_no_fix_targets_configured(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    local = _local(tmp_path, checkout, fix_environment=None)
    assert check_readiness(local, _shared(with_targets=False)) == []


def test_missing_credential_file_fails_closed(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    local = _local(tmp_path, checkout, fix_environment=tmp_path / "missing.env")
    diagnostics = check_readiness(local, _shared())
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False


def test_credential_with_wrong_variable_name_fails_closed(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("OTHER_VAR=x\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False


def test_credential_with_wrong_variable_name_does_not_leak_the_value(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("TOKEN=ghp_supersecretvalue\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False
    assert "ghp_supersecretvalue" not in credential.detail
    assert "TOKEN" in credential.detail


def test_credential_with_non_utf8_bytes_reports_diagnostic_instead_of_raising(
    tmp_path: Path,
) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_bytes(b"GH_TOKEN=\xff\xfe\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False


def test_well_formed_credential_passes(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is True


def test_credential_equal_to_installation_token_fails_closed(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=shared-token\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared(), installation_token="shared-token")
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False


def test_missing_workflow_contract_fails_closed(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=False)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    contract = next(d for d in diagnostics if d.name == "fix workflow contract")
    assert contract.available is False


def test_launch_is_unavailable_until_the_sandbox_launcher_is_implemented(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    launch = next(d for d in diagnostics if d.name == "fix sandbox launch")
    assert launch.available is False


def test_present_workflow_contract_passes(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    contract = next(d for d in diagnostics if d.name == "fix workflow contract")
    assert contract.available is True


def test_handler_readiness_uses_attached_installation_token(tmp_path: Path) -> None:
    from agent_factory.work_kinds.fix.handler import FixHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=shared-token\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = FixHandler(_shared(), local)
    handler.attach_installation_token(lambda: "shared-token")
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False


def test_handler_readiness_fails_closed_when_the_token_provider_fails(tmp_path: Path) -> None:
    from agent_factory.github import GitHubApiError
    from agent_factory.work_kinds.fix.handler import FixHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = FixHandler(_shared(), local)

    def _broken() -> str:
        raise GitHubApiError("cannot mint")

    handler.attach_installation_token(_broken)
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False
    assert "cannot mint" in credential.detail


def test_handler_readiness_fails_closed_on_unexpected_provider_errors(tmp_path: Path) -> None:
    from agent_factory.work_kinds.fix.handler import FixHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = FixHandler(_shared(), local)

    def _broken() -> str:
        raise RuntimeError("transport wrapper exploded")

    handler.attach_installation_token(_broken)
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False
    assert "transport wrapper exploded" in credential.detail
