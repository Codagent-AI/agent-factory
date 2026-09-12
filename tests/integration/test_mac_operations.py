from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from agent_factory import operations
from agent_factory.cli import _poll_seconds  # pyright: ignore[reportPrivateUsage]
from agent_factory.config import LocalConfig
from agent_factory.store import ClaimDraft, ClaimStore


def _local_config(root: Path, shared: Path) -> Path:
    path = root / "local.toml"
    path.write_text(
        f'''\
shared_config = "{shared}"
storage_root = "{root / "factory"}"

[repositories]
agent_evals = "{root / "missing-evals"}"
agent_runner = "{root / "missing-runner"}"
agent_skills = "{root / "missing-skills"}"

[schedule]
timezone = "UTC"
poll_minutes = 5
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 999999
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "{root / "missing-app.pem"}"
suite_environment = "{root / "missing-suite.env"}"
''',
        encoding="utf-8",
    )
    return path


def test_local_config_uses_portable_root_and_default_operational_limits(tmp_path: Path) -> None:
    config = LocalConfig.from_file(_local_config(tmp_path, tmp_path / "missing-shared.toml"))

    assert config.state_path == (tmp_path / "factory" / "state.sqlite3").resolve()
    assert config.log_path == (tmp_path / "factory" / "logs" / "controller.log").resolve()
    assert config.schedule.poll_seconds == 300
    assert config.limits.inactivity_seconds == 1800


def test_cli_doctor_is_read_only_and_status_explains_persisted_pause_and_holds(
    tmp_path: Path,
) -> None:
    config_path = _local_config(tmp_path, tmp_path / "missing-shared.toml")
    config = LocalConfig.from_file(config_path)
    store = ClaimStore(config.state_path)
    claim = store.create_claim(
        ClaimDraft("example/evals", 7, "I7", "P7", "eval", "request", {"settings": {}})
    )
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/tmp/evidence")
    store.set_paused(True)
    store.set_hold(claim.id, "readiness", {"reason": "Docker is unavailable"})
    store.record_delivery_failure(claim.id, "handoff", RuntimeError("network unavailable"))
    store.close()

    command = [sys.executable, "-m", "agent_factory.cli", "--config", str(config_path)]
    doctor = subprocess.run([*command, "doctor"], check=False, capture_output=True, text=True)
    assert doctor.returncode == 1
    assert "shared configuration: FAIL" in doctor.stdout
    assert "starts no evaluations" in doctor.stdout

    status = subprocess.run([*command, "status"], check=False, capture_output=True, text=True)
    assert status.returncode == 0
    assert "paused: true" in status.stdout
    assert "current: example/evals#7 rep-1 (reserved)" in status.stdout
    assert "readiness: Docker is unavailable" in status.stdout
    assert "unfinished reporting: handoff" in status.stdout
    assert ClaimStore(config.state_path).get_run(run.id) is not None


def _shared_config_text(*, eval_table: str) -> str:
    return f"""\
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
running = "running-option"
review = "review-option"
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"
human = "human-option"

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

{eval_table}
"""


def test_doctor_reports_resolved_harness_branch_without_fetching(tmp_path: Path) -> None:
    evals = tmp_path / "evals"
    evals.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(evals)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(evals),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "one",
        ],
        check=True,
    )
    origin = tmp_path / "evals-origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(evals), str(origin)], check=True)
    subprocess.run(["git", "-C", str(evals), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(evals), "fetch", "-q", "origin"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(evals), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(
        _shared_config_text(
            eval_table='[eval]\nharness_ref = "main"\nsuite = "and-scene"\nrepetitions = 3\n'
        )
    )
    local_config = _local_config(tmp_path, shared_path)
    text = local_config.read_text().replace(str(tmp_path / "missing-evals"), str(evals))
    local_config.write_text(text)
    config = LocalConfig.from_file(local_config)

    diagnostics = operations.doctor(config)
    harness = next(d for d in diagnostics if d.name == "shared configuration")
    assert harness.available is True
    assert harness.detail == f"harness branch main → {sha}"


def test_doctor_reports_obsolete_harness_sha_configuration(tmp_path: Path) -> None:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(
        _shared_config_text(
            eval_table='[eval]\nharness_sha = "' + "a" * 40 + '"\nsuite = "and-scene"\n'
            "repetitions = 3\n"
        )
    )
    config = LocalConfig.from_file(_local_config(tmp_path, shared_path))

    diagnostics = operations.doctor(config)
    harness = next(d for d in diagnostics if d.name == "shared configuration")
    assert harness.available is False
    assert "harness_ref" in harness.detail


def test_launch_agent_template_renders_explicit_paths_and_is_valid_plist(tmp_path: Path) -> None:
    from agent_factory.operations import render_launch_agent

    rendered = render_launch_agent(
        Path("/opt/agent-factory/.venv/bin/agent-factory"),
        tmp_path / "config.toml",
        tmp_path / "factory",
        tmp_path / "factory" / "logs" / "controller.log",
        tmp_path / "credentials" / "github-app.pem",
    )
    target = tmp_path / "com.codagent.agent-factory.plist"
    target.write_text(rendered, encoding="utf-8")
    checked = subprocess.run(
        ["plutil", "-lint", str(target)], check=False, capture_output=True, text=True
    )

    assert checked.returncode == 0, checked.stderr
    assert "/opt/agent-factory/.venv/bin/agent-factory" in rendered
    assert "--config" in rendered
    assert "RunAtLoad" in rendered and "KeepAlive" in rendered


def test_doctor_helpers_report_local_io_and_spawn_failures(tmp_path: Path) -> None:
    environment = tmp_path / "suite.env"
    environment.write_text("CANDIDATE_TOKEN=value\n", encoding="utf-8")
    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        suite = operations._suite_environment(environment)  # pyright: ignore[reportPrivateUsage]
    assert not suite.available
    assert "unreadable" in suite.detail

    not_a_file = tmp_path / "credentials"
    not_a_file.mkdir()
    private = operations._private_file("key", not_a_file)  # pyright: ignore[reportPrivateUsage]
    assert not private.available
    assert "regular file" in private.detail

    with patch("agent_factory.operations.subprocess.run", side_effect=PermissionError("denied")):
        command = operations._command_check(  # pyright: ignore[reportPrivateUsage]
            "Docker", ("docker", "info"), "Start Docker."
        )
    assert not command.available
    assert "could not run" in command.detail


def test_doctor_reports_disk_probe_failure_without_aborting(tmp_path: Path) -> None:
    config = LocalConfig.from_file(_local_config(tmp_path, tmp_path / "shared.toml"))
    with patch("agent_factory.operations.shutil.disk_usage", side_effect=OSError("stale mount")):
        storage = operations._free_space(config)  # pyright: ignore[reportPrivateUsage]

    assert not storage.available
    assert "cannot inspect" in storage.detail


def test_explicit_resident_poll_override_wins_over_local_schedule(tmp_path: Path) -> None:
    config = LocalConfig.from_file(_local_config(tmp_path, tmp_path / "shared.toml"))

    assert _poll_seconds(1.5, config) == 1.5  # pyright: ignore[reportPrivateUsage]
    assert _poll_seconds(None, config) == 300  # pyright: ignore[reportPrivateUsage]


def test_local_config_reports_malformed_timezone_as_configuration_error(tmp_path: Path) -> None:
    import pytest

    from agent_factory.config import ConfigurationError

    text = _local_config(tmp_path, tmp_path / "shared.toml").read_text()
    for timezone in ("/UTC", "../UTC", "America/../New_York"):
        with pytest.raises(ConfigurationError, match="schedule.timezone"):
            LocalConfig.from_toml(text.replace('timezone = "UTC"', f'timezone = "{timezone}"'))


def _fix_shared_config_text(*, credential: Path | None = None) -> str:
    return _shared_config_text(
        eval_table='[eval]\nharness_ref = "main"\nsuite = "and-scene"\nrepetitions = 3\n'
    ) + (
        '\n[fix]\ncontract = "factory-fix/1"\n'
        '[fix.branches]\nrunner = "main"\n'
        '[[fix.targets]]\nrepository = "example/work"\n'
    )


def test_doctor_reports_fix_diagnostics_in_a_separate_section(tmp_path: Path) -> None:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    config = LocalConfig.from_file(_local_config(tmp_path, shared_path))

    diagnostics = operations.doctor(config)

    fix_names = [d.name for d in diagnostics if d.name.startswith("fix ")]
    assert fix_names
    text = operations.format_doctor(diagnostics)
    assert "-- fix --" in text
    assert text.index("-- fix --") > text.index("shared configuration")


def test_doctor_reports_missing_working_clone(tmp_path: Path) -> None:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    local_config_path = _local_config(tmp_path, shared_path)
    text = local_config_path.read_text()
    text += f'\n[repositories.working_clones]\n"example/work" = "{tmp_path / "missing-clone"}"\n'
    local_config_path.write_text(text)
    config = LocalConfig.from_file(local_config_path)

    diagnostics = operations.doctor(config)

    clone = next(d for d in diagnostics if d.name == "fix working clone example/work")
    assert clone.available is False
    assert "missing-clone" in clone.detail


def test_doctor_reports_available_working_clone(tmp_path: Path) -> None:
    clone_path = tmp_path / "clone"
    clone_path.mkdir()
    subprocess.run(["git", "init", "-q", str(clone_path)], check=True)
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    local_config_path = _local_config(tmp_path, shared_path)
    text = local_config_path.read_text()
    text += f'\n[repositories.working_clones]\n"example/work" = "{clone_path}"\n'
    local_config_path.write_text(text)
    config = LocalConfig.from_file(local_config_path)

    diagnostics = operations.doctor(config)

    clone = next(d for d in diagnostics if d.name == "fix working clone example/work")
    assert clone.available is True


def test_doctor_reports_mirror_not_yet_created(tmp_path: Path) -> None:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    config = LocalConfig.from_file(_local_config(tmp_path, shared_path))

    diagnostics = operations.doctor(config)

    mirror = next(d for d in diagnostics if d.name == "fix mirror example/work")
    assert mirror.available is False
    assert "not yet created" in mirror.detail


def test_doctor_identity_checks_report_authentication_and_org_role(tmp_path: Path) -> None:
    credential = tmp_path / "fix.env"
    credential.write_text("GH_TOKEN=fix-token\n", encoding="utf-8")
    credential.chmod(0o600)
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    local_config_path = _local_config(tmp_path, shared_path)
    text = local_config_path.read_text()
    text += f'\nfix_environment = "{credential}"\n'
    local_config_path.write_text(text)
    config = LocalConfig.from_file(local_config_path)

    class StubRunner:
        def run(
            self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
        ) -> str:
            assert environment == {"GH_TOKEN": "fix-token"}
            if arguments == ["api", "user"]:
                return '{"login": "fix-machine-user"}'
            if arguments[1].startswith("orgs/"):
                return '{"role": "admin"}'
            if arguments[1] == "repos/example/work":
                return '{"full_name": "example/work"}'
            raise AssertionError(f"unexpected gh invocation: {arguments}")

    with patch("agent_factory.operations.SubprocessGhRunner", StubRunner):
        diagnostics = operations.doctor(config)

    identity = next(d for d in diagnostics if d.name == "fix credential identity")
    assert identity.available is True
    assert "fix-machine-user" in identity.detail
    not_app = next(d for d in diagnostics if d.name == "fix credential is not the App identity")
    assert not_app.available is True
    admin = next(d for d in diagnostics if d.name == "fix credential organization role")
    assert admin.available is True
    assert "admin" in admin.detail
    reach = next(d for d in diagnostics if d.name == "fix credential reach example/work")
    assert reach.available is True


def test_doctor_identity_check_fails_when_credential_is_the_app_identity(tmp_path: Path) -> None:
    credential = tmp_path / "fix.env"
    credential.write_text("GH_TOKEN=fix-token\n", encoding="utf-8")
    credential.chmod(0o600)
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    local_config_path = _local_config(tmp_path, shared_path)
    text = local_config_path.read_text()
    text += f'\nfix_environment = "{credential}"\n'
    local_config_path.write_text(text)
    config = LocalConfig.from_file(local_config_path)

    class StubRunner:
        def run(
            self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
        ) -> str:
            if arguments == ["api", "user"]:
                return '{"login": "example-factory[bot]"}'
            if arguments[1].startswith("orgs/"):
                return '{"role": "member"}'
            return '{"full_name": "example/work"}'

    with patch("agent_factory.operations.SubprocessGhRunner", StubRunner):
        diagnostics = operations.doctor(config)

    not_app = next(d for d in diagnostics if d.name == "fix credential is not the App identity")
    assert not_app.available is False


def test_doctor_excludes_fix_diagnostics_from_the_shared_prerequisite_gate(
    tmp_path: Path,
) -> None:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(_fix_shared_config_text())
    config = LocalConfig.from_file(_local_config(tmp_path, shared_path))

    diagnostics = operations.doctor(config, include_fix=False)

    assert not any(d.name.startswith("fix ") for d in diagnostics)


def test_doctor_gives_docker_and_authentication_time_to_complete(tmp_path: Path) -> None:
    config = LocalConfig.from_file(_local_config(tmp_path, tmp_path / "shared.toml"))
    commands: list[tuple[tuple[str, ...], float]] = []

    def delayed_check(
        command: tuple[str, ...], *, capture_output: bool, check: bool, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append((command, timeout))
        if timeout < 10:
            raise subprocess.TimeoutExpired(command, timeout)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    with patch("agent_factory.operations.subprocess.run", side_effect=delayed_check):
        diagnostics = operations.doctor(config)
        diagnostics.extend(operations.model_authentication({"lead": "codex:test:high"}))

    relevant = [d for d in diagnostics if d.name in {"Docker", "codex model authentication"}]
    assert len(relevant) >= 2
    assert all(d.available for d in relevant)
    assert all(10 <= timeout <= 60 for command, timeout in commands if command[0] != "cursor")
