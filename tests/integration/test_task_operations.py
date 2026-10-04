"""Task slot and doctor group appear in operator output."""

# pyright: reportPrivateUsage=false

from pathlib import Path

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.operations import format_doctor, status
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.pull_request.kinds import TASK
from agent_factory.work_kinds.pull_request.readiness import check_readiness
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE


def test_status_renders_task_slot_and_claim(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        assert "task slot: free" in status(store)
        claim = store.create_claim(
            ClaimDraft("example/work", 76, "I76", "P76", "task", "fp-task", {})
        )
        store.reserve_run(claim.id, "task", reason="initial", evidence_path=str(tmp_path))
        rendered = status(store)
        assert "task slot: example/work#76 task (" in rendered
        assert "task" in rendered
    finally:
        store.close()


def test_doctor_formats_task_host_group() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[task]\n")
    local = LocalConfig.from_toml(_LOCAL_BASE)
    rendered = format_doctor(check_readiness(local, shared, definition=TASK))
    assert "-- task-host --" in rendered
    assert "task targets: FAIL" in rendered
