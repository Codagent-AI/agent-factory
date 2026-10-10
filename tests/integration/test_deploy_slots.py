"""scripts/slots.sh reads `agent-factory status` slot lines for scripts/deploy.sh."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SLOTS = Path(__file__).resolve().parents[2] / "scripts" / "slots.sh"

FREE = "paused: false\neval slot: free\nfix slot: free\nfeature slot: free\n"


def _holds(predicate: str, status: str) -> bool:
    result = subprocess.run(
        ["bash", "-c", f'source "$1"; {predicate} "$2"', "slots", str(SLOTS), status],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr
    return result.returncode == 0


@pytest.mark.parametrize(
    ("status", "free"),
    [
        (FREE, True),
        (
            FREE.replace(
                "fix slot: free",
                "fix slot: busy (high, low)\n"
                "fix lane high: o/r#1 fix (running)\nfix lane low: o/r#2 fix (running)",
            ),
            False,
        ),
        (
            FREE.replace(
                "fix slot: free",
                "fix slot: busy (all)\nfix lane all (pre-lane attempt): o/r#1 fix (running)",
            ),
            False,
        ),
        (FREE.replace("eval slot: free", "eval slot: example/evals#3 eval (running)"), False),
        (FREE.replace("fix slot: free", "fix slot: example/work#1 fix (running)"), False),
        (FREE + "host attempts: 0\n", True),
        (FREE + "host attempts: 1\n", True),
        (
            FREE.replace("fix slot: free", "fix slot: example/work#1 fix (running)")
            + "host attempts: 0\n",
            False,
        ),
        (
            FREE.replace("feature slot: free", "feature slot: example/work#2 feature (running)"),
            False,
        ),
        ("", False),
    ],
)
def test_slots_are_free_only_when_every_slot_is_free(status: str, free: bool) -> None:
    assert _holds("slots_free", status) is free


@pytest.mark.parametrize(
    ("status", "free"),
    [(FREE + "host attempts: 0\n", True), (FREE + "host attempts: 1\n", False), (FREE, False)],
)
def test_host_slots_require_an_explicit_zero(status: str, free: bool) -> None:
    assert _holds("host_slots_free", status) is free
