"""Marker, message, and result contracts without external sessions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_factory.notify import marker
from agent_factory.notify.deliver import PHRASES, render
from agent_factory.notify.supervise import parse_result
from agent_factory.store import Claim

FIRST = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"
SECOND = "c2ae018f-230c-437f-bd07-ff9f49ab6a83"


def test_marker_last_valid_and_safe_name() -> None:
    now = datetime(2026, 10, 3, tzinfo=UTC)
    first = marker.render(FIRST, "bad-->name", now)
    second = marker.render(SECOND, "agent-factory-ab", now)
    assert '"name":"bad-name"' in first
    assert '"name":"bad>"' not in first
    body = first + "\n<!-- codagent-session: {oops} -->\n" + second
    assert marker.parse(body) == {
        "session_id": SECOND,
        "name": "agent-factory-ab",
        "recorded_at": "2026-10-03T00:00:00Z",
    }
    assert marker.parse('<!-- codagent-session: {"session_id":"bad"} -->') is None


def test_stamp_and_carry_preserve_prose() -> None:
    original = "prose with trailing spaces  \n"
    stamped = marker.stamp(original, FIRST, "session")
    assert stamped.startswith(original)
    assert stamped.count("codagent-session:") == 1
    restamped = marker.stamp(stamped, SECOND, "other")
    assert restamped.count("codagent-session:") == 1
    assert marker.parse(restamped)["session_id"] == SECOND  # type: ignore[index]
    edited = marker.carry(restamped, "new prose\n")
    assert edited.startswith("new prose\n")
    assert restamped.split("<!-- codagent-session:")[1] == edited.split("<!-- codagent-session:")[1]
    assert marker.carry("unmarked", original) == original


def test_replacing_a_marker_keeps_every_other_byte() -> None:
    """Acceptance round 0 F-1: replacing a marker must not move it or touch the prose."""
    now = datetime(2026, 10, 3, tzinfo=UTC)
    old = marker.render(FIRST, "first", now)
    new = marker.render(SECOND, "second", now)
    for before, after in (
        ("Alpha\n" + old + "\nBeta\n", "Alpha\n" + new + "\nBeta\n"),
        ("x\n\n" + old + "\n\n\n", "x\n\n" + new + "\n\n\n"),
        ("x\n\n" + old, "x\n\n" + new),
        (old + "\nmiddle\n" + old + "\nend\n", "middle\n" + new + "\nend\n"),
    ):
        assert marker.stamp(before, SECOND, "second", now) == after
        assert marker.carry(new, before) == after


def test_each_message_phrase_has_only_required_links() -> None:
    claim = Claim("claim", "o/r", 12, "I", "P", "fix", "fp", {}, "settled", {}, {}, {}, {})
    for kind, phrase in PHRASES.items():
        row = {"stop_kind": kind}
        message = render(row, claim)
        assert phrase in message.splitlines()[0]
        assert message.splitlines()[1:] == [
            "Issue: https://github.com/o/r/issues/12",
            "Claim: claim",
        ]
    assert "Pull request: https://github.com/o/r/pull/1" in render(
        {"stop_kind": "pull-request"}, claim, "https://github.com/o/r/pull/1"
    )


def test_structured_result_parser(tmp_path: Path) -> None:
    path = tmp_path / "stdout.json"
    path.write_text(
        json.dumps(
            {
                "is_error": False,
                "total_cost_usd": 0.01,
                "structured_output": {"outcome": "sent", "detail": "ok"},
            }
        )
    )
    assert parse_result(path) == ("sent", "ok", 0.01)
    path.write_text("oops")
    assert parse_result(path)[0] == "failed"
