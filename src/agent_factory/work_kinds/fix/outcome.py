"""Parsing and validation of the versioned factory-fix outcome contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_VALID_OUTCOMES = frozenset({"pull-request", "needs-input", "failed"})


@dataclass(frozen=True)
class InterpretedOutcome:
    execution_status: str
    product_verdict: str
    result: dict[str, object]


@dataclass(frozen=True)
class OutcomeRead:
    outcome: InterpretedOutcome | None
    error: str | None = None


def _outcome_path(evidence_path: Path, contract: str) -> Path:
    if contract == "factory-review/1":
        name = "review-outcome.json"
    elif contract == "factory-feature/1":
        name = "feature-outcome.json"
    else:
        name = "fix-outcome.json"
    return evidence_path / name


def read_interpreted_outcome(evidence_path: Path, contract: str) -> OutcomeRead:
    """Read the same contract and verdict used by the work-kind result handler."""
    path = _outcome_path(evidence_path, contract)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OutcomeRead(None)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return OutcomeRead(None, f"cannot read {path}: {type(error).__name__}: {error}")
    if not isinstance(payload, dict):
        return OutcomeRead(None, f"invalid outcome object in {path}")
    value = cast(dict[str, object], payload)
    if value.get("contract") != contract or value.get("outcome") not in _VALID_OUTCOMES:
        return OutcomeRead(None, f"invalid contract or outcome in {path}")
    verdict = cast(str, value["outcome"])
    return OutcomeRead(InterpretedOutcome("completed", verdict, value))


def read_outcome(evidence_path: Path, contract: str) -> Mapping[str, object] | None:
    """Read the versioned outcome; return None for any technical-failure case."""
    interpreted = read_interpreted_outcome(evidence_path, contract).outcome
    return interpreted.result if interpreted is not None else None
