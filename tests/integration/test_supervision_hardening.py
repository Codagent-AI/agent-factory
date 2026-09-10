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

    def no_cycle(_state: Path, _config: Path) -> None:
        pass

    monkeypatch.setattr("agent_factory.runtime.cycle", no_cycle)

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


def test_late_container_is_discovered_when_wrapper_exits(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    from agent_factory import supervisor

    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    store = ClaimStore(tmp_path / "state.sqlite3")
    run = store.reserve_run(_claim(store), "rep-1", reason="initial", evidence_path=str(artifact))
    store.mark_running(run.id, {"pid": 123, "start": "x"})
    plan = ExecutionPlan((), str(tmp_path), {}, (), (), {"suite": "and-scene"}, False)
    identity_states = iter(["alive", "missing", "missing"])

    def identity_status(_identity: object) -> str:
        return next(identity_states)

    monkeypatch.setattr(supervisor, "_identity_status", identity_status)
    discovered: dict[str, object] = {
        "id": "owned",
        "image": "sha256:image",
        "artifact_path": str(artifact),
    }
    discoveries = iter([None, discovered])

    def discover(_artifact: str) -> dict[str, object] | None:
        result = next(discoveries)
        if result is not None:
            store.request_cancellation(run.id)
        return result

    monkeypatch.setattr(supervisor, "discover_container", discover)

    def inspection(_container: str) -> dict[str, object]:
        return {
            "Id": "owned",
            "Image": "sha256:image",
            "State": {"Running": True},
            "Mounts": [{"Source": str(artifact), "Destination": "/artifacts"}],
        }

    monkeypatch.setattr(supervisor, "inspect_container", inspection)
    stopped: list[object] = []

    def terminate(_identity: object, container: object) -> bool:
        stopped.append(container)
        return True

    monkeypatch.setattr(supervisor, "_terminate_execution", terminate)
    supervisor._observe(  # pyright: ignore[reportPrivateUsage]
        store,
        run.id,
        plan,
        SupervisionLimits(10, 10, 10),
        {"pid": 123, "start": "x"},
    )
    finished = store.get_run(run.id)
    assert finished is not None and finished.status == "cancelled"
    assert stopped == [discovered]
    store.close()


def test_container_discovery_batches_inspection_and_filters_source(tmp_path: Path) -> None:
    import json
    from unittest.mock import patch

    from agent_factory.supervisor import discover_container

    observed = [
        {
            "Id": name,
            "Image": "sha256:image",
            "Mounts": [
                {"Destination": "/artifacts", "Source": str(source)},
            ],
        }
        for name, source in [("decoy", tmp_path / "other"), ("owned", tmp_path)]
    ]
    with patch(
        "agent_factory.supervisor.subprocess.run",
        side_effect=[
            subprocess.CompletedProcess([], 0, "decoy\nowned\n", ""),
            subprocess.CompletedProcess([], 0, json.dumps(observed), ""),
        ],
    ) as commands:
        found = discover_container(str(tmp_path))
    assert found is not None and found["id"] == "owned"
    assert commands.call_args_list[1].args[0] == ["docker", "inspect", "decoy", "owned"]


def test_unchanged_quota_log_is_not_reparsed_each_poll(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    from agent_factory import supervisor

    store = ClaimStore(tmp_path / "state.sqlite3")
    run = store.reserve_run(_claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(run.id, {"pid": 123, "start": "x"})
    plan = ExecutionPlan((), str(tmp_path), {}, (), (), {"suite": "and-scene"}, False)
    states = iter(["alive", "alive", "alive", "missing"])

    def identity(_identity: object) -> str:
        return next(states)

    def discover(_artifact: str) -> None:
        return None

    reads: list[float] = []

    def quota(_artifact: Path, *, now: float) -> None:
        reads.append(now)
        return None

    monkeypatch.setattr(supervisor, "_identity_status", identity)
    monkeypatch.setattr(supervisor, "discover_container", discover)
    monkeypatch.setattr(supervisor, "bounded_quota_deadline", quota)
    supervisor._observe(  # pyright: ignore[reportPrivateUsage]
        store,
        run.id,
        plan,
        SupervisionLimits(10, 10, 10),
        {},
    )
    assert len(reads) == 1
    store.close()
