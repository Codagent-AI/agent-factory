from __future__ import annotations

import pytest

from agent_factory.backends.resolve import adoption_action


@pytest.mark.parametrize(
    ("state", "result_present", "expected"),
    [
        ("alive", False, "observe"),
        ("alive", True, "observe"),
        ("gone", True, "finish-result"),
        ("gone", False, "interrupt"),
        ("mismatch", True, "hold"),
        ("unknown", False, "hold"),
    ],
)
def test_adoption_decision(state: str, result_present: bool, expected: str) -> None:
    assert adoption_action(state, result_present=result_present) == expected
