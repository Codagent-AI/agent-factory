from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_factory.backends import Probe
from agent_factory.controller import ExecutionPlan
from agent_factory.fly.backend import FlyMachineBackend
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import (
    ResultRead,
    SupervisionLimits,
    launch_supervisor,
    process_start_identity,
    resume_supervisor,
    supervise,
)
from agent_factory.supervisor import (
    _plan_document as plan_document,  # pyright: ignore[reportPrivateUsage]
)
from agent_factory.work_kinds.fix.handler import FixHandler


@pytest.mark.darwin
@pytest.mark.parametrize(
    ("filename", "payload", "status", "kind"),
    [
        ("result.json", {"evaluation_status": "completed"}, "completed", "fix"),
        (
            "fix-outcome.json",
            {"contract": "factory-fix/1", "outcome": "pull-request"},
            "completed",
            "fix",
        ),
        (
            "fix-outcome.json",
            {"contract": "wrong", "outcome": "pull-request"},
            "interrupted",
            "fix",
        ),
        (
            "feature-outcome.json",
            {"contract": "wrong", "outcome": "pull-request"},
            "observing",
            "feature",
        ),
        (
            "feature-outcome.json",
            {"contract": "factory-feature/1", "outcome": "wrong"},
            "observing",
            "feature",
        ),
        (None, None, "interrupted", "fix"),
    ],
)
def test_replacement_watcher_settles_gone_host_execution(
    tmp_path: Path, filename: str | None, payload: dict[str, str] | None, status: str, kind: str
) -> None:
    state = tmp_path / "state.sqlite3"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", kind, "x", {}))
    run = store.reserve_run(claim.id, kind, reason="initial", evidence_path=str(evidence))
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
    if status == "observing":
        assert saved.result["uncertain"] is True


@pytest.mark.darwin
def test_replacement_watcher_holds_unreadable_feature_outcome(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "feature", "x", {}))
    run = store.reserve_run(claim.id, "feature", reason="initial", evidence_path=str(evidence))
    plan = ExecutionPlan(
        ("/bin/true",),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(evidence), "backend": "host"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
    (evidence / "feature-outcome.json").write_text("{")

    supervise(state, run.id, run.launch_nonce)

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "observing"
    assert "feature-outcome.json" in str(saved.result["reason"])
    assert "JSON" in str(saved.result["reason"])


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
        assert saved is not None and saved.status == "completed", saved.result if saved else None
        assert saved.progress["backend"] == "host"
        assert len(store.runs_for_claim(claim.id)) == 1
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)


@pytest.mark.darwin
def test_replacement_watcher_records_unsupervised_fly_loss(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "eval", "x", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    plan = ExecutionPlan(
        ("run.sh",),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(tmp_path), "backend": "fly-machine"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
    with (
        patch.object(FlyMachineBackend, "identity_from_plan", return_value={"id": "gone"}),
        patch.object(FlyMachineBackend, "adopt", return_value=Probe("gone")),
        patch.object(FlyMachineBackend, "provenance", return_value={}),
    ):
        supervise(state, run.id, run.launch_nonce)

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "interrupted"
    assert saved.result["reason"] == "execution ended while unsupervised"


@pytest.mark.darwin
@pytest.mark.parametrize("writes_outcome", [False, True])
def test_real_watcher_loss_settles_and_applies_one_recovery_retry(
    tmp_path: Path, writes_outcome: bool
) -> None:
    state = tmp_path / "state.sqlite3"
    evidence = tmp_path / "evidence"
    attempt = evidence / "attempt-1"
    attempt.mkdir(parents=True)
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
    script = (
        "import pathlib,sys,time; "
        "root=pathlib.Path(sys.argv[1]); (root/'ready').touch(); time.sleep(.6); "
        "(root/'fix-outcome.json').write_text("
        '\'{"contract":"factory-fix/1","outcome":"pull-request"}\') '
        "if sys.argv[2]=='yes' else None"
    )
    plan = ExecutionPlan(
        (sys.executable, "-c", script, str(attempt), "yes" if writes_outcome else "no"),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(attempt), "backend": "host", "sandbox": "host"},
        False,
    )
    watcher = launch_supervisor(state, run.id, plan, SupervisionLimits(5, 5, 5))
    try:
        deadline = time.monotonic() + 3
        while not (attempt / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (attempt / "ready").exists()
        active = store.get_run(run.id)
        assert active is not None and active.status == "running"
        watcher.terminate()
        watcher.wait(timeout=3)
        time.sleep(0.7)

        replacement = resume_supervisor(state, run.id)
        assert replacement is not None
        replacement.wait(timeout=5)

        settled = store.get_run(run.id)
        assert settled is not None
        assert settled.status == ("completed" if writes_outcome else "interrupted")
        assert len(store.runs_for_claim(claim.id)) == 1
        handler = object.__new__(FixHandler)
        current_claim = store.get_claim(claim.id)
        assert current_claim is not None
        next_unit, reason = handler.next_unit(current_claim, store.runs_for_claim(claim.id))
        if writes_outcome:
            assert (next_unit, reason) == (None, "initial")
        else:
            assert settled.result["reason"] == "execution ended while unsupervised"
            assert (next_unit, reason) == ("fix", "recovery")
            assert next_unit is not None
            retry = store.reserve_run(
                claim.id, next_unit, reason=reason, evidence_path=str(evidence)
            )
            store.finish_run(retry.id, execution_status="interrupted", result={})
            assert handler.next_unit(current_claim, store.runs_for_claim(claim.id))[0] is None
    finally:
        if watcher.poll() is None:
            watcher.terminate()
            watcher.wait(timeout=3)


@pytest.mark.darwin
def test_observer_rereads_result_after_process_disappears(tmp_path: Path) -> None:
    from agent_factory import supervisor

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(run.id, {})
    plan = ExecutionPlan(("/bin/true",), str(tmp_path), {}, (), (), {"backend": "host"}, False)
    reads = [ResultRead(None), ResultRead({"evaluation_status": "completed"})]
    with patch.object(supervisor, "_load_result", side_effect=reads):
        supervisor._observe(store, run.id, plan, SupervisionLimits(5, 5, 5), {})  # pyright: ignore[reportPrivateUsage]

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "completed"
