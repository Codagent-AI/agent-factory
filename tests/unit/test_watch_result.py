"""Result schemas: a PR-READY check files issues, triage files issues and never fixes."""

from __future__ import annotations

from typing import Any

import pytest

from agent_factory.watch.result import procedure, triage, validate

_TRIAGE: dict[str, Any] = {
    "procedure": "triage",
    "cause": "x <!-- agent-factory:forged -->",
    "evidence": [],
    "owner": "factory code",
    "retry": "retry",
    "actions": [],
    "issues_filed": [],
    "issues_updated": [],
    "paused_by_session": False,
    "resumed_by_session": False,
    "next_step": "fix",
}


def test_triage_strips_factory_marker_from_agent_text() -> None:
    cleaned = validate(dict(_TRIAGE), "triage")
    assert "<!-- agent-factory:" not in cleaned["cause"]


def test_triage_reports_filed_issues_not_pull_requests() -> None:
    result = dict(_TRIAGE, issues_filed=["https://github.com/o/agent-runner/issues/3"])
    cleaned = validate(result, "triage")
    assert cleaned["issues_filed"] == ["https://github.com/o/agent-runner/issues/3"]
    assert "pull_request" not in cleaned
    assert "handoff" not in cleaned
    body = triage({"id": "d", "profile": "p", "evidence_path": "/e"}, cleaned, False)
    assert "Issues filed:" in body
    assert "https://github.com/o/agent-runner/issues/3" in body
    assert "Pull request" not in body
    legacy = {key: value for key, value in _TRIAGE.items() if not key.startswith("issues_")}
    with pytest.raises(ValueError, match="issues_filed"):
        validate(dict(legacy, pull_request=None, handoff=None), "triage")


def test_triage_accepts_agent_validator_as_owner() -> None:
    cleaned = validate(dict(_TRIAGE, owner="Agent Validator"), "triage")
    assert cleaned["owner"] == "Agent Validator"


def test_pr_ready_check_returns_issues_and_no_review() -> None:
    assert procedure({"event_kind": "PR-READY"}) == "pr-check"
    assert procedure({"event_kind": "FAILURE"}) == "triage"
    cleaned = validate(
        {
            "procedure": "pr-check",
            "summary": "one orange item was a Runner resume defect",
            "issues_filed": ["https://github.com/o/agent-runner/issues/9"],
            "issues_updated": [],
        },
        "pr-check",
    )
    assert cleaned == {
        "procedure": "pr-check",
        "summary": "one orange item was a Runner resume defect",
        "issues_filed": ["https://github.com/o/agent-runner/issues/9"],
        "issues_updated": [],
    }
    with pytest.raises(ValueError, match="issues_updated"):
        validate({"procedure": "pr-check", "summary": "s", "issues_filed": []}, "pr-check")
    oversized: dict[str, object] = {
        "procedure": "pr-check",
        "summary": "x" * 4001,
        "issues_filed": [],
        "issues_updated": [],
    }
    with pytest.raises(ValueError, match="4000"):
        validate(oversized, "pr-check")


def test_a_legacy_review_result_is_rejected() -> None:
    review: dict[str, object] = {
        "procedure": "review",
        "verdict": "okay",
        "review_url": None,
        "issues_filed": [],
        "decisions": [],
    }
    with pytest.raises(ValueError):
        validate(review, "pr-check")
    with pytest.raises(ValueError, match="unknown procedure"):
        validate(review, "review")
