"""Legacy persisted plans retain their execution owner across a deploy."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from agent_factory.backends import Probe
from agent_factory.backends.docker import DockerContainerBackend
from agent_factory.backends.resolve import backend_for
from agent_factory.config import LocalConfig
from agent_factory.controller import AttemptResult, ExecutionPlan
from agent_factory.fly.backend import FlyMachineBackend
from agent_factory.operations import status
from agent_factory.runtime import (
    _dispose_result,  # pyright: ignore[reportPrivateUsage]
    _reconcile_backends,  # pyright: ignore[reportPrivateUsage]
)
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, supervise
from agent_factory.supervisor import (
    _plan_document as plan_document,  # pyright: ignore[reportPrivateUsage]
)
from agent_factory.work_kinds.base import WorkKindHandler


@pytest.mark.parametrize(
    ("kind", "hints", "argv", "expected"),
    [
        ("eval", {"suite": "and-scene", "sandbox": "docker"}, ("run.sh",), "docker"),
        ("eval", {"suite": "and-scene"}, ("run.sh",), "docker"),
        ("eval", {"suite": "and-scene", "backend": "fly-machine"}, ("run.sh",), "fly-machine"),
        ("fix", {"sandbox": "docker"}, ("sandbox-run.sh",), "docker"),
        ("fix", {"sandbox": "host"}, ("host-run.sh",), "host"),
        ("fix", {"artifact_path": "/tmp/e"}, ("/bin/true",), "host"),
        ("fix", {"backend": "host", "sandbox": "host"}, ("host-run.sh",), "host"),
    ],
)
def test_persisted_plan_resolves_after_restart(
    tmp_path: Path, kind: str, hints: dict[str, str], argv: tuple[str, ...], expected: str
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", kind, "x", {}))
    run = store.reserve_run(claim.id, "unit", reason="initial", evidence_path=str(tmp_path))
    plan = ExecutionPlan(argv, str(tmp_path), {}, (), (), hints, False)
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))

    recorded = store.get_run(run.id)
    assert recorded is not None
    backend = backend_for(recorded.plan)
    assert backend is not None and backend.name == expected


def test_ambiguous_persisted_plan_holds_its_run(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path))
    plan = ExecutionPlan(("/bin/true",), str(tmp_path), {}, (), (), {}, False)
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})

    supervise(state, run.id, run.launch_nonce)

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "observing"
    assert saved.result["uncertain"] is True


def test_reconciliation_selects_only_live_and_unconsumed_runs(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "fix", "x", {}))
    consumed = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(consumed.id, execution_status="failed", result={})
    store.set_setting("consumed-results", consumed.id, {"complete": True})
    pending = store.reserve_run(claim.id, "fix", reason="recovery", evidence_path=str(tmp_path))
    store.finish_run(pending.id, execution_status="failed", result={})
    active = store.reserve_run(claim.id, "review", reason="review", evidence_path=str(tmp_path))

    selected = store.runs_requiring_backend_reconciliation()

    assert {run.id for run in selected} == {pending.id, active.id}


def test_recorded_fly_run_reconciles_after_fly_is_disabled(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", "eval", "x", {}))
    artifact = tmp_path / "artifact"
    factory = artifact / ".factory"
    factory.mkdir(parents=True)
    (factory / "manifest.json").write_text(
        '{"fly":{"app":"old-app","token_file":"/tmp/old-token"}}'
    )
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(artifact))
    plan = ExecutionPlan(
        ("run.sh",),
        str(tmp_path),
        {},
        (),
        (),
        {"artifact_path": str(artifact), "backend": "fly-machine"},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits={})
    store.mark_running(run.id, {})
    local = cast(
        LocalConfig,
        SimpleNamespace(eval_execution="docker", fix=SimpleNamespace(execution="host"), fly=None),
    )
    calls: list[tuple[str | None, Path | None]] = []

    def capture(backend: FlyMachineBackend, _store: object) -> list[str]:
        calls.append((backend._app, backend._token_file))  # pyright: ignore[reportPrivateUsage]
        return []

    with patch.object(FlyMachineBackend, "reconcile", capture):
        _reconcile_backends(store, local)

    assert calls == [("old-app", Path("/tmp/old-token"))]


@pytest.mark.darwin
@pytest.mark.parametrize(
    ("kind", "reason", "hints", "argv", "backend_name"),
    [
        ("eval", "initial", {"suite": "and-scene", "sandbox": "docker"}, ("run.sh",), "docker"),
        ("eval", "initial", {"suite": "and-scene"}, ("run.sh",), "docker"),
        ("fix", "initial", {"sandbox": "docker"}, ("sandbox-run.sh",), "docker"),
        ("fix", "initial", {"sandbox": "host"}, ("host-run.sh",), "host"),
        ("fix", "review", {"sandbox": "host"}, ("host-run.sh",), "host"),
    ],
)
def test_legacy_plan_adoption_and_status(
    tmp_path: Path,
    kind: str,
    reason: str,
    hints: dict[str, str],
    argv: tuple[str, ...],
    backend_name: str,
) -> None:
    state = tmp_path / "state.sqlite3"
    store = ClaimStore(state)
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I", "P", kind, "x", {}))
    run = store.reserve_run(
        claim.id, "rep-1" if kind == "eval" else "fix", reason=reason, evidence_path=str(tmp_path)
    )
    plan = ExecutionPlan(
        argv, str(tmp_path), {}, (), (), {**hints, "artifact_path": str(tmp_path)}, False
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
    (tmp_path / "result.json").write_text('{"evaluation_status":"completed"}')
    with patch.object(DockerContainerBackend, "adopt", return_value=Probe("gone")):
        supervise(state, run.id, run.launch_nonce)
    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "completed"
    assert len(store.runs_for_claim(claim.id)) == 1
    assert "claim: example/repo#1" in status(store, include_all=True)
    assert backend_for(saved.plan).name == backend_name  # type: ignore[union-attr]


@pytest.mark.darwin
def test_legacy_fly_adoption_disposal_and_status(tmp_path: Path) -> None:
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
        {"suite": "and-scene", "backend": "fly-machine", "artifact_path": str(tmp_path)},
        False,
    )
    store.configure_run(run.id, plan=plan_document(plan), limits=vars(SupervisionLimits()))
    assert store.begin_run(run.id, launch_nonce=run.launch_nonce, supervisor={}, process={})
    (tmp_path / "result.json").write_text('{"evaluation_status":"completed"}')
    decisions: list[str] = []

    def record_disposal(_identity: object, decision: str, _store: object) -> None:
        decisions.append(decision)

    def classify(_run: object, _result: object) -> SimpleNamespace:
        return SimpleNamespace(kind="success")

    with (
        patch.object(FlyMachineBackend, "identity_from_plan", return_value={"id": "machine-1"}),
        patch.object(FlyMachineBackend, "adopt", return_value=Probe("gone")),
        patch.object(FlyMachineBackend, "provenance", return_value={}),
        patch.object(FlyMachineBackend, "dispose", side_effect=record_disposal),
    ):
        supervise(state, run.id, run.launch_nonce)
        saved = store.get_run(run.id)
        assert saved is not None and saved.status == "completed"
        handler = cast(WorkKindHandler, SimpleNamespace(classify=classify))
        _dispose_result(store, handler, saved, AttemptResult("completed", None, saved.result), None)
    assert decisions == ["destroy"]
    assert "claim: example/repo#1" in status(store, include_all=True)
