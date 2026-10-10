"""INT-002: previous release's explicit SQL remains usable on both guards."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore, LaneBusy

# Verbatim INSERT/UPDATE statements from origin/main store.py; named columns omit lane.
OLD_INSERT = """INSERT INTO run (id, claim_id, unit_key, attempt_number, reason, status,
                    kind, launch_nonce, supervisor_json, plan_json, evidence_path, progress_json,
                    cancellation_requested, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?, '{}', '{}', ?, '{}', 0, '{}', ?)"""
OLD_RUNNING = """UPDATE run SET status = 'running', supervisor_json = ?, started_at = ?
                WHERE id = ? AND status = 'reserved'"""
OLD_PROGRESS = """UPDATE run SET progress_json = ? WHERE id = ?
                AND status IN ('reserved', 'running', 'observing')"""
OLD_FINISH = """UPDATE run SET status = ?, result_json = ?, finished_at = ? WHERE id = ?
                AND status IN ('reserved', 'running', 'observing')"""


def test_upgrade_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    raw = sqlite3.connect(path, isolation_level=None)
    raw.executescript((Path("tests/fixtures/priority_lanes_v4.sql")).read_text())
    with closing(ClaimStore(path)) as store:
        claims = [
            store.create_claim(ClaimDraft("o/r", i, f"I{i}", f"P{i}", kind, "fp", {}))
            for i, kind in enumerate(["fix", "eval", "fix"], 1)
        ]
        store.set_paused(True)
        store.set_hold(claims[0].id, "quota", {"until": "2099-01-01"})
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
        assert store.lane_mode() == "kind"  # doctor/writable open leaves old guard
        for i, claim in enumerate(claims[:2]):
            raw.execute(
                OLD_INSERT,
                (
                    f"legacy-{i}",
                    claim.id,
                    "one",
                    0,
                    "initial",
                    claim.kind,
                    "nonce",
                    "/e",
                    "2026-01-01",
                ),
            )
            raw.execute(OLD_RUNNING, ("{}", "2026-01-01", f"legacy-{i}"))
            raw.execute(OLD_PROGRESS, ('{"step":"working"}', f"legacy-{i}"))
        store.enable_lanes()
        assert store.get_run("legacy-0").progress == {"step": "working"}  # type: ignore[union-attr]
        for claim in (claims[0], claims[2]):
            with pytest.raises(LaneBusy) as error:
                store.reserve_run(
                    claim.id, "next", reason="recovery", lane="medium", evidence_path="/e"
                )
            assert error.value.cause == "legacy"
        assert store.lane_decision("task", "low", None, "initial").allowed
        raw.execute(OLD_FINISH, ("completed", "{}", "2026-01-02", "legacy-0"))
        low = store.reserve_run(
            claims[0].id, "next", reason="initial", lane="low", evidence_path="/e"
        )
        high = store.reserve_run(
            claims[2].id, "next", reason="initial", lane="high", evidence_path="/e"
        )
        assert {r.id for r in store.restore_kind_guard(False)} == {low.id, high.id}
        assert store.lane_mode() == "lanes"
        assert {r.id for r in store.restore_kind_guard(True)} == {low.id, high.id}
        store.finish_run(high.id, execution_status="completed", result={})
        assert store.restore_kind_guard(True) == [] and store.lane_mode() == "lanes"
        assert store.restore_kind_guard(False) == [] and store.lane_mode() == "kind"
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                OLD_INSERT,
                (
                    "old-second",
                    claims[2].id,
                    "old",
                    0,
                    "initial",
                    "fix",
                    "nonce",
                    "/e",
                    "2026-01-03",
                ),
            )
        assert store.is_paused()
        assert store.get_hold(claims[0].id, "quota") == {"until": "2099-01-01"}
        assert len(store.runs_for_claim(claims[0].id)) == 2
        assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
    raw.close()
