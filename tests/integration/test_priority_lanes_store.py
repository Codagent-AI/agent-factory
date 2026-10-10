# pyright: reportPrivateUsage=false
"""INT-001: real OS processes serialize lane decisions in SQLite WAL."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory.config import JobCapConfig
from agent_factory.store import ClaimDraft, ClaimStore, LaneBusy

WORKER = """
import json, pathlib, sys, time
from agent_factory.store import ClaimStore, LaneBusy
from agent_factory.config import JobCapConfig
path, ready = sys.argv[1:]
store = ClaimStore(pathlib.Path(path), job_cap=JobCapConfig(1000,24))
original_gate = store._lane_gate
pathlib.Path(ready).touch()
for line in sys.stdin:
    claim, lane, gate, first, output = json.loads(line)
    store._lane_gate = original_gate
    if first != '-':
        def gate_fn(*args):
            pathlib.Path(first).touch()
            time.sleep(.1)
            original_gate(*args)
        store._lane_gate = gate_fn
    while not pathlib.Path(gate).exists(): time.sleep(.001)
    try:
        run = store.reserve_run(claim, 'one', lane=lane, reason='initial', evidence_path='/e')
        result = {'id': run.id}
    except LaneBusy as error:
        result = {'cause': error.cause}
    destination = pathlib.Path(output)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(result))
    temporary.replace(destination)
store.close()
"""


def _wait(path: Path) -> None:
    import time

    limit = time.monotonic() + 10
    while not path.exists():
        assert time.monotonic() < limit
        time.sleep(0.002)


@pytest.mark.parametrize("pair", ["same", "start", "continuation", "high-first", "low-first"])
def test_serializable_reservations(tmp_path: Path, pair: str) -> None:
    path = tmp_path / "state.sqlite3"
    with closing(ClaimStore(path, job_cap=JobCapConfig(1000, 24))) as store:
        store.enable_lanes()
        ready = [tmp_path / f"ready-{i}" for i in (0, 1)]
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", WORKER, str(path), str(signal)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for signal in ready
        ]
        try:
            for signal in ready:
                _wait(signal)
            for round_number in range(20 if pair in {"same", "start", "continuation"} else 1):
                claims = [
                    store.create_claim(
                        ClaimDraft("o/r", i, f"I{i}", f"P{round_number}-{i}", "fix", "fp", {})
                    )
                    for i in (1, 2)
                ]
                if pair == "continuation":
                    run = store.reserve_run(
                        claims[1].id, "previous", lane="low", reason="initial", evidence_path="/e"
                    )
                    store.mark_running(run.id, {})
                    store.finish_run(run.id, execution_status="completed", result={})
                lanes = ["high", "high" if pair == "same" else "low"]
                if pair == "low-first":
                    lanes.reverse()
                gates = [tmp_path / f"go-{round_number}-{i}" for i in (0, 1)]
                outputs = [tmp_path / f"result-{round_number}-{i}.json" for i in (0, 1)]
                locked = tmp_path / f"locked-{round_number}"
                for i, process in enumerate(processes):
                    assert process.stdin is not None
                    task = [
                        claims[i].id,
                        lanes[i],
                        str(gates[i]),
                        str(locked) if i == 0 and pair.endswith("first") else "-",
                        str(outputs[i]),
                    ]
                    process.stdin.write(json.dumps(task) + "\n")
                    process.stdin.flush()
                gates[0].touch()
                if pair.endswith("first"):
                    _wait(locked)
                gates[1].touch()
                for output in outputs:
                    _wait(output)
                results: list[dict[str, str]] = [
                    json.loads(output.read_text()) for output in outputs
                ]
                active = store.nonterminal_runs(kind="fix")
                assert len({r.lane for r in active}) == len(active)
                if pair == "same":
                    assert sum("id" in result for result in results) == 1
                    assert {"cause": "lane-busy"} in results
                elif pair == "high-first":
                    assert "id" in results[0] and results[1] == {"cause": "higher-lane"}
                elif pair in {"continuation", "low-first"}:
                    assert all("id" in result for result in results)
                else:
                    assert "id" in results[0]
                    if "id" in results[1]:
                        rows = store._connection.execute(
                            'SELECT lane FROM run WHERE status="reserved" ORDER BY created_at'
                        ).fetchall()
                        assert [row[0] for row in rows] == ["low", "high"]
                    else:
                        assert results[1] == {"cause": "higher-lane"}
                for run in active:
                    store.finish_run(run.id, execution_status="completed", result={})
        finally:
            for process in processes:
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                assert process.stderr is not None
                assert process.returncode == 0, process.stderr.read()


def test_gate_cause_selection(tmp_path: Path) -> None:
    with closing(ClaimStore(tmp_path / "db")) as store:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        holder = store.reserve_run(
            claim.id, "one", lane="high", reason="initial", evidence_path="/e"
        )
        assert store.lane_decision("fix", "urgent", None, "initial").cause == "kind-mode"
        store.enable_lanes()
        assert store.lane_decision("fix", "high", None, "initial").cause == "lane-busy"
        assert store.lane_decision("fix", "low", None, "initial").cause == "higher-lane"
        assert store.lane_decision("fix", "urgent", None, "initial").allowed
        store._connection.execute("UPDATE run SET lane=NULL WHERE id=?", (holder.id,))  # pyright: ignore[reportPrivateUsage]
        for lane in ("urgent", "high", "medium", "low"):
            with pytest.raises(LaneBusy) as error:
                store.reserve_run(
                    claim.id, "next", lane=lane, reason="recovery", evidence_path="/e"
                )
            assert error.value.cause == "legacy"
            assert error.value.holder.id == holder.id
