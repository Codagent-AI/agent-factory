"""Docker ownership through a launcher restart, using test-owned containers."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent_factory.backends.docker import discover_container
from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, supervise
from agent_factory.supervisor import (
    _plan_document as plan_document,  # pyright: ignore[reportPrivateUsage]
)


def _docker(*args: str) -> str:
    result = subprocess.run(("docker", *args), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.docker
def test_container_outlives_launcher_and_decoy_is_untouched(tmp_path: Path) -> None:
    if subprocess.run(("docker", "info"), capture_output=True, timeout=10).returncode != 0:
        pytest.skip("Docker daemon unavailable")
    evidence = tmp_path / "evidence"
    decoy_path = tmp_path / "decoy"
    evidence.mkdir()
    decoy_path.mkdir()
    owned = _docker(
        "run", "-d", "--rm", "-v", f"{evidence}:/artifacts", "alpine:latest", "sleep", "60"
    )
    decoy = _docker(
        "run", "-d", "--rm", "-v", f"{decoy_path}:/artifacts", "alpine:latest", "sleep", "60"
    )
    try:
        state = tmp_path / "state.sqlite3"
        store = ClaimStore(state)
        claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
        run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
        plan = ExecutionPlan(
            ("/bin/true",),
            str(tmp_path),
            {},
            (),
            (),
            {"artifact_path": str(evidence), "sandbox": "docker"},
            False,
        )
        store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
        assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
        record = discover_container(str(evidence))
        assert record is not None and record["id"] == owned
        store.update_progress(run.id, {"container": record})

        watcher = threading.Thread(target=supervise, args=(state, run.id, run.launch_nonce))
        watcher.start()
        time.sleep(0.2)
        assert store.get_run(run.id).status == "running"  # type: ignore[union-attr]
        (evidence / "result.json").write_text(json.dumps({"evaluation_status": "completed"}))
        _docker("stop", owned)
        watcher.join(timeout=15)
        assert not watcher.is_alive()
        assert store.get_run(run.id).status == "completed"  # type: ignore[union-attr]
        assert _docker("inspect", "--format", "{{.State.Running}}", decoy) == "true"
    finally:
        subprocess.run(("docker", "rm", "-f", owned, decoy), capture_output=True, timeout=20)


@pytest.mark.docker
def test_changed_container_identity_is_held_without_stopping_it(tmp_path: Path) -> None:
    if subprocess.run(("docker", "info"), capture_output=True, timeout=10).returncode != 0:
        pytest.skip("Docker daemon unavailable")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    owned = _docker(
        "run", "-d", "--rm", "-v", f"{evidence}:/artifacts", "alpine:latest", "sleep", "60"
    )
    try:
        state = tmp_path / "state.sqlite3"
        store = ClaimStore(state)
        claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
        run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(evidence))
        plan = ExecutionPlan(
            ("/bin/true",),
            str(tmp_path),
            {},
            (),
            (),
            {"artifact_path": str(evidence), "sandbox": "docker"},
            False,
        )
        store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
        assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
        record = discover_container(str(evidence))
        assert record is not None
        store.update_progress(run.id, {"container": {**record, "image": "another image"}})

        supervise(state, run.id, run.launch_nonce)

        saved = store.get_run(run.id)
        assert saved is not None and saved.status == "observing"
        assert saved.result["uncertain"] is True
        assert _docker("inspect", "--format", "{{.State.Running}}", owned) == "true"
    finally:
        subprocess.run(("docker", "rm", "-f", owned), capture_output=True, timeout=20)
