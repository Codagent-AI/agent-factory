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
