"""E2E-001: several fix priorities plus a task through public tick/status."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

from agent_factory.store import Run
from tests.e2e.test_fix_cycle import Harness


def _add(h: Harness, number: int, priority: str, *, task: bool = False) -> None:
    data = h.state()
    item: dict[str, Any] = (
        copy.deepcopy(data["items"][0])
        if data["items"]
        else {
            "content": {
                "__typename": "Issue",
                "repository": {"nameWithOwner": "example/work"},
                "author": {"login": "writer"},
                "state": "OPEN",
                "body": "maintenance",
                "labels": {"nodes": []},
            },
            "fieldValues": {"nodes": []},
        }
    )
    item["id"] = f"P{number}"
    item["content"].update(
        id=f"I{number}", number=number, issueType={"name": "Task" if task else "Bug"}
    )
    item["fieldValues"]["nodes"] = [
        {
            "field": {"id": h.shared.project.status.id},
            "optionId": h.shared.project.status.option("ready"),
        },
        {
            "field": {"id": h.shared.project.owner.id},
            "optionId": h.shared.project.owner.option("factory"),
        },
        {
            "field": {"id": h.shared.project.priority_id, "name": "Priority"},
            "name": priority,
            "optionId": priority,
        },
    ]
    data["items"].append(item)
    h.board.write_text(json.dumps(data))


def _release(h: Harness, run: Run, artifacts: dict[str, Path]) -> None:
    claim = h.store.get_claim(run.claim_id)
    assert claim is not None
    branch = f"factory/{run.kind}-{claim.issue_number}-{claim.id[:8]}"
    result = json.dumps(
        {
            "contract": f"factory-{run.kind}/1",
            "outcome": "pull-request",
            "pr": {
                "url": f"https://github.com/example/work/pull/{claim.issue_number + 200}",
                "number": claim.issue_number + 200,
                "branch": branch,
                "head_sha": "f" * 40,
            },
            "validator": {"status": "passed"},
            "ci": {"status": "passed"},
        }
    )
    artifact = artifacts[run.id]
    staged = artifact / "finish.tmp"
    staged.write_text(result)
    staged.replace(artifact / "finish")


def _wait_finished(h: Harness, run: Run) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = h.store.get_run(run.id)
        assert current is not None
        if current.finished_at:
            return
        time.sleep(0.02)
    raise AssertionError("stub attempt did not finish")


def test_public_tick_fills_priority_lanes(tmp_path: Path) -> None:
    h = Harness(tmp_path, execution="host", kind="task")
    h.cli_timeout = 90  # Several real clones and supervisors contend with parallel CI tests.
    config = (
        h.config.read_text()
        .replace("inactivity_seconds = 20", "inactivity_seconds = 300")
        .replace("execution_seconds = 30", "execution_seconds = 600")
        .replace("total_seconds = 60", "total_seconds = 900")
    )
    h.config.write_text(config)
    h.cli("lanes", "enable")
    data = h.state()
    data["items"].clear()
    h.board.write_text(json.dumps(data))
    artifacts: dict[str, Path] = {}
    try:
        for number, priority in [(1, "Low"), (2, "Medium"), (3, "High")]:
            _add(h, number, priority)
            h.tick()
            active = h.store.nonterminal_runs(kind="fix")
            run = next(r for r in active if h.store.get_claim(r.claim_id).issue_number == number)  # type: ignore[union-attr]
            artifacts[run.id] = h.wait_started(run)
            assert run.lane == priority.lower()
            assert len(active) == number
        _add(h, 4, "High")
        _add(h, 5, "Low")
        _add(h, 6, "Low", task=True)
        h.tick()
        (task,) = h.store.nonterminal_runs(kind="task")
        artifacts[task.id] = h.wait_started(task)
        assert len(h.store.nonterminal_runs(kind="fix")) == 3
        assert not h.store.claims_for_item("P4") and not h.store.claims_for_item("P5")
        status = h.cli("status")
        assert "fix slot: busy (high, medium, low)" in status
        for lane, number in [("low", 1), ("medium", 2), ("high", 3)]:
            assert f"fix lane {lane}: example/work#{number}" in status
        assert "example/work#4 waits for fix lane high" in status
        assert "example/work#5 waits for fix lane low" in status
        high = next(r for r in h.store.nonterminal_runs(kind="fix") if r.lane == "high")
        _release(h, high, artifacts)
        _wait_finished(h, high)
        h.tick()
        second_high = next(r for r in h.store.nonterminal_runs(kind="fix") if r.lane == "high")
        assert h.store.get_claim(second_high.claim_id).issue_number == 4  # type: ignore[union-attr]
        artifacts[second_high.id] = h.wait_started(second_high)
        assert not h.store.claims_for_item("P5")
        for run in h.store.nonterminal_runs():
            _release(h, run, artifacts)
        for run in h.store.nonterminal_runs():
            _wait_finished(h, run)
        h.tick()
        (last,) = h.store.nonterminal_runs(kind="fix")
        assert h.store.get_claim(last.claim_id).issue_number == 5  # type: ignore[union-attr]
        artifacts[last.id] = h.wait_started(last)
        _release(h, last, artifacts)
        _wait_finished(h, last)
        h.tick()
        assert "fix slot: free" in h.cli("status") and "task slot: free" in h.cli("status")
        for item in h.state()["items"]:
            fields = {f["field"]["id"]: f for f in item["fieldValues"]["nodes"]}
            assert fields[h.shared.project.status.id]["optionId"] == h.shared.project.status.option(
                "review"
            )
            (claim,) = h.store.claims_for_item(item["id"])
            pr = claim.outcome["pr"]
            assert isinstance(pr, dict)
            assert f"pull/{item['content']['number'] + 200}" in pr["url"]
            assert any(pr["url"] in body for body in h.comments())
    finally:
        for run in h.store.nonterminal_runs():
            artifacts.setdefault(
                run.id, Path(run.evidence_path) / f"attempt-{run.attempt_number + 1}"
            )
            artifacts[run.id].mkdir(parents=True, exist_ok=True)
            _release(h, run, artifacts)
        h.store.close()
