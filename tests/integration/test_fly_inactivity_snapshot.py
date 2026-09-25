"""A Fly attempt stopped for inactivity leaves a snapshot of what its guest was doing."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from agent_factory.backends import Probe
from agent_factory.controller import ExecutionPlan
from agent_factory.fly.backend import FlyMachineBackend
from agent_factory.fly.transport import FlyTransport, FlyTransportError
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import (
    SupervisionLimits,
    _observe_fly,  # pyright: ignore[reportPrivateUsage]
    _process_identity,  # pyright: ignore[reportPrivateUsage]
)

IDENTITY = {"app": "app", "id": "machine-1", "token_file": "/tmp/fly-token"}


class RecordingTransport:
    def __init__(self, output: bytes | None) -> None:
        self.output = output
        self.commands: list[tuple[str, float | None]] = []
        self.token_file: Path | None = None

    def command(self, command: str, *, timeout: float | None = 120) -> bytes:
        self.commands.append((command, timeout))
        if self.output is None:
            raise FlyTransportError("ssh could not run: TimeoutExpired")
        return self.output


def _backend(transport: RecordingTransport) -> FlyMachineBackend:
    return FlyMachineBackend(transport_factory=lambda _app, _id: cast(FlyTransport, transport))


def test_snapshot_writes_the_guest_process_and_network_state(tmp_path: Path) -> None:
    transport = RecordingTransport(b"== processes\n1 0 S 10 do_wait sh\n")
    destination = tmp_path / ".factory" / "inactivity-snapshot-run-1.txt"

    assert _backend(transport).snapshot(IDENTITY, destination)

    command, timeout = transport.commands[0]
    assert "/proc" in command and "/proc/net/tcp" in command
    # A snapshot must never hold up the stop for long.
    assert timeout is not None and timeout <= 60
    text = destination.read_text(encoding="utf-8")
    assert "Machine machine-1" in text
    assert "1 0 S 10 do_wait sh" in text


def test_snapshot_failure_is_recorded_and_does_not_raise(tmp_path: Path) -> None:
    destination = tmp_path / ".factory" / "inactivity-snapshot-run-1.txt"

    assert not _backend(RecordingTransport(None)).snapshot(IDENTITY, destination)

    assert "snapshot failed: ssh could not run: TimeoutExpired" in destination.read_text(
        encoding="utf-8"
    )


class FakeBackend:
    def __init__(self, launcher: subprocess.Popen[bytes]) -> None:
        self.launcher = launcher
        self.calls: list[str] = []
        self.snapshots: list[Path] = []

    def probe(self, _identity: Mapping[str, object]) -> Probe:
        return Probe("alive")

    def snapshot(self, _identity: Mapping[str, object], destination: Path) -> bool:
        self.calls.append("snapshot")
        self.snapshots.append(destination)
        return True

    def terminate(self, _identity: Mapping[str, object]) -> bool:
        self.calls.append("terminate")
        self.launcher.kill()
        self.launcher.wait()
        return True

    def dispose(self, _identity: Mapping[str, object], _decision: str) -> None:
        self.calls.append("dispose")


def _observe(tmp_path: Path, *, cancel: bool) -> tuple[FakeBackend, str, ClaimStore]:
    artifact = tmp_path / "artifact"
    (artifact / ".factory").mkdir(parents=True)
    plan = ExecutionPlan(
        ("true",),
        str(artifact),
        {},
        (),
        (),
        {"artifact_path": str(artifact), "backend": "fly-machine"},
        True,
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "x", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(artifact))
    store.configure_run(run.id, plan={"argv": ["true"]}, limits={})
    store.mark_running(run.id, {})
    if cancel:
        store.request_cancellation(run.id)
    launcher = subprocess.Popen(["sleep", "30"])
    identity = _process_identity(launcher.pid, plan)
    assert identity is not None
    backend = FakeBackend(launcher)
    _observe_fly(
        store,
        run.id,
        plan,
        SupervisionLimits(inactivity_seconds=0.2, execution_seconds=600, total_seconds=600),
        IDENTITY,
        cast(FlyMachineBackend, backend),
        identity,
    )
    return backend, run.id, store


def test_inactivity_stop_snapshots_the_guest_before_terminating_it(tmp_path: Path) -> None:
    backend, run_id, store = _observe(tmp_path, cancel=False)
    try:
        assert backend.calls[:2] == ["snapshot", "terminate"]
        assert backend.snapshots == [
            tmp_path / "artifact" / ".factory" / f"inactivity-snapshot-{run_id}.txt"
        ]
        finished = store.get_run(run_id)
        assert finished is not None and finished.status == "timed_out"
        assert finished.result == {"timeout": "inactivity"}
        assert finished.progress["inactivity_snapshot"] == {
            "path": str(backend.snapshots[0]),
            "captured": True,
        }
    finally:
        store.close()


def test_cancellation_stops_without_a_snapshot(tmp_path: Path) -> None:
    backend, run_id, store = _observe(tmp_path, cancel=True)
    try:
        assert "snapshot" not in backend.calls
        finished = store.get_run(run_id)
        assert finished is not None and finished.status == "cancelled"
    finally:
        store.close()
