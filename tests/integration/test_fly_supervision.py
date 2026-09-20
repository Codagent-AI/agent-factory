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
