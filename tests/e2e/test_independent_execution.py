"""Process-boundary coverage for E2E-002's controlled-suite substitute."""

from __future__ import annotations

import subprocess
import sys
import time
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import AndSceneAdapter, PreparedWorktrees, SourceRepositories
from agent_factory.supervisor import (
    SupervisionLimits,
    container_matches_recorded_ownership,
    launch_supervisor,
    resume_supervisor,
)


def test_appending_nested_suite_log_prevents_false_inactivity_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifacts"
    logs = artifact / "logs"
    logs.mkdir(parents=True)
    log = logs / "agent-runner.log"
    log.write_text("started\n", encoding="utf-8")
    directory_version = logs.stat().st_mtime_ns
    program = tmp_path / "progressing_suite.py"
    program.write_text(
        "import json, pathlib, sys, time\n"
        "artifact = pathlib.Path(sys.argv[1])\n"
        "log = artifact / 'logs/agent-runner.log'\n"
        "for _ in range(20):\n"
        "    with log.open('a') as stream: stream.write('progress\\n')\n"
        "    time.sleep(.1)\n"
        "(artifact / 'result.json').write_text(json.dumps({'evaluation_status': 'completed'}))\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.sqlite3"
    with closing(ClaimStore(state)) as store:
        run = store.reserve_run(
            _claim(store), "rep-1", reason="initial", evidence_path=str(artifact)
        )
        adapter = AndSceneAdapter(environment_file=tmp_path / "candidate.env")

        # Repository/model readiness is unrelated to this real process test.
        def ready(_worktrees: PreparedWorktrees) -> None:
            return None

        monkeypatch.setattr(adapter, "readiness", ready)
        repositories = SourceRepositories(tmp_path, tmp_path, tmp_path)
        worktrees = PreparedWorktrees("claim", tmp_path, tmp_path, tmp_path, repositories)
        frozen = {
            "suite": "and-scene",
            "settings": {
                "roles": dict.fromkeys(("lead", "implementor", "tester"), "codex:test:high")
            },
        }
        plan = adapter.plan(frozen, worktrees, artifact, recovery=False)
        plan = replace(
            plan,
            argv=(sys.executable, str(program), str(artifact)),
            working_directory=str(tmp_path),
        )
        watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(0.7, 10, 10))
        watcher.wait(timeout=8)
        finished = store.get_run(run.id)
        assert finished is not None and finished.status == "completed"
        assert logs.stat().st_mtime_ns == directory_version


def test_nested_agent_session_updates_prevent_false_inactivity_timeout(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts"
    session_root = artifact / ".runtime" / "agent-session-state"
    session_file = session_root / "cursor" / "chats" / "project" / "chat" / "store.db-wal"
    session_file.parent.mkdir(parents=True)
    session_file.write_text("started\n", encoding="utf-8")
    program = tmp_path / "session_progressing_suite.py"
    program.write_text(
        "import json, pathlib, sys, time\n"
        "session_file = pathlib.Path(sys.argv[1])\n"
        "result = pathlib.Path(sys.argv[2])\n"
        "for _ in range(20):\n"
        "    with session_file.open('a') as stream: stream.write('progress\\n')\n"
        "    time.sleep(.1)\n"
        "result.write_text(json.dumps({'evaluation_status': 'completed'}))\n",
        encoding="utf-8",
    )
    state = tmp_path / "state.sqlite3"
    with closing(ClaimStore(state)) as store:
        run = store.reserve_run(
            _claim(store), "rep-1", reason="initial", evidence_path=str(artifact)
        )
        plan = ExecutionPlan(
            (sys.executable, str(program), str(session_file), str(artifact / "result.json")),
            str(tmp_path),
            {},
            (),
            (f"glob:{session_root}/cursor/chats/*/*/store.db*",),
            {"artifact_path": str(artifact)},
            False,
        )

        watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(1.3, 10, 10))
        watcher.wait(timeout=8)

        finished = store.get_run(run.id)
        assert finished is not None and finished.status == "completed"


def _claim(store: ClaimStore) -> str:
    return store.create_claim(
        ClaimDraft("example/evals", 1, "I1", "P1", "eval", "request", {"settings": {}})
    ).id


def _plan(tmp_path: Path, marker: Path, *, evaluation_status: str = "completed") -> ExecutionPlan:
    program = tmp_path / "controlled_suite.py"
    program.write_text(
        "import pathlib, sys, time\n"
        "marker = pathlib.Path(sys.argv[1])\n"
        "marker.write_text(str(__import__('os').getpid()))\n"
        "while not marker.with_suffix('.done').exists(): time.sleep(.02)\n"
        f"result = {{'evaluation_status': '{evaluation_status}', "
        "'product_verdict': 'ready-for-human-review'}\n"
        "pathlib.Path(sys.argv[2]).write_text(__import__('json').dumps(result))\n",
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    return ExecutionPlan(
        (sys.executable, str(program), str(marker), str(artifacts / "result.json")),
        str(tmp_path),
        {},
        (),
        (str(marker),),
        {"artifact_path": str(artifacts)},
        False,
    )


def _wait_for(path: Path) -> None:
    end = time.monotonic() + 5
    while not path.exists() and time.monotonic() < end:
        time.sleep(0.02)
    assert path.exists()


@pytest.mark.darwin
def test_e2e_002_supervisor_survives_launcher_and_recovers_completion(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "a")
    )
    marker = tmp_path / "started"
    supervisor = launch_supervisor(
        state,
        run.id,
        _plan(tmp_path, marker),
        SupervisionLimits(inactivity_seconds=10, execution_seconds=10, total_seconds=10),
    )
    _wait_for(marker)
    # The independently sessioned supervisor is not tied to this test's controller/store object.
    store.close()
    restarted = ClaimStore(state)
    active = restarted.get_run(run.id)
    assert active is not None and active.status == "running"
    assert active.process.get("pid") == int(marker.read_text(encoding="utf-8"))

    marker.with_suffix(".done").touch()
    supervisor.wait(timeout=5)
    finished = restarted.get_run(run.id)
    assert finished is not None and finished.status == "completed"
    assert finished.result["product_verdict"] == "ready-for-human-review"


def test_timeout_and_cancellation_only_signal_verified_owned_child(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "a")
    )
    marker = tmp_path / "started"
    supervisor = launch_supervisor(
        state,
        run.id,
        _plan(tmp_path, marker),
        SupervisionLimits(inactivity_seconds=0.15, execution_seconds=10, total_seconds=10),
    )
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        _wait_for(marker)
        supervisor.wait(timeout=5)
        finished = store.get_run(run.id)
        assert finished is not None and finished.status == "timed_out"
        assert finished.result["timeout"] == "inactivity"
        assert decoy.poll() is None
    finally:
        decoy.terminate()
        decoy.wait(timeout=5)


def test_cancellation_request_is_observed_without_touching_a_decoy(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "a")
    )
    marker = tmp_path / "started"
    supervisor = launch_supervisor(
        state,
        run.id,
        _plan(tmp_path, marker),
        SupervisionLimits(inactivity_seconds=10, execution_seconds=10, total_seconds=10),
    )
    decoy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"])
    try:
        _wait_for(marker)
        store.request_cancellation(run.id)
        supervisor.wait(timeout=5)
        finished = store.get_run(run.id)
        assert finished is not None and finished.status == "cancelled"
        assert decoy.poll() is None
    finally:
        decoy.terminate()
        decoy.wait(timeout=5)


def test_suite_reported_technical_failure_is_not_recorded_as_product_completion(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    run = store.reserve_run(
        _claim(store), "rep-1", reason="initial", evidence_path=str(tmp_path / "a")
    )
    marker = tmp_path / "started"
    supervisor = launch_supervisor(
        state,
        run.id,
        _plan(tmp_path, marker, evaluation_status="failed"),
        SupervisionLimits(inactivity_seconds=10, execution_seconds=10, total_seconds=10),
    )
    _wait_for(marker)
    marker.with_suffix(".done").touch()
    supervisor.wait(timeout=5)
    finished = store.get_run(run.id)
    assert finished is not None and finished.status == "failed"


def test_container_termination_requires_recorded_id_image_and_exact_artifact_mount(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    recorded = {"id": "abc", "image": "sha256:immutable", "artifact_path": str(artifact)}
    inspect = {
        "Id": "abc",
        "Image": "sha256:immutable",
        "Mounts": [{"Source": str(artifact.resolve()), "Destination": "/artifacts"}],
    }
    assert container_matches_recorded_ownership(recorded, inspect)
    assert not container_matches_recorded_ownership(recorded, {**inspect, "Id": "other"})
    assert not container_matches_recorded_ownership(
        recorded, {**inspect, "Mounts": [{"Source": "/tmp/other", "Destination": "/artifacts"}]}
    )


@pytest.mark.parametrize(
    "restart,total_seconds,expected",
    [
        (False, 4, "completed"),
        (True, 4, "completed"),
        (False, 0.8, "timed_out"),
    ],
)
def test_bounded_claude_wait_does_not_consume_execution_or_idle_budget(
    tmp_path: Path,
    restart: bool,
    total_seconds: float,
    expected: str,
) -> None:
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    program = tmp_path / "waiting_suite.py"
    program.write_text(
        "import datetime, json, pathlib, sys, time\n"
        "reset = (datetime.datetime.now(datetime.UTC) "
        "+ datetime.timedelta(seconds=2)).isoformat()\n"
        "print(f'Claude implementor quota reached; waiting until {reset} '"
        "'before resuming Agent Runner', flush=True)\n"
        "time.sleep(1.5)\n"
        "(pathlib.Path(sys.argv[1]) / 'result.json').write_text("
        "json.dumps({'evaluation_status': 'complete'}))\n"
    )
    state = tmp_path / "state.sqlite3"
    with closing(ClaimStore(state)) as store:
        run = store.reserve_run(
            _claim(store), "rep-1", reason="initial", evidence_path=str(artifact)
        )
        plan = ExecutionPlan(
            (sys.executable, str(program), str(artifact)),
            str(tmp_path),
            {},
            (),
            (str(artifact / "factory-suite.log"),),
            {"artifact_path": str(artifact), "suite": "and-scene"},
            False,
        )
        watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(0.5, 0.7, total_seconds))
        if restart:
            time.sleep(0.5)
            watcher.kill()
            watcher.wait(timeout=5)
            replacement = resume_supervisor(state, run.id)
            assert replacement is not None
            watcher = replacement
        watcher.wait(timeout=8)
        finished = store.get_run(run.id)
        assert finished is not None and finished.status == expected
        if expected == "timed_out":
            assert finished.result["timeout"] == "total"


def _fix_claim(store: ClaimStore) -> str:
    return store.create_claim(
        ClaimDraft("example/work", 2, "I2", "P2", "fix", "fix:example/work#2", {"kind": "fix"})
    ).id


@pytest.mark.darwin
def test_e2e_003_two_slots_survive_a_controller_restart_and_refuse_seconds(tmp_path: Path) -> None:
    """E2E-003: an eval and a fix run together under separate supervisors."""
    from agent_factory.store import NonterminalRunError

    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    eval_claim = _claim(store)
    fix_claim = _fix_claim(store)
    eval_run = store.reserve_run(
        eval_claim, "rep-1", reason="initial", evidence_path=str(tmp_path / "eval")
    )
    fix_run = store.reserve_run(
        fix_claim, "fix", reason="initial", evidence_path=str(tmp_path / "fix")
    )
    # A second attempt of either kind is refused while its slot is held; the other kind's
    # slot is independent.
    with pytest.raises(NonterminalRunError):
        store.reserve_run(_claim(store), "rep-1", reason="initial", evidence_path="/tmp/x")
    with pytest.raises(NonterminalRunError):
        store.reserve_run(_fix_claim(store), "fix", reason="initial", evidence_path="/tmp/y")
    eval_marker = tmp_path / "eval-started"
    fix_marker = tmp_path / "fix-started"
    eval_dir = tmp_path / "eval-plan"
    fix_dir = tmp_path / "fix-plan"
    eval_dir.mkdir()
    fix_dir.mkdir()
    limits = SupervisionLimits(inactivity_seconds=10, execution_seconds=10, total_seconds=10)
    eval_supervisor = launch_supervisor(state, eval_run.id, _plan(eval_dir, eval_marker), limits)
    fix_supervisor = launch_supervisor(state, fix_run.id, _plan(fix_dir, fix_marker), limits)
    _wait_for(eval_marker)
    _wait_for(fix_marker)
    active = store.nonterminal_runs()
    assert {run.kind for run in active} == {"eval", "fix"}
    assert len({run.supervisor.get("pid") for run in active}) == 2
    assert eval_supervisor.pid != fix_supervisor.pid
    # Controller restart: the store handle goes away; the supervisors do not.
    store.close()
    restarted = ClaimStore(state)
    for run_id, marker in ((eval_run.id, eval_marker), (fix_run.id, fix_marker)):
        observed = restarted.get_run(run_id)
        assert observed is not None and observed.status == "running"
        assert observed.process.get("pid") == int(marker.read_text(encoding="utf-8"))
    eval_marker.with_suffix(".done").touch()
    fix_marker.with_suffix(".done").touch()
    eval_supervisor.wait(timeout=5)
    fix_supervisor.wait(timeout=5)
    for run_id, claim_id in ((eval_run.id, eval_claim), (fix_run.id, fix_claim)):
        finished = restarted.get_run(run_id)
        assert finished is not None and finished.status == "completed"
        assert finished.claim_id == claim_id
        assert len(restarted.runs_for_claim(claim_id)) == 1
    assert not restarted.nonterminal_runs()
    restarted.close()
