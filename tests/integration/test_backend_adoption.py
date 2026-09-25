from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, process_start_identity, supervise
from agent_factory.supervisor import (
    _plan_document as plan_document,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.darwin
@pytest.mark.parametrize(
    ("filename", "payload", "status"),
    [
        ("result.json", {"evaluation_status": "completed"}, "completed"),
        ("fix-outcome.json", {"contract": "factory-fix/1", "outcome": "pull-request"}, "completed"),
        ("fix-outcome.json", {"contract": "wrong", "outcome": "pull-request"}, "interrupted"),
        (None, None, "interrupted"),
    ],
)
def test_replacement_watcher_settles_gone_host_execution(
    tmp_path: Path, filename: str | None, payload: dict[str, str] | None, status: str
) -> None:
    state = tmp_path / "state.sqlite3"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    plan = ExecutionPlan(
        ("/bin/true",),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(evidence), "sandbox": "host"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
    if filename is not None:
        (evidence / filename).write_text(json.dumps(payload))

    supervise(state, run.id, run.launch_nonce)

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == status
    if status == "interrupted":
        assert saved.result["reason"] == "execution ended while unsupervised"


@pytest.mark.darwin
def test_replacement_watcher_holds_reused_host_pid(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path))
    plan = ExecutionPlan(
        ("/bin/true",),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(tmp_path), "sandbox": "host"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(
        run.id,
        launch_nonce=run.launch_nonce,
        supervisor={},
        process={"pid": os.getpid(), "start": "an older process"},
    )

    supervise(state, run.id, run.launch_nonce)

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "observing"
    assert saved.result["uncertain"] is True


@pytest.mark.darwin
def test_replacement_watcher_continues_a_live_host_process(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    script = (
        "import json,pathlib,sys,time; "
        "time.sleep(.3); "
        "(pathlib.Path(sys.argv[1])/'result.json').write_text(json.dumps({'evaluation_status':'completed'}))"
    )
    plan = ExecutionPlan(
        (sys.executable, "-c", script, str(evidence)),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(evidence), "sandbox": "host"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    child = subprocess.Popen(plan.argv, start_new_session=True)
    try:
        deadline = time.monotonic() + 2
        start = process_start_identity(child.pid)
        while start is None and time.monotonic() < deadline:
            time.sleep(0.01)
            start = process_start_identity(child.pid)
        assert start is not None
        assert store.begin_run(
            run.id,
            launch_nonce=run.launch_nonce,
            supervisor={},
            process={"pid": child.pid, "start": start},
        )
        watcher = threading.Thread(target=supervise, args=(state, run.id, run.launch_nonce))
        watcher.start()
        child.wait(timeout=3)
        watcher.join(timeout=3)
        assert not watcher.is_alive()
        saved = store.get_run(run.id)
        assert saved is not None and saved.status == "completed"
        assert saved.progress["backend"] == "host"
        assert len(store.runs_for_claim(claim.id)) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
