from pathlib import Path

import pytest

from agent_factory.lanes import LANES, lane_for, priority_rank, rank


@pytest.mark.parametrize("lane", LANES)
def test_vocabulary(lane: str) -> None:
    for value in (lane, lane.upper(), lane.title(), f" {lane} "):
        assert lane_for(value) == lane
    assert rank(lane) == LANES.index(lane)


def test_three_lanes_with_urgent_in_high() -> None:
    assert LANES == ("high", "medium", "low")
    for value in ("Urgent", "urgent", " URGENT "):
        assert lane_for(value) == "high"
    # Urgent still ranks ahead of High in the queue.
    assert priority_rank("Urgent") < priority_rank("High") < priority_rank("Medium")


@pytest.mark.parametrize("value", [None, "", "unknown"])
def test_unknown_occupies_low(value: str | None) -> None:
    assert lane_for(value) == "low"
    # Unset and unknown values still rank after an explicit Low.
    assert priority_rank(value) > priority_rank("Low")


def test_priority_lanes_documented_once() -> None:
    full = Path("docs/operations.md").read_text()
    assert "## Priority lanes" in full
    for text in ("Urgent, then High", "Low, then cards with no or unknown Priority"):
        assert text in full
    assert "four concurrent" not in full
    for path in (
        "AGENTS.md",
        ".claude/skills/factory-assign/SKILL.md",
        ".claude/skills/factory-status/SKILL.md",
        ".claude/skills/factory-watch/SKILL.md",
    ):
        text = Path(path).read_text()
        section = text[text.index("## Priority lanes") :]
        assert '"Priority lanes" in `docs/operations.md`' in section, path
        assert "<kind> lane <lane>:" in section and "lane wait:" in section, path
        # The pointer replaces the copied rules, sizing, and rollback text.
        assert "Rollback past lanes" not in text, path


def test_slots_config() -> None:
    from agent_factory.config import ConfigurationError, SharedConfig

    text = Path("config/codagent.toml").read_text()
    assert SharedConfig.from_toml(text).slots == {"eval": 2}
    without = text[: text.index("\n[slots]")]
    assert SharedConfig.from_toml(without).slots == {}
    assert SharedConfig.from_toml(without + "\n[slots]\nfix = 3\n").slots == {"fix": 3}
    for bad, message in (
        ("bug = 2", "not a work kind"),
        ("eval = 0", ">= 1"),
        ("task = true", ">= 1"),
    ):
        with pytest.raises(ConfigurationError, match=message):
            SharedConfig.from_toml(without + f"\n[slots]\n{bad}\n")
