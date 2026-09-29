"""INT-004: launch lease, timeout, adoption, dead sessions, and probe errors."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agent_factory.config import (
    CredentialsConfig,
    LimitsConfig,
    LocalConfig,
    RepositoryConfig,
    ScheduleConfig,
)
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import ProcessProbeError, process_start_identity
from agent_factory.watch import store as watch_store
from agent_factory.watch import supervise


def test_launch_lease_expires_without_process(tmp_path: Path) -> None:
    local = LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        old = datetime.now(UTC) - timedelta(minutes=3)
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id="r",
            repository="o/r",
            issue_number=1,
            pr_number=None,
            pr_url=None,
            event_at=old.isoformat(),
            now=old.isoformat(),
        )
        row = watch_store.rows(store)[0]
        assert watch_store.claim_launch(
            store, row["id"], old, 90, "claude:m:e", str(tmp_path / "E")
        )
        supervise.supervise(store, local, tmp_path / "local.toml")
        ended = watch_store.get(store, row["id"])
        assert ended is not None
        assert ended["state"] == "launch-failed"
        assert watch_store.running_count(store) == 0
        assert "alert" in watch_store.json_field(ended, "deliveries_json")
    finally:
        store.close()


def _local(tmp_path: Path) -> LocalConfig:
    return LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )


def _launched(
    store: ClaimStore,
    tmp_path: Path,
    key: str,
    *,
    launched: datetime | None = None,
    timeout_minutes: int = 90,
    identity: dict[str, object] | None = None,
) -> dict[str, Any]:
    claim = store.create_claim(ClaimDraft("o/r", 7, "I", f"P-{key}", "fix", f"fp-{key}", {}))
    at = launched or datetime.now(UTC)
    watch_store.insert(
        store,
        event_key=f"FAILURE:{key}",
        event_kind="FAILURE",
        claim_id=claim.id,
        run_id=None,
        repository="o/r",
        issue_number=7,
        pr_number=None,
        pr_url=None,
        event_at=at.isoformat(),
        now=at.isoformat(),
    )
    row = next(r for r in watch_store.rows(store) if r["event_key"] == f"FAILURE:{key}")
    evidence = tmp_path / "E" / key
    evidence.mkdir(parents=True)
    assert watch_store.claim_launch(
        store, row["id"], at, timeout_minutes, "claude:m:e", str(evidence)
    )
    if identity is not None:
        watch_store.update(store, row["id"], process_json=json.dumps(identity))
    saved = watch_store.get(store, row["id"])
    assert saved is not None
    return saved


def _dead_identity() -> dict[str, object]:
    finished = subprocess.Popen(["/usr/bin/true"])
    finished.wait()
    return {"pid": finished.pid, "start": "Mon Jan  1 00:00:00 2001"}


_TRIAGE = {
    "procedure": "triage",
    "cause": "registry propagation",
    "evidence": ["HTTP 400 failed to get manifest"],
    "owner": "transient",
    "retry": "succeeded",
    "actions": [],
    "issues_filed": [],
    "issues_updated": [],
    "paused_by_session": False,
    "resumed_by_session": False,
    "next_step": "none",
}


def _state(store: ClaimStore, row: dict[str, Any]) -> dict[str, Any]:
    saved = watch_store.get(store, row["id"])
    assert saved is not None
    return saved


def test_valid_result_completes_even_with_nonzero_exit(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        row = _launched(store, tmp_path, "valid", identity=_dead_identity())
        evidence = Path(row["evidence_path"])
        (evidence / "watch-result.json").write_text(json.dumps(_TRIAGE))
        (evidence / "exit.json").write_text('{"code": 1, "finished_at": "x"}')
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        ended = _state(store, row)
        assert ended["state"] == "completed"
        assert ended["detail"] == "exit code 1"
        deliveries = watch_store.json_field(ended, "deliveries_json")
        assert set(deliveries) == {"triage"}
    finally:
        store.close()


def test_live_session_with_valid_result_completes_at_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        row = _launched(
            store,
            tmp_path,
            "result-before-deadline",
            launched=datetime.now(UTC) - timedelta(minutes=5),
            timeout_minutes=1,
            identity={"pid": 12345, "start": "known"},
        )
        (Path(row["evidence_path"]) / "watch-result.json").write_text(json.dumps(_TRIAGE))
        terminated: list[object] = []

        def alive(_identity: object) -> str:
            return "alive"

        monkeypatch.setattr(supervise, "process_identity_status", alive)
        monkeypatch.setattr(supervise, "terminate_owned_process", terminated.append)
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        ended = _state(store, row)
        assert ended["state"] == "completed"
        assert ended["detail"] == "session deadline exceeded after result"
        assert set(watch_store.json_field(ended, "deliveries_json")) == {"triage"}
        assert watch_store.json_field(ended, "audit_json") == {"outcome": "missing"}
        assert len(terminated) == 1
    finally:
        store.close()


def test_dead_session_without_result_is_interrupted_and_alerted(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        missing = _launched(store, tmp_path, "missing", identity=_dead_identity())
        invalid = _launched(store, tmp_path, "invalid", identity=_dead_identity())
        invalid_evidence = Path(invalid["evidence_path"])
        (invalid_evidence / "watch-result.json").write_text('{"procedure": "review"}')
        (invalid_evidence / "exit.json").write_text('{"code": 0, "finished_at": "x"}')
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        assert _state(store, missing)["state"] == "interrupted"
        assert _state(store, missing)["detail"] == "no exit record"
        assert _state(store, invalid)["state"] == "interrupted"
        assert _state(store, invalid)["detail"].startswith("invalid result")
        for row in (missing, invalid):
            ended = _state(store, row)
            assert set(watch_store.json_field(ended, "deliveries_json")) == {"alert"}
            assert watch_store.json_field(ended, "usage_json")["estimated_cost_usd"] is None
            assert watch_store.json_field(ended, "audit_json") == {"outcome": "missing"}
        assert watch_store.running_count(store) == 0
    finally:
        store.close()


def _wait(predicate: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.mark.darwin
def test_timeout_terminates_the_whole_session_group(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    grandchild_file = tmp_path / "grandchild.pid"
    session_process = subprocess.Popen(
        ["/bin/bash", "-c", f"sleep 60 & echo $! > {grandchild_file}; wait"],
        start_new_session=True,
    )
    try:
        assert _wait(grandchild_file.is_file)
        grandchild = int(grandchild_file.read_text())
        start = process_start_identity(session_process.pid)
        assert start is not None
        row = _launched(
            store,
            tmp_path,
            "timeout",
            launched=datetime.now(UTC) - timedelta(minutes=5),
            timeout_minutes=1,
            identity={"pid": session_process.pid, "start": start},
        )
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        ended = _state(store, row)
        assert ended["state"] == "timed-out"
        assert set(watch_store.json_field(ended, "deliveries_json")) == {"alert"}
        session_process.wait(timeout=5)
        assert _wait(lambda: process_start_identity(grandchild) is None)
    finally:
        if session_process.poll() is None:
            session_process.kill()
        store.close()


@pytest.mark.darwin
def test_live_session_is_adopted_from_its_pid_file(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    live = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        row = _launched(store, tmp_path, "adopt", launched=datetime.now(UTC) - timedelta(minutes=5))
        (Path(row["evidence_path"]) / "pid").write_text(f"{live.pid}\n")
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        adopted = _state(store, row)
        assert adopted["state"] == "launched"
        assert watch_store.json_field(adopted, "process_json")["pid"] == live.pid
    finally:
        live.kill()
        live.wait()
        store.close()


def test_probe_error_leaves_row_launched_and_other_rows_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        unknown = _launched(store, tmp_path, "a-unknown")
        (Path(unknown["evidence_path"]) / "pid").write_text("12345\n")
        finished = _launched(store, tmp_path, "b-finished", identity=_dead_identity())
        finished_evidence = Path(finished["evidence_path"])
        (finished_evidence / "watch-result.json").write_text(json.dumps(_TRIAGE))
        (finished_evidence / "exit.json").write_text('{"code": 0, "finished_at": "x"}')

        def failing_probe(pid: int) -> str | None:
            raise ProcessProbeError("ps unavailable")

        monkeypatch.setattr(supervise, "process_start_identity", failing_probe)
        supervise.supervise(store, _local(tmp_path), tmp_path / "local.toml")
        assert _state(store, unknown)["state"] == "launched"
        assert _state(store, finished)["state"] == "completed"
    finally:
        store.close()
