from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pytest import MonkeyPatch

from agent_factory import cli
from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import (
    SupervisionLimits,
    launch_supervisor,
    process_start_identity,
    resume_supervisor,
    supervise,
)


def _claim(store: ClaimStore) -> str:
    return store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "x", {})).id


def _plan(tmp_path: Path, program: str) -> ExecutionPlan:
    script = tmp_path / "suite.py"
    script.write_text(program, encoding="utf-8")
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    return ExecutionPlan(
        (sys.executable, str(script), str(artifact)),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(artifact)},
        False,
    )


def test_tick_replaces_only_running_or_observing_watchers(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = _claim(store)
    reserved = store.reserve_run(claim, "rep-1", reason="initial", evidence_path="/tmp/reserved")
    store.finish_run(reserved.id, execution_status="failed", result={})
    running = store.reserve_run(claim, "rep-1", reason="recovery", evidence_path="/tmp/running")
    store.mark_running(running.id, {"pid": 0, "start": "unknown"})
    calls: list[tuple[str, Path | None]] = []

    def record_resume(path: Path, run_id: str, *, config_path: Path | None = None) -> None:
        assert path == state
        calls.append((run_id, config_path))

    monkeypatch.setattr(cli, "resume_supervisor", record_resume)

    cli._tick(state, tmp_path / "local.toml")  # pyright: ignore[reportPrivateUsage]

    assert calls == [(running.id, tmp_path / "local.toml")]


def test_resume_does_not_spawn_a_second_live_watcher(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(_claim(store), "rep-1", reason="initial", evidence_path="/tmp/evidence")
    watcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        start = process_start_identity(watcher.pid)
        assert start is not None
        store.mark_running(run.id, {"pid": watcher.pid, "start": start})
        assert resume_supervisor(state, run.id) is None
    finally:
        watcher.terminate()
        watcher.wait(timeout=5)


def test_valid_result_waits_for_owned_process_exit(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "e")
    )
    plan = _plan(
        tmp_path,
        "import json, pathlib, sys, time\n"
        "artifact = pathlib.Path(sys.argv[1])\n"
        "(artifact / 'result.json').write_text(json.dumps({'evaluation_status': 'completed'}))\n"
        "(artifact / 'ready').touch()\n"
        "time.sleep(.4)\n",
    )
    watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(10, 10, 10))
    ready = tmp_path / "artifact" / "ready"
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()
    time.sleep(0.05)
    assert store.get_run(run.id).status == "running"  # type: ignore[union-attr]
    watcher.wait(timeout=3)
    assert store.get_run(run.id).status == "completed"  # type: ignore[union-attr]


def test_invalid_result_is_preserved_as_failed_execution(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "e")
    )
    plan = _plan(
        tmp_path,
        "import pathlib, sys\npathlib.Path(sys.argv[1], 'result.json').write_text('{not json')\n",
    )
    watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(10, 10, 10))
    watcher.wait(timeout=3)
    finished = store.get_run(run.id)
    assert finished is not None and finished.status == "failed"
    assert finished.result["reason"] == "invalid result.json"


def test_invalid_persisted_plan_is_reported_without_crashing_watcher(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(_claim(store), "rep-1", reason="initial", evidence_path="/tmp/evidence")
    error: RuntimeError | None = None
    try:
        supervise(state, run.id, run.launch_nonce)
    except RuntimeError as caught:
        error = caught

    assert error is None
    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "observing"
    assert saved.result["reason"] == "invalid persisted plan"
