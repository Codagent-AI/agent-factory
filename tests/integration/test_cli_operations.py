from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore


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
