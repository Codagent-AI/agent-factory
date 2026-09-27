from __future__ import annotations

import sqlite3
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


# INT-007: status summarises terminal-claim cleanup from recorded estimates only.

GIB = 2**30


def _terminal_history(
    tmp_path: Path, *, measured: int, unmeasured: int, pruned: int = 28
) -> tuple[ClaimStore, list[str]]:
    """Pending and pruned cancelled claims; the first two pending ones are failing."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    pending: list[str] = []
    for number in range(measured + unmeasured + pruned):
        claim = store.create_claim(
            ClaimDraft("example/work", number, f"I{number}", f"P{number}", "eval", "x", {})
        )
        run = store.reserve_run(
            claim.id, "rep-1", reason="initial", evidence_path=str(unreadable / claim.id)
        )
        store.finish_run(run.id, execution_status="completed", result={})
        store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
        cleanup: dict[str, object] = {"complete": True, "terminal_observed_at": "2026-09-01"}
        if number < measured:
            cleanup["size_estimate"] = {"bytes": GIB // 2, "measured_at": "2026-09-02"}
        if number >= measured + unmeasured:
            cleanup["retention"] = {"pruned_at": "2026-09-05", "removed": [], "errors": []}
            cleanup["size_estimate"] = {"bytes": 0, "measured_at": "2026-09-05"}
        else:
            pending.append(claim.id)
        store.set_cleanup(claim.id, cleanup)
    store.set_cleanup(
        pending[0],
        {
            **_cleanup(store, pending[0]),
            "retention": {"removed": [], "errors": [{"path": "/x", "error": "busy"}]},
        },
    )
    store.set_cleanup(
        pending[1],
        {
            **_cleanup(store, pending[1]),
            "fly_image": {"state": "failed", "error": "HTTP 500", "persistent": False},
        },
    )
    # Older than the post-run audit window, which reads recent attempts' audit records;
    # the cleanup summary itself must never touch the unreadable evidence.
    with sqlite3.connect(tmp_path / "state.sqlite3") as connection:
        connection.execute("UPDATE run SET finished_at = '2026-01-01T00:00:00+00:00'")
    unreadable.chmod(0)
    return store, pending


def _cleanup(store: ClaimStore, claim_id: str) -> dict[str, object]:
    claim = store.get_claim(claim_id)
    assert claim is not None
    return dict(claim.cleanup)


def _status_of(store: ClaimStore, tmp_path: Path) -> str:
    from agent_factory.operations import status

    try:
        return status(store)
    finally:
        (tmp_path / "unreadable").chmod(0o700)


def test_status_summarises_a_partly_measured_cleanup_backlog(tmp_path: Path) -> None:
    store, _ = _terminal_history(tmp_path, measured=7, unmeasured=5)

    output = _status_of(store, tmp_path)

    summary = [line for line in output.splitlines() if line.startswith("cleanup:")]
    assert summary == [
        "cleanup: 12 claims pending (~3.5 GiB across 7 measured, 5 not yet measured), "
        "28 pruned, 2 failing"
    ]
    listed = [line for line in output.splitlines() if line.startswith("claim:")]
    assert listed == ["claim: example/work#0 (cancelled)", "claim: example/work#1 (cancelled)"]
    assert "busy" in output
    assert "HTTP 500" in output


def test_status_shows_only_the_sum_when_every_pending_claim_is_measured(tmp_path: Path) -> None:
    store, _ = _terminal_history(tmp_path, measured=4, unmeasured=0, pruned=1)

    output = _status_of(store, tmp_path)

    assert "cleanup: 4 claims pending (~2.0 GiB), 1 pruned, 2 failing" in output


def test_status_says_when_no_pending_claim_is_measured(tmp_path: Path) -> None:
    store, _ = _terminal_history(tmp_path, measured=0, unmeasured=3, pruned=0)

    output = _status_of(store, tmp_path)

    assert "cleanup: 3 claims pending (size not yet measured), 0 pruned, 2 failing" in output
