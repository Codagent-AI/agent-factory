"""Parsing and validation of the versioned factory-fix outcome contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

_VALID_OUTCOMES = frozenset({"pull-request", "needs-input", "failed"})


def read_outcome(evidence_path: Path, contract: str) -> Mapping[str, object] | None:
    """Read and validate `fix-outcome.json`; return None for any technical-failure case."""
    path = evidence_path / "fix-outcome.json"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload = cast(dict[str, object], payload)
    if payload.get("contract") != contract:
        return None
    if payload.get("outcome") not in _VALID_OUTCOMES:
        return None
    return payload
