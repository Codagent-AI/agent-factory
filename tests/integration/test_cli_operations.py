from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore


def test_cli_doctor_reports_a_broken_local_configuration_instead_of_exiting(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text("this is not valid toml [[[", encoding="utf-8")

    command = [sys.executable, "-m", "agent_factory.cli", "--config", str(config_path)]
    doctor = subprocess.run([*command, "doctor"], check=False, capture_output=True, text=True)

    assert doctor.returncode == 1
    assert "-- shared --" in doctor.stdout
    assert "local configuration: FAIL" in doctor.stdout
    assert "Traceback" not in doctor.stderr


def test_cli_tick_still_exits_at_startup_on_invalid_local_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text("this is not valid toml [[[", encoding="utf-8")

    command = [sys.executable, "-m", "agent_factory.cli", "--config", str(config_path)]
    tick = subprocess.run([*command, "tick"], check=False, capture_output=True, text=True)

    assert tick.returncode != 0
    assert "invalid local configuration" in tick.stderr


def test_cli_doctor_reports_unsupported_eval_host_execution(tmp_path: Path) -> None:
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        f'''\
shared_config = "{tmp_path / "missing-shared.toml"}"
storage_root = "{tmp_path / "factory"}"

[repositories]
agent_evals = "{tmp_path / "missing-evals"}"
agent_runner = "{tmp_path / "missing-runner"}"
agent_skills = "{tmp_path / "missing-skills"}"

[schedule]
timezone = "UTC"
poll_minutes = 5
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "{tmp_path / "missing-app.pem"}"
suite_environment = "{tmp_path / "missing-suite.env"}"

[eval]
execution = "host"
''',
        encoding="utf-8",
    )

    command = [sys.executable, "-m", "agent_factory.cli", "--config", str(config_path)]
    doctor = subprocess.run([*command, "doctor"], check=False, capture_output=True, text=True)
    assert doctor.returncode == 1
    assert "local configuration: FAIL" in doctor.stdout
    assert "eval host execution" in doctor.stdout

    tick = subprocess.run([*command, "tick"], check=False, capture_output=True, text=True)
    assert tick.returncode != 0
    assert "invalid local configuration" in tick.stderr


def test_cli_pause_resume_and_status_use_durable_state(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/evals", 7, "I7", "P7", "eval", "x", {}))
    store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/tmp/evidence")
    store.close()

    command = [sys.executable, "-m", "agent_factory.cli", "--state", str(state)]
    assert subprocess.run([*command, "pause"], check=False).returncode == 0
    paused = subprocess.run([*command, "status"], check=False, capture_output=True, text=True)
    assert paused.returncode == 0
    assert "paused: true" in paused.stdout
    assert "rep-1" in paused.stdout
    assert subprocess.run([*command, "resume"], check=False).returncode == 0
    resumed = subprocess.run([*command, "status"], check=False, capture_output=True, text=True)
    assert "paused: false" in resumed.stdout


def test_cli_status_all_lists_every_saved_claim(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/work", 9, "I9", "P9", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/evidence")
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "failed"})
    store.set_cleanup(claim.id, {"review_observed": True, "complete": True})
    store.close()

    command = [sys.executable, "-m", "agent_factory.cli", "--state", str(state)]
    default = subprocess.run([*command, "status"], check=False, capture_output=True, text=True)
    assert "example/work#9" not in default.stdout

    everything = subprocess.run(
        [*command, "status", "--all"], check=False, capture_output=True, text=True
    )
    assert everything.returncode == 0
    assert "example/work#9" in everything.stdout


def test_cli_all_flag_is_rejected_on_commands_other_than_status(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    ClaimStore(state).close()
    command = [sys.executable, "-m", "agent_factory.cli", "--state", str(state)]
    tick = subprocess.run([*command, "tick", "--all"], check=False, capture_output=True, text=True)
    assert tick.returncode != 0
