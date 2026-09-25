"""Legacy persisted plans retain their execution owner across a deploy."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.backends.resolve import backend_for
from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits, supervise
from agent_factory.supervisor import (
    _plan_document as plan_document,  # pyright: ignore[reportPrivateUsage]
)


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
