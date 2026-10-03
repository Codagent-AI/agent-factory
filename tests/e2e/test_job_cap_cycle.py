"""Operator-facing cap behavior through the CLI and the controlled fix harness."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from test_fix_cycle import Harness, _pr_outcome  # pyright: ignore[reportPrivateUsage]


def _cap_one(harness: Harness) -> None:
    shared = harness.config.parent / "shared.toml"
    shared.write_text(shared.read_text() + "\n[job_cap]\nattempts = 1\nwindow_hours = 24\n")


def test_new_ready_card_waits_until_cli_reset(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    _cap_one(harness)
    harness.tick()
    first = harness.active_run()
    artifact = harness.wait_started(first)
    second_artifact: Path | None = None
    try:
        harness.finish(artifact, _pr_outcome(harness.branch_for(first.claim_id)))
        board = harness.state()
        item = copy.deepcopy(board["items"][0])
        item["id"] = "P2"
        item["content"]["id"] = "I2"
        item["content"]["number"] = 2
        for field in item["fieldValues"]["nodes"]:
            if field["field"]["id"] == harness.shared.project.status.id:
                field["optionId"] = harness.shared.project.status.option("ready")
        board["items"].append(item)
        harness.board.write_text(json.dumps(board))
        harness.tick()
        harness.tick()
        assert harness.store.claims_for_item("P2") == []
        assert sum("factory job cap is reached" in body for body in harness.comments()) == 1
        status = harness.cli("status")
        assert "job cap: 1/1 attempts in the last 24 h" in status
        assert "job cap waiting: example/work#2" in status
        assert "earliest clear" in status
        harness.cli("pause")
        harness.cli("resume")
        harness.tick()
        assert harness.store.claims_for_item("P2") == []
        assert "held work can start in the next cycle" in harness.cli("job-cap", "reset")
        harness.tick()
        second = harness.active_run()
        assert second.claim_id != first.claim_id
        second_artifact = harness.wait_started(second)
    finally:
        (artifact / "finish").touch()
        if second_artifact is not None:
            (second_artifact / "finish").touch()
        harness.store.close()


def test_ready_retry_waits_under_same_claim_until_cli_reset(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    _cap_one(harness)
    harness.tick()
    first = harness.active_run()
    artifact = harness.wait_started(first)
    retry_artifact: Path | None = None
    try:
        harness.finish(artifact, "crash")
        harness.tick()
        harness.tick()
        claim = harness.store.get_claim(first.claim_id)
        assert claim is not None
        assert len(harness.store.runs_for_claim(first.claim_id)) == 1
        assert harness.store.get_hold(first.claim_id, "job-cap") is not None
        assert sum("factory job cap is reached" in body for body in harness.comments()) == 1
        status = harness.cli("status")
        assert "job cap: 1/1 attempts in the last 24 h" in status
        assert "blocking condition: factory job cap reached" in status
        harness.cli("pause")
        harness.cli("resume")
        harness.tick()
        assert len(harness.store.runs_for_claim(first.claim_id)) == 1
        reset = harness.cli("job-cap", "reset")
        assert "0/1 attempts" in reset
        assert "held work can start in the next cycle" in reset
        harness.tick()
        retry = harness.active_run()
        assert retry.claim_id == first.claim_id
        assert retry.attempt_number == first.attempt_number + 1
        retry_artifact = harness.wait_started(retry)
    finally:
        (artifact / "finish").touch()
        if retry_artifact is not None:
            (retry_artifact / "finish").touch()
        harness.store.close()
