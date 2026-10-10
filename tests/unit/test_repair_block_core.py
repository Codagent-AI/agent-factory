"""Audit causality, supersession, explanation, resume arithmetic and additive contract."""

from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_factory.work_kinds.pull_request.handler import (
    _feature_stop_message,  # pyright: ignore[reportPrivateUsage]
    feature_resume_point,
)
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome
from tests.fixtures.repair_block.audits import PATH, REASON, blocked, event
from tests.integration.test_feature_workflow_scripts import PACKAGE

rb: Any = SimpleNamespace(**runpy.run_path(str(PACKAGE / "repair-block.py")))


@pytest.mark.parametrize(
    "path", [PATH, "implement:2, sub:task, check:3", "define, sub:factory-define, specs, check"]
)
def test_causal_nested_block(path: str) -> None:
    events = rb.parse_audit(blocked(path).splitlines())
    assert rb.causal_endpoint(events) == path
    assert rb.blocked_response(events, path, "path") == (path, REASON)


@pytest.mark.parametrize(
    "tail",
    [
        event(PATH, "step_start"),
        event(PATH, "step_end", outcome="success"),
        event(PATH, "repair_blocked", response="REPAIR_BLOCKED"),
        "2026-01-01T00:00:00Z [" + PATH + "] repair_blocked invalid json",
        event(PATH, "repair_blocked", response=3),
    ],
)
def test_path_supersession(tail: str) -> None:
    events = rb.parse_audit((blocked() + tail).splitlines())
    assert rb.blocked_response(events, PATH, "path") is None


def test_supersession_scopes() -> None:
    endpoint = "archive, sub:archive-change"
    first = endpoint + ", archive-transition"
    later = endpoint + ", verify-archive-commit"
    events = rb.parse_audit(
        [
            event(first, "repair_blocked", response=REASON),
            event(first, "step_end", outcome="failed"),
            event(later, "step_start"),
            event(later, "step_end", outcome="failed"),
        ]
    )
    assert rb.blocked_response(events, first, "path") == (first, REASON)
    assert rb.blocked_response(events, endpoint, "subtree") is None


@pytest.mark.parametrize(
    "audit",
    [
        blocked(failure_kind="infrastructure"),
        blocked(declaration=False),
        blocked().rsplit("\n", 2)[0],
        blocked() + event("", "run_start"),
        blocked() + "\n" + blocked("unrelated", declaration=False),
        blocked()
        + "\n"
        + event("unrelated", "step_end", outcome="success")
        + "\n"
        + event("", "run_end", outcome="failed", failure_kind="step"),
    ],
)
def test_noncausal_blocks(audit: str) -> None:
    assert rb.causal_endpoint(rb.parse_audit(audit.splitlines())) is None


@pytest.mark.parametrize(
    "response,expected",
    [
        ("  why\nREPAIR_BLOCKED\n ", "why"),
        ("REPAIR_BLOCKED", ""),
        ("\nREPAIR_BLOCKED", ""),
        ("why REPAIR_BLOCKED", "why REPAIR_BLOCKED"),
        ("why", "why"),
        (None, ""),
    ],
)
def test_explanation(response: object, expected: str) -> None:
    assert rb.explanation(response) == expected


def test_loop_ancestry_and_order() -> None:
    assert rb.descendant("implement:2, check", "implement")
    assert not rb.descendant("implementation", "implement")
    for start in ("", *rb.ORDER):
        for checkpoint in ("", "implement", "archive", "verify"):
            points = [point for point in (start, checkpoint) if point]
            expected = max(points, key=rb.ORDER.index) if points else ""
            assert rb.later(start, checkpoint) == expected


@pytest.mark.parametrize("blocked_step", ["check", "", None, 3])
@pytest.mark.parametrize("resume", ["", "design"])
def test_outcome_contract(tmp_path: Path, blocked_step: object, resume: str) -> None:
    outcome = rb.build_outcome("check", REASON, resume, "")
    outcome["blocked_step"] = blocked_step
    path = tmp_path / "feature-outcome.json"
    path.write_text(json.dumps(outcome))
    expected = blocked_step == "check"
    assert (read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is not None) == expected
    done = subprocess.run(
        ["python3", str(PACKAGE / "verify-feature-outcome.py"), str(path)],
        capture_output=True,
        text=True,
    )
    assert (done.returncode == 0) == expected
    del outcome["blocked_step"]
    path.write_text(json.dumps(outcome))
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_stop_and_fresh_resume() -> None:
    outcome = rb.build_outcome("define, specs, check", REASON, "", "")
    message = _feature_stop_message(outcome, "o/r")
    assert REASON in message and "define, specs, check" in message
    assert "No branch was published; the next attempt starts fresh." in message
    assert feature_resume_point("needs-input", None, None, False, False) == ""


@pytest.mark.parametrize("outcome", ["needs-input", "failed", "pull-request"])
def test_standalone_legacy_outcome_validation(tmp_path: Path, outcome: str) -> None:
    path = tmp_path / "feature-outcome.json"
    # The standalone script historically checked only contract and outcome. The
    # supervisor's stricter validation remains responsible for legacy payload fields.
    path.write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": outcome,
                "stopped_step": "",
                "branch": None,
            }
        )
    )
    done = subprocess.run(
        ["python3", str(PACKAGE / "verify-feature-outcome.py"), str(path)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("context", ["run", "archive"])
def test_concurrent_outcome_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context: str
) -> None:
    session = tmp_path / "session"
    artifacts = tmp_path / "artifacts"
    session.mkdir()
    artifacts.mkdir()
    path = "archive, sub:archive-change, check" if context == "archive" else PATH
    (session / "audit.log").write_text(blocked(path))

    def unpublished(_branch: str) -> bool:
        return False

    monkeypatch.setitem(rb.record.__globals__, "published", unpublished)
    original = json.dumps(
        {
            "contract": "factory-feature/1",
            "outcome": "needs-input",
            "blocked_step": "another check",
            "questions": ["why"],
            "direction_summary": "Another recorder finished",
        }
    )

    def race(_source: object, destination: Path) -> None:
        destination.write_text(original)
        raise FileExistsError("another recorder published first")

    monkeypatch.setattr(rb.os, "link", race)
    rb.record(context, session, artifacts, "claim")
    assert (artifacts / "feature-outcome.json").read_text() == original
    assert sorted(file.name for file in artifacts.iterdir()) == ["feature-outcome.json"]
