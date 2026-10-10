# pyright: reportPrivateUsage=false
"""INT-005: public status and the real deploy slot predicates."""

from contextlib import closing
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore
from tests.integration.test_deploy_slots import _holds
from tests.integration.test_priority_lanes_cli import _cli


def test_status_and_slot_predicates(tmp_path: Path) -> None:
    path = tmp_path / "db"
    with closing(ClaimStore(path)) as store:
        idle = _cli(path, "status").stdout
        assert _holds("slots_free", idle)
        assert _holds("host_slots_free", idle)
        assert "lanes: off (" in idle
        store.enable_lanes()
        claims = [
            store.create_claim(ClaimDraft("o/r", i, f"I{i}", f"P{i}", "fix", "fp", {}))
            for i in (1, 2)
        ]
        low = store.reserve_run(
            claims[0].id, "one", lane="low", reason="initial", evidence_path="/e"
        )
        high = store.reserve_run(
            claims[1].id, "one", lane="high", reason="initial", evidence_path="/e"
        )
        store.mark_running(low.id, {})
        store.mark_running(high.id, {})
        store.set_setting(
            "lane-wait",
            "o/r:3",
            {"kind": "fix", "lane": "medium", "cause": "higher-lane", "holder_run_id": high.id},
        )
        store.set_claim_lifecycle(
            claims[0].id,
            "settled",
            {"pr": {"number": 4}, "waiting_review": {"comments": ["feedback"]}},
        )
        busy = _cli(path, "status").stdout
        assert "fix slot: busy (high, low)" in busy
        assert "fix lane high: o/r#2 one (running)" in busy
        assert "fix lane low: o/r#1 one (running)" in busy
        assert "o/r#3 waits for fix lane medium" in busy
        assert "held by busy fix lane high (o/r#2; higher-lane)" in busy
        assert "lanes: off" not in busy
        assert not _holds("slots_free", busy)
        assert not _holds("host_slots_free", busy)
        store.finish_run(high.id, execution_status="completed", result={})
        store._connection.execute("UPDATE run SET lane=NULL WHERE id=?", (low.id,))  # pyright: ignore[reportPrivateUsage]
        legacy = _cli(path, "status").stdout
        assert "fix slot: busy (all)" in legacy
        assert "fix lane all (pre-lane attempt): o/r#1" in legacy
        assert "o/r#3 waits" not in legacy  # stale holder is ignored
        assert not _holds("slots_free", legacy)
        store.finish_run(low.id, execution_status="completed", result={})
        assert _holds("slots_free", _cli(path, "status").stdout)
