from __future__ import annotations

import json
import time
from pathlib import Path

from agent_factory.controller import AttemptResult, ExecutionPlan
from agent_factory.fly.backend import FlyMachineBackend
from agent_factory.store import Claim, ClaimStore, Run
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler
from agent_factory.work_kinds.eval.handler import (
    _technical_failure,  # pyright: ignore[reportPrivateUsage]
)


class FakeClient:
    def __init__(self, machine: dict[str, object]) -> None:
        self.machine = machine
        self.destroyed = False
        self.stopped = False

    def get_machine(self, machine_id: str) -> dict[str, object]:
        assert machine_id == "machine-1"
        return self.machine

    def destroy(self, machine_id: str) -> None:
        assert machine_id == "machine-1"
        self.destroyed = True

    def stop(self, machine_id: str) -> None:
        assert machine_id == "machine-1"
        self.stopped = True
        self.machine["state"] = "stopped"

    def list_machines(self, metadata_key: str, metadata_value: str) -> list[dict[str, object]]:
        assert (metadata_key, metadata_value) == ("factory-owner", "agent-factory")
        return [self.machine]


def _backend(client: FakeClient) -> FlyMachineBackend:
    return FlyMachineBackend(client_factory=lambda app, token_file: client)


def _identity(tmp_path: Path, token: Path) -> tuple[ExecutionPlan, dict[str, object]]:
    artifact = tmp_path / "artifact"
    factory = artifact / ".factory"
    factory.mkdir(parents=True)
    (factory / "machine.json").write_text(
        json.dumps(
            {"app": "factory", "id": "machine-1", "nonce": "nonce", "image_ref": "sha256:old"}
        )
    )
    (factory / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "claim_id": "claim-1",
                "unit_key": "rep-1",
                "nonce": "nonce",
                "fly": {"app": "factory", "token_file": str(token)},
            }
        )
    )
    plan = ExecutionPlan((), str(tmp_path), {}, (), (), {"artifact_path": str(artifact)}, False)
    return plan, {"id": "run-1"}


def test_fly_backend_reads_machine_identity_and_probes_immutable_metadata(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("secret")
    machine: dict[str, object] = {
        "id": "machine-1",
        "state": "started",
        "region": "ewr",
        "config": {
            "image": "registry.fly.io/factory@sha256:new",
            "guest": {"cpu_kind": "shared", "cpus": 4, "memory_mb": 8192},
            "metadata": {
                "factory-owner": "agent-factory",
                "run_id": "run-1",
                "claim_id": "claim-1",
                "unit_key": "rep-1",
                "nonce": "nonce",
            },
        },
    }
    plan, run = _identity(tmp_path, token)
    backend = _backend(FakeClient(machine))

    identity = backend.identity_from_plan(plan, run)

    assert identity is not None
    assert backend.probe(identity).state == "alive"
    machine["config"]["metadata"]["nonce"] = "wrong"  # type: ignore[index]
    assert backend.probe(identity).state == "mismatch"


def test_fly_backend_never_destroys_unknown_or_mismatched_machine(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("secret")
    client = FakeClient(
        {"id": "machine-1", "state": "started", "config": {"metadata": {"nonce": "wrong"}}}
    )
    plan, run = _identity(tmp_path, token)
    backend = _backend(client)
    identity = backend.identity_from_plan(plan, run)
    assert identity is not None

    backend.dispose(identity, "destroy")

    assert not client.destroyed


def test_machine_loss_is_settled_infrastructure_failure_not_a_retry() -> None:
    result = AttemptResult("interrupted", None, {"reason": "machine lost"})

    assert not _technical_failure(result)


def test_fly_disposal_stops_only_a_verified_machine_and_records_quota_hold(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("secret")
    client = FakeClient(
        {
            "id": "machine-1",
            "state": "started",
            "config": {
                "metadata": {
                    "factory-owner": "agent-factory",
                    "run_id": "run-1",
                    "claim_id": "claim-1",
                    "unit_key": "rep-1",
                    "nonce": "nonce",
                    "deadline_epoch": "9999999999",
                }
            },
        }
    )
    plan, run = _identity(tmp_path, token)
    backend = _backend(client)
    identity = backend.identity_from_plan(plan, run)
    assert identity is not None
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        backend.dispose(identity, "stop", store)
        assert client.stopped
        assert store.get_setting("runtime", "fly:machine:claim-1") == {
            "machine_id": "machine-1",
            "decision": "stop",
            "deadline_epoch": 9999999999,
            "state": "stopped",
        }
    finally:
        store.close()


def test_fly_reconciliation_destroys_expired_machine_and_reports_live_orphan(
    tmp_path: Path,
) -> None:
    token = tmp_path / "token"
    token.write_text("secret")
    client = FakeClient(
        {
            "id": "machine-1",
            "state": "started",
            "config": {
                "metadata": {
                    "factory-owner": "agent-factory",
                    "deadline_epoch": str(int(time.time()) - 1),
                }
            },
        }
    )
    backend = _backend(client)
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        assert backend.reconcile(store) == ["machine-1"]
        assert client.destroyed
    finally:
        store.close()


def test_lost_repetition_is_explained_but_does_not_become_a_product_failure() -> None:
    handler = EvalHandler(
        EvalDefaults(
            "main",
            "main",
            {"lead": "codex:x", "implementor": "codex:x", "tester": "codex:x"},
            False,
            2,
        )
    )
    claim = Claim(
        "claim",
        "repo",
        1,
        "issue",
        "item",
        "eval",
        "fp",
        {"settings": {"repetitions": 2}},
        "active",
        {},
        {},
        {},
        {},
    )
    lost = Run(
        "lost",
        "claim",
        "rep-1",
        0,
        "initial",
        "failed",
        "eval",
        "",
        {"reason": "machine lost", "failure": {"owner": "factory", "code": "machine-lost"}},
        "",
        {},
        {},
        {},
        False,
        None,
        None,
    )
    review = Run(
        "review",
        "claim",
        "rep-2",
        0,
        "initial",
        "completed",
        "eval",
        "",
        {"product_verdict": "ready-for-human-review"},
        "",
        {},
        {},
        {},
        False,
        None,
        None,
    )

    outcome = handler.settle(claim, [lost, review])

    assert outcome is not None
    assert outcome.verdict == "pending-human-review"
    assert "rep-1" in outcome.event_body and "machine lost" in outcome.event_body
