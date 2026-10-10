from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.lanes import LANES, lane_for, rank
from agent_factory.store import ClaimDraft, ClaimStore, classify_reservation


@pytest.mark.parametrize("lane", LANES)
def test_vocabulary(lane: str) -> None:
    for value in (lane, lane.upper(), lane.title(), f" {lane} "):
        assert lane_for(value) == lane
    assert rank(lane) == LANES.index(lane)


@pytest.mark.parametrize("value", [None, "", "unknown"])
def test_unknown_occupies_low(value: str | None) -> None:
    assert lane_for(value) == "low"


def test_episode_classification(tmp_path: Path) -> None:
    with_store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = with_store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        run = with_store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/e"
        )
        started = replace(run, started_at="2026-01-01")
        planning = replace(run, result={"failure_stage": "pre-suite"})
        assert classify_reservation([], "initial") == "start"
        assert classify_reservation([run], "recovery") == "start"
        assert classify_reservation([planning], "initial") == "start"
        assert classify_reservation([started], "recovery") == "continuation"
        assert classify_reservation([started], "initial") == "continuation"  # eval repetition 2
        for reason in ("review", "unblock"):
            assert classify_reservation([started], reason) == "start"
            episode = replace(run, reason=reason)
            retry = replace(episode, result={"failure_stage": "pre-suite"})
            assert classify_reservation([started, retry], reason) == "start"
            launched = replace(retry, started_at="2026-01-02")
            assert classify_reservation([started, launched], reason) == "continuation"
            # A later planning failure folds into that same, already-started episode.
            assert classify_reservation([started, launched, retry], reason) == "continuation"
            assert classify_reservation([started, episode], "recovery") == "start"
            assert classify_reservation([started, launched, retry], "quota") == "continuation"
            # Another review/unblock after a successful episode is a fresh start.
            assert (
                classify_reservation([started, replace(episode, started_at="x")], reason) == "start"
            )
    finally:
        with_store.close()
