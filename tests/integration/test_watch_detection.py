# pyright: reportPrivateUsage=false
"""INT-001: transactional cursor, grace eligibility, and de-duplication."""

from __future__ import annotations

import multiprocessing
from datetime import UTC, datetime, timedelta
from multiprocessing.queues import Queue
from pathlib import Path

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import detect
from agent_factory.watch import store as watch_store


def test_detection_uses_current_grace_and_never_requeues(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        claim = store.create_claim(
            ClaimDraft("Codagent-AI/example", 12, "I", "P", "eval", "fp", {})
        )
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/evidence"
        )
        store.finish_run(run.id, execution_status="failed", result={})
        store.set_setting("consumed-results", run.id, {"complete": True})
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?",
            ((now - timedelta(minutes=5)).isoformat(), run.id),
        )
        detect.detect(store, 7, lambda: now + timedelta(seconds=1))
        assert not [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        detect.detect(store, 3, lambda: now + timedelta(seconds=2))
        detect.detect(store, 3, lambda: now + timedelta(seconds=3))
        failures = [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        assert len(failures) == 1
        assert failures[0]["event_key"] == f"FAILURE:{run.id}"
        assert store._connection.execute("PRAGMA user_version").fetchone()[0] == 4
    finally:
        store.close()


def _failed_run(store: ClaimStore, finished: datetime, key: str) -> str:
    claim = store.create_claim(
        ClaimDraft("Codagent-AI/example", 13, "I", f"P-{key}", "eval", f"fp-{key}", {})
    )
    run = store.reserve_run(
        claim.id, key, lane="low", reason="initial", evidence_path="/tmp/evidence"
    )
    store.finish_run(run.id, execution_status="failed", result={})
    store.set_setting("consumed-results", run.id, {"complete": True})
    store._connection.execute(
        "UPDATE run SET finished_at=? WHERE id=?", (finished.isoformat(), run.id)
    )
    return run.id


def _failures(store: ClaimStore) -> list[str]:
    return [row["run_id"] for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]


def _fix_run(
    store: ClaimStore,
    finished: datetime,
    key: str,
    *,
    status: str,
    outcome: str,
    consumed: bool,
    kind: str = "fix",
) -> str:
    claim = store.create_claim(
        ClaimDraft("Codagent-AI/example", 15, "I", f"P-{key}", kind, f"fp-{key}", {})
    )
    run = store.reserve_run(
        claim.id, key, lane="low", reason="initial", evidence_path="/tmp/evidence"
    )
    store.finish_run(run.id, execution_status=status, result={"outcome": outcome})
    if consumed:
        store.set_setting("consumed-results", run.id, {"complete": True})
    store._connection.execute(
        "UPDATE run SET finished_at=? WHERE id=?", (finished.isoformat(), run.id)
    )
    return run.id


def test_completed_failed_outcome_waits_for_grace_then_queues_failure(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        run_id = _fix_run(
            store,
            now - timedelta(minutes=5),
            "failed-outcome",
            status="completed",
            outcome="failed",
            consumed=True,
        )
        detect.detect(store, 7, lambda: now)
        assert watch_store.rows(store) == []
        detect.detect(store, 7, lambda: now + timedelta(minutes=3))
        rows = watch_store.rows(store)
        assert len(rows) == 1
        assert rows[0]["event_key"] == f"FAILURE:{run_id}"
        assert rows[0]["event_kind"] == "FAILURE"
    finally:
        store.close()


def test_completed_failed_task_outcome_queues_failure(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        run_id = _fix_run(
            store,
            now - timedelta(minutes=8),
            "task-failed-outcome",
            status="completed",
            outcome="failed",
            consumed=True,
            kind="task",
        )
        detect.detect(store, 7, lambda: now)
        assert _failures(store) == [run_id]
    finally:
        store.close()


def test_completed_needs_input_outcome_queues_no_event(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        _fix_run(
            store,
            now - timedelta(minutes=8),
            "needs-input",
            status="completed",
            outcome="needs-input",
            consumed=True,
        )
        detect.detect(store, 7, lambda: now)
        assert watch_store.rows(store) == []
    finally:
        store.close()


def test_failed_status_and_outcome_queue_one_failure_across_cycles(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        run_id = _fix_run(
            store,
            now - timedelta(minutes=8),
            "double-failure",
            status="failed",
            outcome="failed",
            consumed=True,
        )
        detect.detect(store, 7, lambda: now)
        detect.detect(store, 7, lambda: now + timedelta(minutes=1))
        assert _failures(store) == [run_id]
        assert len(watch_store.rows(store)) == 1
    finally:
        store.close()


def test_completed_failed_outcome_waits_for_result_consumption(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        _fix_run(
            store,
            now - timedelta(minutes=8),
            "unconsumed-outcome",
            status="completed",
            outcome="failed",
            consumed=False,
        )
        detect.detect(store, 7, lambda: now)
        assert watch_store.rows(store) == []
    finally:
        store.close()


def test_failure_waits_for_result_consumption(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 0, lambda: now - timedelta(minutes=10))
        run_id = _failed_run(store, now - timedelta(minutes=5), "unconsumed")
        store._connection.execute(
            "DELETE FROM settings WHERE namespace='consumed-results' AND key=?", (run_id,)
        )
        detect.detect(store, 0, lambda: now)
        assert _failures(store) == []
        store.set_setting("consumed-results", run_id, {"complete": True})
        detect.detect(store, 0, lambda: now + timedelta(minutes=3))
        assert _failures(store) == [run_id]
    finally:
        store.close()


def test_pr_ready_after_cursor_passes_finished_at(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=10))
        claim = store.create_claim(ClaimDraft("o/r", 14, "I", "P", "fix", "fp-pr", {}))
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/pr"
        )
        store.finish_run(run.id, execution_status="interrupted", result={})
        finished = now - timedelta(minutes=5)
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (finished.isoformat(), run.id)
        )
        detect.detect(store, 7, lambda: now)
        assert not [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        store.normalize_terminal_result(
            run.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": "https://github.com/o/r/pull/14"}},
        )
        detect.detect(store, 7, lambda: now + timedelta(minutes=3))
        detect.detect(store, 7, lambda: now + timedelta(minutes=4))
        ready = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert len(ready) == 1
        assert ready[0]["pr_number"] == 14
    finally:
        store.close()


def test_task_pr_ready_and_failure_each_queue_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 0, lambda: now - timedelta(minutes=10))
        ready = store.create_claim(ClaimDraft("o/r", 76, "I76", "P76", "task", "fp-76", {}))
        ready_run = store.reserve_run(
            ready.id, "task", lane="low", reason="initial", evidence_path="/tmp/task-pr"
        )
        store.finish_run(
            ready_run.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": "https://github.com/o/r/pull/76"}},
        )
        failed = store.create_claim(ClaimDraft("o/r", 77, "I77", "P77", "task", "fp-77", {}))
        failed_run = store.reserve_run(
            failed.id, "task", lane="low", reason="initial", evidence_path="/tmp/task-fail"
        )
        store.finish_run(failed_run.id, execution_status="failed", result={})
        store.set_setting("consumed-results", failed_run.id, {"complete": True})
        detect.detect(store, 0, lambda: now)
        detect.detect(store, 0, lambda: now + timedelta(seconds=1))
        events = [
            row for row in watch_store.rows(store) if row["run_id"] in {ready_run.id, failed_run.id}
        ]
        assert {(row["event_kind"], row["run_id"]) for row in events} == {
            ("PR-READY", ready_run.id),
            ("FAILURE", failed_run.id),
        }
    finally:
        store.close()


def test_grace_shrinking_to_zero_queues_a_waiting_failure_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=30))
        run_id = _failed_run(store, now - timedelta(minutes=5), "shrink")
        detect.detect(store, 7, lambda: now)
        assert _failures(store) == []
        detect.detect(store, 0, lambda: now + timedelta(minutes=5))
        detect.detect(store, 0, lambda: now + timedelta(minutes=10))
        assert _failures(store) == [run_id]
    finally:
        store.close()


def test_grace_growing_delays_a_waiting_failure_then_queues_it_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 7, lambda: now - timedelta(minutes=30))
        finished = now - timedelta(minutes=5)
        run_id = _failed_run(store, finished, "grow")
        detect.detect(store, 7, lambda: now)
        detect.detect(store, 15, lambda: finished + timedelta(minutes=14))
        assert _failures(store) == []
        detect.detect(store, 15, lambda: finished + timedelta(minutes=15, seconds=1))
        detect.detect(store, 15, lambda: finished + timedelta(minutes=20))
        assert _failures(store) == [run_id]
    finally:
        store.close()


def test_failures_before_enablement_or_past_the_horizon_are_never_queued(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        _failed_run(store, now - timedelta(days=9), "before")
        detect.detect(store, 7, lambda: now - timedelta(days=8))
        _failed_run(store, now - timedelta(days=7, minutes=30), "old")
        recent = _failed_run(store, now - timedelta(days=1), "recent")
        detect.detect(store, 7, lambda: now)
        assert _failures(store) == [recent]
    finally:
        store.close()


def test_claims_and_finished_evals_queue_no_event(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(UTC)
        detect.detect(store, 0, lambda: now - timedelta(minutes=10))
        claim = store.create_claim(
            ClaimDraft("Codagent-AI/example", 21, "I", "P-eval", "eval", "fp-eval", {})
        )
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/eval"
        )
        store.finish_run(run.id, execution_status="completed", result={})
        store.set_setting("consumed-results", run.id, {"complete": True})
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?",
            ((now - timedelta(minutes=5)).isoformat(), run.id),
        )
        store._connection.execute(
            "UPDATE claim SET created_at=? WHERE id=?",
            ((now - timedelta(minutes=6)).isoformat(), claim.id),
        )
        detect.detect(store, 0, lambda: now)
        detect.detect(store, 0, lambda: now + timedelta(minutes=1))
        assert watch_store.rows(store) == []
    finally:
        store.close()


def _contend(path: str, dispatch_id: str, results: Queue[bool]) -> None:
    store = ClaimStore(Path(path))
    try:
        won = False
        for _ in range(50):
            detect.detect(store, 0)
            won = won or watch_store.claim_launch(
                store, dispatch_id, datetime.now(UTC), 90, "claude:m:e", "/tmp/e"
            )
        results.put(won)
    finally:
        store.close()


def test_two_processes_queue_and_launch_each_event_exactly_once(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    store = ClaimStore(path)
    try:
        now = datetime.now(UTC)
        detect.detect(store, 0, lambda: now - timedelta(minutes=1))
        _failed_run(store, now - timedelta(seconds=30), "race")
        detect.detect(store, 0, lambda: now)
        row = watch_store.rows(store)[0]
    finally:
        store.close()
    context = multiprocessing.get_context("spawn")
    results: Queue[bool] = context.Queue()
    workers = [
        context.Process(target=_contend, args=(str(path), row["id"], results)) for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=5) for _ in workers) == [False, True]
    store = ClaimStore(path)
    try:
        rows = watch_store.rows(store)
        keys = [(r["event_key"], r["attempt"]) for r in rows]
        assert len(keys) == len(set(keys))
        failure = [r for r in rows if r["event_key"] == row["event_key"]]
        assert len(failure) == 1
        assert failure[0]["state"] == "launched"
    finally:
        store.close()
