# ruff: noqa: E501
"""Validate headless watcher results and render stable operator comments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from agent_factory.store import Claim, Run

OWNERS = frozenset(
    {"factory code", "Agent Runner", "Agent Evals", "Skills", "environment", "transient"}
)


def _clean(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if len(value) > 4000:
        raise ValueError(f"{path} exceeds 4000 characters")
    return value.replace("<!-- agent-factory:", "")


def _optional(value: object, path: str) -> str | None:
    return None if value is None else _clean(value, path)


def _strings(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return [_clean(item, path) for item in cast(list[object], value)]


def validate(value: object, procedure: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    value = cast(dict[str, Any], value)
    if value.get("procedure") != procedure:
        raise ValueError("procedure does not match dispatch")
    if procedure == "review":
        decisions = value.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("decisions must be an array")
        decisions = cast(list[object], decisions)
        if len(decisions) > 10:
            raise ValueError("decisions must have at most 10 items")
        clean_decisions: list[dict[str, object]] = []
        for index, decision in enumerate(decisions):
            if not isinstance(decision, dict):
                raise ValueError(f"decisions[{index}] must be an object")
            decision = cast(dict[str, object], decision)
            options = decision.get("options")
            if not isinstance(options, list):
                raise ValueError("decision options must be an array")
            clean_options: list[dict[str, str]] = []
            for option in cast(list[object], options):
                if not isinstance(option, dict):
                    raise ValueError("decision option must be an object")
                option = cast(dict[str, object], option)
                clean_options.append(
                    {
                        "label": _clean(option.get("label"), "label"),
                        "consequence": _clean(option.get("consequence"), "consequence"),
                    }
                )
            clean_decisions.append(
                {
                    "question": _clean(decision.get("question"), "question"),
                    "context": _clean(decision.get("context"), "context"),
                    "options": clean_options,
                    "recommendation": _clean(decision.get("recommendation"), "recommendation"),
                }
            )
        return {
            "procedure": procedure,
            "verdict": _clean(value.get("verdict"), "verdict"),
            "review_url": _optional(value.get("review_url"), "review_url"),
            "issues_filed": _strings(value.get("issues_filed"), "issues_filed"),
            "decisions": clean_decisions,
        }
    if procedure != "triage":
        raise ValueError("unknown procedure")
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
        "pull_request": _optional(value.get("pull_request"), "pull_request"),
        "paused_by_session": paused,
        "resumed_by_session": resumed,
        "next_step": _clean(value.get("next_step"), "next_step"),
        "handoff": _optional(value.get("handoff"), "handoff"),
    }


RESULT_FILE = "watch-result.json"


def procedure(row: dict[str, Any]) -> str:
    """The headless procedure a dispatch runs: a PR review or a failure triage."""
    return "review" if row["event_kind"] == "PR-READY" else "triage"


def read(path: str | Path, expected: str) -> dict[str, Any]:
    return validate(json.loads(Path(path).read_text(encoding="utf-8")), expected)


def event_line(row: dict[str, Any], claim: Claim | None = None, run: Run | None = None) -> str:
    prefix = f"{row['event_kind']} {row['repository']}#{row['issue_number']}"
    if run is not None:
        if row["event_kind"] == "FAILURE":
            return (
                f"{prefix} {run.kind} {run.id} {run.status} {run.finished_at} "
                f"{json.dumps(run.result, sort_keys=True, separators=(',', ':'))[:200]}"
            )
        if row["event_kind"] == "EVAL-DONE":
            return f"{prefix} {run.id} {run.status} {run.finished_at}"
        if row["event_kind"] == "PR-READY":
            return f"{prefix} {run.kind} {run.reason} {run.id} {row.get('pr_url') or ''} {run.finished_at}"
    if claim is not None and row["event_kind"] == "CLAIM":
        return f"{prefix} {claim.kind} {claim.id} {claim.lifecycle} {row['event_at']}"
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
        f"Pull request: {result['pull_request'] or 'none'}",
        f"Next step: {result['next_step']}",
        f"Handoff: {result['handoff'] or 'none'}",
        f"Factory paused: {'yes' if paused else 'no'}",
        f"Paused by session: {'yes' if result['paused_by_session'] else 'no'}",
        f"Resumed by session: {'yes' if result['resumed_by_session'] else 'no'}",
        f"Dispatch: {row['id']}",
        f"Profile: {row['profile']}",
        f"Evidence path: {row['evidence_path']}",
    ]
    return "\n".join(lines)


def decisions(result: dict[str, Any], operator: str) -> str:
    lines = ["Factory review decisions"]
    if operator:
        lines.append(f"@{operator}")
    for index, decision in enumerate(result["decisions"], 1):
        lines.extend([f"{index}. {decision['question']}", decision["context"]])
        lines.extend(
            f"- {option['label']}: {option['consequence']}" for option in decision["options"]
        )
        lines.append(f"Recommended: {decision['recommendation']}")
    return "\n".join(lines)


def notice(
    row: dict[str, Any],
    config_path: object,
    *,
    budget: bool,
    claim: Claim | None = None,
    run: Run | None = None,
) -> str:
    description = (
        "No agent ran because today's watch session budget was spent."
        if budget
        else f"Watch dispatch {row['state']}: {row['detail']}"
    )
    return "\n".join(
        (
            event_line(row, claim, run),
            description,
            f"Pull request: {row.get('pr_url') or 'none'}",
            f"Evidence path: {row.get('evidence_path') or 'none'}",
            f"Redispatch: agent-factory --config {config_path} watch redispatch {row['id']}",
        )
    )
