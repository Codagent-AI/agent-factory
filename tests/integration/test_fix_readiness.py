# ruff: noqa: E501

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from agent_factory.config import (
    FeatureConfig,
    FixBranches,
    FixConfig,
    FixTarget,
    LocalConfig,
    SharedConfig,
)
from agent_factory.operations import Diagnostic
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from agent_factory.work_kinds.pull_request.kinds import FIX
from agent_factory.work_kinds.pull_request.readiness import check_readiness

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
_FINALIZE_WITH_PARAM = (
    'name: finalize-pr\nparams:\n  - name: ci_fix_cycles\n    default: "3"\nsteps: []\n'
)
_FINALIZE_WITHOUT_PARAM = "name: finalize-pr\nsteps: []\n"


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _runner_checkout(tmp_path: Path, *, with_contract: bool) -> Path:
    origin = tmp_path / "runner-origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    # The fix workflow ships with the factory; the Runner only has to accept the
    # ci_fix_cycles parameter the workflow passes to finalize-pr.
    workflow = origin / "workflows" / "core"
    workflow.mkdir(parents=True)
    (workflow / "finalize-pr-v1.0.yaml").write_text(
        _FINALIZE_WITH_PARAM if with_contract else _FINALIZE_WITHOUT_PARAM
    )
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


def test_second_kind_readiness_uses_its_own_target_configuration(tmp_path: Path) -> None:
    local = _local(tmp_path, tmp_path / "missing-runner", fix_environment=None)
    shared = dataclasses.replace(_shared(with_targets=False), feature=FeatureConfig())

    def feature_targets(_shared: SharedConfig) -> tuple[FixTarget, ...]:
        return (FixTarget("example/features"),)

    other = dataclasses.replace(
        FIX,
        kind="feature",
        noun="Feature",
        targets=feature_targets,
    )

    diagnostics = check_readiness(local, shared, definition=other)

    assert diagnostics
    assert any(d.name == "feature credential" for d in diagnostics)


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


def test_runner_finalize_pr_without_fix_cycles_fails_closed(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=False)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    contract = next(d for d in diagnostics if d.name == "fix workflow contract")
    assert contract.available is False
    assert "ci_fix_cycles" in contract.detail


def test_unknown_contract_version_fails_closed_before_consulting_the_runner(
    tmp_path: Path,
) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    shared = dataclasses.replace(
        _shared(), fix=dataclasses.replace(_shared().fix, contract="factory-fix/999")
    )
    diagnostics = check_readiness(local, shared)
    contract = next(d for d in diagnostics if d.name == "fix workflow contract")
    assert contract.available is False
    assert "packaged" in contract.detail


def test_launch_requires_a_sandbox_script_that_contains_the_credential(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    launch = next(d for d in diagnostics if d.name == "fix sandbox launch")
    assert launch.available is False
    assert "sandbox-run.sh" in launch.detail

    script = checkout / "scripts" / "sandbox-run.sh"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\n# --image --artifact-dir --no-default-secrets --env-file --docker-run-arg\n"
    )
    git(checkout, "add", ".")
    git(
        checkout,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "launcher",
    )
    git(checkout, "update-ref", "refs/remotes/origin/main", "HEAD")
    with mock.patch(
        "agent_factory.work_kinds.pull_request.readiness.shutil.which", return_value="/bin/docker"
    ):
        diagnostics = check_readiness(local, _shared())
    launch = next(d for d in diagnostics if d.name == "fix sandbox launch")
    assert launch.available is True, launch.detail


def test_present_workflow_contract_passes(tmp_path: Path) -> None:
    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    diagnostics = check_readiness(local, _shared())
    contract = next(d for d in diagnostics if d.name == "fix workflow contract")
    assert contract.available is True, contract.detail
    assert "packaged" in contract.detail


def test_handler_readiness_uses_attached_installation_token(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=shared-token\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = PullRequestHandler(FIX, _shared(), local)
    handler.attach_installation_token(lambda: "shared-token")
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False


def test_handler_readiness_fails_closed_when_the_token_provider_fails(tmp_path: Path) -> None:
    from agent_factory.github import GitHubApiError
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = PullRequestHandler(FIX, _shared(), local)

    def _broken() -> str:
        raise GitHubApiError("cannot mint")

    handler.attach_installation_token(_broken)
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False
    assert "cannot mint" in credential.detail


def test_handler_readiness_fails_closed_on_unexpected_provider_errors(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    checkout = _runner_checkout(tmp_path, with_contract=True)
    env = tmp_path / "fix.env"
    env.write_text("GH_TOKEN=abc123\n")
    env.chmod(0o600)
    local = _local(tmp_path, checkout, fix_environment=env)
    handler = PullRequestHandler(FIX, _shared(), local)

    def _broken() -> str:
        raise RuntimeError("transport wrapper exploded")

    handler.attach_installation_token(_broken)
    credential = next(d for d in handler.readiness(local, _shared()) if d.name == "fix credential")
    assert credential.available is False
    assert "transport wrapper exploded" in credential.detail


def _check_plugin_installed(output: str, plugin_name: str, *, json_format: bool) -> bool:
    from agent_factory.work_kinds.pull_request.readiness import (  # noqa: PLC0415
        _plugin_installed,  # pyright: ignore[reportPrivateUsage]
    )

    return _plugin_installed(output, plugin_name, json_format=json_format)


def _check_role_cli_diagnostics(shared: SharedConfig) -> list[Diagnostic]:
    from agent_factory.operations import configured_adapters  # noqa: PLC0415
    from agent_factory.work_kinds.pull_request.readiness import (  # noqa: PLC0415
        _role_cli_diagnostic,  # pyright: ignore[reportPrivateUsage]
    )

    return [_role_cli_diagnostic(adapter) for adapter in configured_adapters(shared)]


def test_plugin_installed_rejects_a_bare_substring_match_in_text_output() -> None:
    # "codagent-extra" contains "codagent" as a substring but is a different plugin.
    assert _check_plugin_installed("codagent-extra 1.0.0\n", "codagent", json_format=False) is False
    # Marketplace/help text mentioning the plugin name is not an installed-plugin record.
    assert (
        _check_plugin_installed(
            "Run `agent plugin install codagent@codagent` to add it.\n",
            "codagent",
            json_format=False,
        )
        is False
    )


def test_plugin_installed_accepts_an_exact_line_entry() -> None:
    assert _check_plugin_installed("codagent\n", "codagent", json_format=False) is True
    assert (
        _check_plugin_installed("- codagent@codagent  1.2.0\n", "codagent", json_format=False)
        is True
    )
    assert (
        _check_plugin_installed("other-plugin\ncodagent\n", "codagent", json_format=False) is True
    )


def test_plugin_installed_accepts_the_claude_cli_glyph_bullet() -> None:
    # The Claude CLI marks each installed plugin with a "❯" bullet; without it in the
    # strip set the token became the glyph itself and every fix claim was gated off.
    listing = "Installed plugins:\n\n  ❯ codagent@codagent\n    Version: 0.11.0\n"
    assert _check_plugin_installed(listing, "codagent", json_format=False) is True
    # The glyph must not turn a different plugin into a match.
    assert _check_plugin_installed("  ❯ codagent-extra@x\n", "codagent", json_format=False) is False


def test_plugin_installed_json_requires_exact_name_or_id() -> None:
    assert (
        _check_plugin_installed('[{"name": "codagent-extra"}]', "codagent", json_format=True)
        is False
    )
    assert _check_plugin_installed('[{"name": "codagent"}]', "codagent", json_format=True) is True
    assert (
        _check_plugin_installed(
            '{"plugins": [{"id": "codagent@codagent"}]}', "codagent", json_format=True
        )
        is True
    )
    assert _check_plugin_installed("not json", "codagent", json_format=True) is False


def test_role_cli_diagnostics_fails_when_plugin_only_mentioned_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = dataclasses.replace(
        _shared(), fix=dataclasses.replace(_shared().fix, defaults={"lead": "claude:profile:high"})
    )

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "claude" else None

    def fake_run(
        command: list[str], *, capture_output: bool, text: bool, check: bool, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if list(command[:2]) == ["claude", "auth"]:
            return subprocess.CompletedProcess(command, 0, "logged in", "")
        if list(command[:2]) == ["claude", "plugin"]:
            return subprocess.CompletedProcess(
                command, 0, "Install codagent-extra or codagent-pro.\n", ""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("agent_factory.work_kinds.pull_request.readiness.shutil.which", fake_which)
    monkeypatch.setattr("agent_factory.work_kinds.pull_request.readiness.subprocess.run", fake_run)

    diagnostics = _check_role_cli_diagnostics(shared)
    claude = next(d for d in diagnostics if d.name == "fix host claude CLI")
    assert claude.available is False
    assert "not list an installed codagent plugin" in claude.detail


def test_role_cli_diagnostics_passes_with_authenticated_installed_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = dataclasses.replace(
        _shared(), fix=dataclasses.replace(_shared().fix, defaults={"lead": "claude:profile:high"})
    )

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "claude" else None

    def fake_run(
        command: list[str], *, capture_output: bool, text: bool, check: bool, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if list(command[:2]) == ["claude", "auth"]:
            return subprocess.CompletedProcess(command, 0, "logged in", "")
        if list(command[:2]) == ["claude", "plugin"]:
            return subprocess.CompletedProcess(command, 0, "codagent\n", "")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("agent_factory.work_kinds.pull_request.readiness.shutil.which", fake_which)
    monkeypatch.setattr("agent_factory.work_kinds.pull_request.readiness.subprocess.run", fake_run)

    diagnostics = _check_role_cli_diagnostics(shared)
    claude = next(d for d in diagnostics if d.name == "fix host claude CLI")
    assert claude.available is True


# -- host mode (INT-003, fix-host group) --------------------------------------------------

_RUNNER_OK = """#!/bin/sh
case "$1" in
  -version) echo "stub-runner 1.2.3" ;;
  -validate) exit 0 ;;
  run) echo "Usage: agent-runner run <workflow> [--session-dir <path>]" ;;
  *) exit 1 ;;
esac
"""
_RUNNER_NO_SESSION_DIR = _RUNNER_OK.replace(" [--session-dir <path>]", "")
_RUNNER_REJECTS_WORKFLOW = _RUNNER_OK.replace(
    "-validate) exit 0", "-validate) echo bad >&2; exit 1"
)
_RUNNER_REJECTS_REVIEW_WORKFLOW = _RUNNER_OK.replace(
    "-validate) exit 0",
    '-validate) case "$2" in *factory-review-v1.0.yaml) echo bad-review >&2; exit 1;; esac',
)
_GH_OK = (
    '#!/bin/sh\nif [ "$1 $2" = "auth status" ] && [ -n "${GH_TOKEN:-}" ]; then exit 0; fi\nexit 1\n'
)
_GH_AUTH_FAILS = "#!/bin/sh\nexit 1\n"
_AGENT_OK = """#!/bin/sh
case "$1 $2 $3" in
  "status  ") echo "Logged in as paul" ;;
  "plugin marketplace list") printf 'cursor-public  global\\ncodagent  user  https://github.com/Codagent-AI/agent-skills\\n' ;;
  *) exit 1 ;;
esac
"""
_AGENT_NOT_LOGGED_IN = _AGENT_OK.replace(
    '"status  ") echo "Logged in as paul"', '"status  ") echo "Not logged in" >&2; exit 1'
)
_AGENT_NO_CODAGENT = _AGENT_OK.replace(
    "codagent  user  https://github.com/Codagent-AI/agent-skills",
    "and-scene  user  https://example.invalid/and-scene",
)
_SETTINGS_OK = "theme: light\nautonomous_backend: headless\nsetup:\n    completed_at: 2026-05-24T13:56:56Z\nautonomous_permission_mode: yolo\n"
_SETTINGS_NOT_YOLO = _SETTINGS_OK.replace("yolo", "ask")
_TRIVIAL = "#!/bin/sh\nexit 0\n"


def _host_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    executables: dict[str, str | None] | None = None,
    settings: str | None = _SETTINGS_OK,
) -> None:
    """A PATH holding only stub executables and a HOME holding only Runner settings."""
    bin_dir = tmp_path / "host-bin"
    bin_dir.mkdir(exist_ok=True)
    scripts: dict[str, str | None] = {
        "agent-runner": _RUNNER_OK,
        "gh": _GH_OK,
        "agent": _AGENT_OK,
        "git": _TRIVIAL,
        "jq": _TRIVIAL,
        "python3": _TRIVIAL,
        "agent-validator": _TRIVIAL,
        **(executables or {}),
    }
    for name, text in scripts.items():
        path = bin_dir / name
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.write_text(text)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    home = tmp_path / "host-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    settings_path = home / ".agent-runner" / "settings.yaml"
    settings_path.unlink(missing_ok=True)
    if settings is not None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(settings)


def _host_local(tmp_path: Path, *, fix_environment: Path | None) -> LocalConfig:
    # The recorded Runner checkout path does not exist: host readiness must never consult it.
    base = _local(tmp_path, tmp_path / "no-such-runner-checkout", fix_environment=fix_environment)
    (tmp_path / "storage").mkdir(exist_ok=True)
    return dataclasses.replace(base, fix=dataclasses.replace(base.fix, execution="host"))


def _host_shared() -> SharedConfig:
    shared = _shared()
    return dataclasses.replace(
        shared,
        fix=dataclasses.replace(
            shared.fix,
            defaults={
                "lead": "cursor:m:high",
                "implementor": "cursor:m:medium",
                "tester": "cursor:m:low",
            },
        ),
    )


def _host_credential(tmp_path: Path) -> Path:
    credential = tmp_path / "fix.env"
    credential.write_text("GH_TOKEN=fix-token-value\n")
    credential.chmod(0o600)
    return credential


def test_host_readiness_passes_with_every_prerequisite_and_never_reads_the_runner_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_environment(tmp_path, monkeypatch)
    diagnostics = check_readiness(
        _host_local(tmp_path, fix_environment=_host_credential(tmp_path)),
        _host_shared(),
        installation_token="app-token",
    )
    failures = [(d.name, d.detail) for d in diagnostics if not d.available]
    assert failures == []
    assert {d.group for d in diagnostics} == {"fix-host"}
    names = {d.name for d in diagnostics}
    assert "fix sandbox launch" not in names
    assert "fix workflow contract" in names
    assert "fix host cursor CLI" in names


@pytest.mark.parametrize(
    ("executables", "settings", "failing", "detail"),
    [
        ({"agent-runner": None}, _SETTINGS_OK, "fix host agent-runner on PATH", "not on PATH"),
        (
            {"agent-runner": _RUNNER_NO_SESSION_DIR},
            _SETTINGS_OK,
            "fix host agent-runner --session-dir support",
            "--session-dir",
        ),
        (
            {"agent-runner": _RUNNER_REJECTS_WORKFLOW},
            _SETTINGS_OK,
            "fix host workflow validation",
            "rejected packaged factory-fix-v1.0.yaml",
        ),
        (
            {"agent-runner": _RUNNER_REJECTS_REVIEW_WORKFLOW},
            _SETTINGS_OK,
            "fix host workflow validation",
            "rejected packaged factory-review-v1.0.yaml",
        ),
        (
            {"gh": _GH_AUTH_FAILS},
            _SETTINGS_OK,
            "fix host gh auth status",
            "failed with the configured fix credential",
        ),
        ({"jq": None}, _SETTINGS_OK, "fix host jq on PATH", "jq is not on PATH"),
        ({"agent": None}, _SETTINGS_OK, "fix host cursor CLI", "agent is not on PATH"),
        (
            {"agent": _AGENT_NOT_LOGGED_IN},
            _SETTINGS_OK,
            "fix host cursor CLI",
            "agent status failed",
        ),
        (
            {"agent": _AGENT_NO_CODAGENT},
            _SETTINGS_OK,
            "fix host cursor CLI",
            "does not list an installed codagent",
        ),
        (
            {},
            _SETTINGS_NOT_YOLO,
            "fix host Runner user settings",
            "autonomous_permission_mode='ask'",
        ),
        ({}, None, "fix host Runner user settings", "settings are unavailable"),
    ],
)
def test_host_readiness_fails_closed_on_each_missing_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    executables: dict[str, str | None],
    settings: str | None,
    failing: str,
    detail: str,
) -> None:
    _host_environment(tmp_path, monkeypatch, executables=executables, settings=settings)
    diagnostics = check_readiness(
        _host_local(tmp_path, fix_environment=_host_credential(tmp_path)),
        _host_shared(),
        installation_token="app-token",
    )
    failed = [d for d in diagnostics if not d.available]
    assert any(d.name == failing for d in failed), [(d.name, d.detail) for d in failed]
    diagnostic = next(d for d in failed if d.name == failing)
    assert detail in diagnostic.detail
    assert diagnostic.action
    assert diagnostic.group == "fix-host"


def test_host_readiness_rejects_a_fix_credential_equal_to_the_installation_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_environment(tmp_path, monkeypatch)
    diagnostics = check_readiness(
        _host_local(tmp_path, fix_environment=_host_credential(tmp_path)),
        _host_shared(),
        installation_token="fix-token-value",
    )
    credential = next(d for d in diagnostics if d.name == "fix credential")
    assert credential.available is False
    assert credential.group == "fix-host"


def test_host_readiness_holds_admission_in_the_runtime_without_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host_environment(tmp_path, monkeypatch, executables={"agent-runner": _RUNNER_NO_SESSION_DIR})
    handler = PullRequestHandler(
        FIX, _host_shared(), _host_local(tmp_path, fix_environment=_host_credential(tmp_path))
    )
    diagnostics = handler.readiness(handler._local, handler._shared)  # pyright: ignore[reportPrivateUsage]
    assert any(not d.available and "--session-dir" in d.detail for d in diagnostics)


def test_runner_user_settings_parses_top_level_scalars_only() -> None:
    from agent_factory.work_kinds.pull_request.readiness import runner_user_settings

    parsed = runner_user_settings(
        _SETTINGS_OK + "onboarding:\n    dismissed: 2026-05-29\n# comment\nquoted: 'x'\n"
    )
    assert parsed["autonomous_backend"] == "headless"
    assert parsed["autonomous_permission_mode"] == "yolo"
    assert parsed["quoted"] == "x"
    assert "completed_at" not in parsed and "dismissed" not in parsed
