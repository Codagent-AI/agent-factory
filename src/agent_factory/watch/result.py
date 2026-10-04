# ruff: noqa: E501
"""Validate headless watcher results and render stable factory-bot comments."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from agent_factory.store import Run

OWNERS = frozenset(
    {
        "factory code",
        "Agent Runner",
        "Agent Evals",
        "Agent Validator",
        "Skills",
        "environment",
        "transient",
    }
)


def _clean(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if len(value) > 4000:
        raise ValueError(f"{path} exceeds 4000 characters")
    return value.replace("<!-- agent-factory:", "")


def _strings(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return [_clean(item, path) for item in cast(list[object], value)]


def _issues(value: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "issues_filed": _strings(value.get("issues_filed"), "issues_filed"),
        "issues_updated": _strings(value.get("issues_updated"), "issues_updated"),
    }


def validate(value: object, procedure: str, *, auto_merge: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    value = cast(dict[str, Any], value)
    if procedure not in {"pr-check", "triage"}:
        raise ValueError("unknown procedure")
    if value.get("procedure") != procedure:
        raise ValueError("procedure does not match dispatch")
    if procedure == "pr-check":
        checked: dict[str, Any] = {
            "procedure": procedure,
            "summary": _clean(value.get("summary"), "summary"),
            **_issues(value),
        }
        risk = value.get("risk")
        if auto_merge:
            if not isinstance(risk, dict):
                raise ValueError("risk must be an object when auto-merge is on")
            risk = cast(dict[str, object], risk)
            level = risk.get("level")
            head = risk.get("head_sha")
            reasons = _strings(risk.get("reasons"), "risk.reasons")
            if (
                level not in {"low", "medium", "high"}
                or not isinstance(head, str)
                or not re.fullmatch(r"[a-fA-F0-9]{40}", head)
                or not reasons
                or any(not item.strip() for item in reasons)
            ):
                raise ValueError("risk needs a level, 40-hex head_sha, and nonempty reasons")
            checked["risk"] = {"level": level, "head_sha": head.lower(), "reasons": reasons}
        elif risk is not None:
            raise ValueError("risk must be absent when auto-merge is off")
        return checked
    owner = _clean(value.get("owner"), "owner")
    if owner not in OWNERS:
        raise ValueError("invalid owner")
    paused = value.get("paused_by_session")
    resumed = value.get("resumed_by_session")
    if not isinstance(paused, bool) or not isinstance(resumed, bool):
        raise ValueError("pause flags must be booleans")
    return {
        "procedure": procedure,
        "cause": _clean(value.get("cause"), "cause"),
        "evidence": _strings(value.get("evidence"), "evidence"),
        "owner": owner,
        "retry": _clean(value.get("retry"), "retry"),
        "actions": _strings(value.get("actions"), "actions"),
        **_issues(value),
        "paused_by_session": paused,
        "resumed_by_session": resumed,
        "next_step": _clean(value.get("next_step"), "next_step"),
    }


RESULT_FILE = "watch-result.json"


def procedure(row: dict[str, Any]) -> str:
    """The headless procedure a dispatch runs: a PR-READY factory check or a failure triage."""
    return "pr-check" if row["event_kind"] == "PR-READY" else "triage"


def read(path: str | Path, expected: str, *, auto_merge: bool = False) -> dict[str, Any]:
    return validate(
        json.loads(Path(path).read_text(encoding="utf-8")), expected, auto_merge=auto_merge
    )


def verdict(result: dict[str, Any], merge: dict[str, Any]) -> str:
    risk = result["risk"]
    outcome = (
        "Merged automatically" if merge["state"] == "merged" else f"Not merged: {merge['reason']}"
    )
    return "\n".join(
        (
            f"Factory risk verdict: {risk['level']}",
            f"Rated head: {risk['head_sha']}",
            "Reasons:",
            *(f"- {reason}" for reason in risk["reasons"]),
            outcome,
        )
    )


def event_line(row: dict[str, Any], run: Run | None = None) -> str:
    prefix = f"{row['event_kind']} {row['repository']}#{row['issue_number']}"
    if run is not None:
        if row["event_kind"] == "FAILURE":
            return (
                f"{prefix} {run.kind} {run.id} {run.status} {run.finished_at} "
                f"{json.dumps(run.result, sort_keys=True, separators=(',', ':'))[:200]}"
            )
        if row["event_kind"] == "PR-READY":
            return f"{prefix} {run.kind} {run.reason} {run.id} {row.get('pr_url') or ''} {run.finished_at}"
    return f"{prefix} {row.get('run_id') or row['claim_id']} {row.get('pr_url') or ''}".strip()


def triage(row: dict[str, Any], result: dict[str, Any], paused: bool) -> str:
    lines = [
        "Factory failure triage",
        f"Cause: {result['cause']}",
        f"Owner: {result['owner']}",
        "Evidence:",
        *(f"- {item}" for item in result["evidence"]),
        f"Retry: {result['retry']}",
        "Actions:",
        *(f"- {item}" for item in result["actions"]),
        f"Issues filed: {', '.join(result['issues_filed']) or 'none'}",
        f"Issues updated: {', '.join(result['issues_updated']) or 'none'}",
        f"Next step: {result['next_step']}",
        f"Factory paused: {'yes' if paused else 'no'}",
        f"Paused by session: {'yes' if result['paused_by_session'] else 'no'}",
        f"Resumed by session: {'yes' if result['resumed_by_session'] else 'no'}",
        f"Dispatch: {row['id']}",
        f"Profile: {row['profile']}",
        f"Evidence path: {row['evidence_path']}",
    ]
    return "\n".join(lines)


def notice(
    row: dict[str, Any],
    config_path: object,
    *,
    budget: bool,
    run: Run | None = None,
) -> str:
    description = (
        "No agent ran because today's watch session budget was spent."
        if budget
        else f"Watch dispatch {row['state']}: {row['detail']}"
    )
    return "\n".join(
        (
            event_line(row, run),
            description,
            f"Pull request: {row.get('pr_url') or 'none'}",
            f"Evidence path: {row.get('evidence_path') or 'none'}",
            f"Redispatch: agent-factory --config {config_path} watch redispatch {row['id']}",
        )
    )
