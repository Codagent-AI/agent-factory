"""INT-005 Fly ownership regression coverage."""

from __future__ import annotations

from pathlib import Path

from agent_factory.backends import Probe
from agent_factory.store import ClaimStore
from agent_factory.supervisor import _timeout  # pyright: ignore[reportPrivateUsage]


def test_int_005_preserves_progress_across_fly_reattachment() -> None:
    # Persisted wall-clock timestamps, rather than a watcher's monotonic origin,
    # make a restart retain the inactivity budget.
    assert _timeout(110.0, 10.0, 100.0, _limits()) is None
    assert _timeout(131.0, 10.0, 100.0, _limits()) == "inactivity"


def test_int_005_typed_api_unknown_is_not_machine_loss() -> None:
    assert Probe("unknown", "temporary API failure").state != "gone"


def test_fly_mismatch_clear_is_compare_and_set(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        old = {"run_id": "run-1", "machine_id": "machine-1"}
        store.set_setting("runtime", "fly:mismatch", old)

        assert store.compare_and_set_setting("runtime", "fly:mismatch", old, {})
        assert not store.compare_and_set_setting("runtime", "fly:mismatch", old, {})
    finally:
        store.close()


def _limits():
    from agent_factory.supervisor import SupervisionLimits

    return SupervisionLimits(inactivity_seconds=30, execution_seconds=1000, total_seconds=2000)


def _fly_plan(artifact: Path):
    from agent_factory.controller import ExecutionPlan

    return ExecutionPlan(
        ("true",),
        str(artifact),
        {},
        (),
        (),
        {"artifact_path": str(artifact), "backend": "fly-machine"},
        True,
    )


def _records(artifact: Path, *, record_run: str, manifest_run: str, token: Path) -> None:
    import json

    factory = artifact / ".factory"
    factory.mkdir(parents=True, exist_ok=True)
    (factory / "machine.json").write_text(
        json.dumps({"app": "app", "id": "machine-1", "run_id": record_run, "nonce": "n"})
    )
    (factory / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": manifest_run,
                "claim_id": "claim-1",
                "unit_key": "rep-1",
                "nonce": "other",
                "fly": {"app": "app", "token_file": str(token)},
            }
        )
    )


def test_int_005_record_from_an_earlier_attempt_is_not_this_attempts_identity(
    tmp_path: Path,
) -> None:
    from agent_factory.fly.backend import FlyMachineBackend

    artifact = tmp_path / "artifact"
    token = tmp_path / "token"
    token.write_text("t")
    _records(artifact, record_run="run-1", manifest_run="run-2", token=token)
    backend = FlyMachineBackend()
    assert backend.identity_from_plan(_fly_plan(artifact), None) is None

    # Once the launcher has claimed the Machine for the recovery attempt, the
    # identity carries the Machine's original nonce and the new run id.
    _records(artifact, record_run="run-2", manifest_run="run-2", token=token)
    identity = backend.identity_from_plan(_fly_plan(artifact), None)
    assert identity is not None
    assert identity["expected_metadata"] == {
        "factory-owner": "agent-factory",
        "run_id": "run-2",
        "claim_id": "claim-1",
        "unit_key": "rep-1",
        "nonce": "n",
    }


def test_int_005_reserved_recovery_attempt_launches_instead_of_attaching(
    tmp_path: Path, monkeypatch: object
) -> None:
    """A surviving Machine's record must not divert a new attempt into attach mode."""
    from pytest import MonkeyPatch

    from agent_factory import supervisor
    from agent_factory.store import ClaimDraft, Run

    assert isinstance(monkeypatch, MonkeyPatch)
    artifact = tmp_path / "artifact"
    token = tmp_path / "token"
    token.write_text("t")
    _records(artifact, record_run="run-1", manifest_run="run-1", token=token)
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "x", {}))
        run = store.reserve_run(claim.id, "rep-1", reason="recovery", evidence_path=str(artifact))
        launched: list[str] = []

        def launch(_store: object, reserved: Run, _plan: object, _limits: object) -> None:
            launched.append(reserved.id)

        def attach(*_arguments: object) -> None:
            raise AssertionError("attach was spawned")

        monkeypatch.setattr(supervisor, "_launch_and_observe", launch)
        monkeypatch.setattr(supervisor, "_spawn_plan_process", attach)
        supervisor._supervise_fly(  # pyright: ignore[reportPrivateUsage]
            store, run, _fly_plan(artifact), _limits()
        )
        assert launched == [run.id]
    finally:
        store.close()


def test_int_005_launcher_exit_code_is_recorded_and_never_inherited(tmp_path: Path) -> None:
    import subprocess

    from agent_factory.supervisor import (
        _launcher_exit_code,  # pyright: ignore[reportPrivateUsage]
        _recording_exit_code,  # pyright: ignore[reportPrivateUsage]
    )

    artifact = tmp_path / "artifact"
    status = artifact / ".factory" / "launcher-exit-code"
    status.parent.mkdir(parents=True)
    status.write_text("0\n")  # left behind by an earlier attempt's launcher

    argv = _recording_exit_code(["sh", "-c", "exit 71"], str(artifact))
    assert not status.exists()
    assert subprocess.run(argv, check=False).returncode == 71
    assert _launcher_exit_code(_fly_plan(artifact), str(artifact)) == 71


def test_status_shows_each_stopped_quota_machine_of_a_claim(tmp_path: Path) -> None:
    """The backend keys records by run; status must still find them by claim."""
    from agent_factory.operations import _hold_lines  # pyright: ignore[reportPrivateUsage]
    from agent_factory.store import ClaimDraft

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "x", {}))
        other = store.create_claim(ClaimDraft("example/evals", 2, "I2", "P2", "eval", "x", {}))
        for run_id, owner, machine in (
            ("run-1", claim.id, "machine-1"),
            ("run-2", claim.id, "machine-2"),
            ("run-9", other.id, "machine-9"),
        ):
            store.set_setting(
                "runtime",
                f"fly:machine:{run_id}",
                {
                    "machine_id": machine,
                    "claim_id": owner,
                    "decision": "stop",
                    "deadline_epoch": 1900000000,
                },
            )
        lines = "\n".join(_hold_lines(store, claim, None))
        assert "machine-1 stopped (quota hold), deadline 1900000000" in lines
        assert "machine-2 stopped (quota hold)" in lines
        assert "machine-9" not in lines
    finally:
        store.close()


def test_a_saved_fly_mismatch_holds_evals_only_while_evals_run_on_fly(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from typing import cast

    from agent_factory.config import LocalConfig
    from agent_factory.runtime import (
        _fly_mismatch_diagnostic,  # pyright: ignore[reportPrivateUsage]
    )

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        store.set_setting(
            "runtime", "fly:mismatch", {"machine_id": "machine-1", "remedy": "destroy it"}
        )

        held = _fly_mismatch_diagnostic(
            store, cast(LocalConfig, SimpleNamespace(eval_execution="fly"))
        )
        stale = _fly_mismatch_diagnostic(
            store, cast(LocalConfig, SimpleNamespace(eval_execution="docker"))
        )

        assert held is not None and not held.available and "machine-1" in held.detail
        assert stale is None
    finally:
        store.close()
