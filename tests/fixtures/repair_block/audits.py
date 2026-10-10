"""Synthetic audit builder pinned to the committed installed-Runner event shape."""

from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURE = Path(__file__).with_name("audit.log")
PATH = "implement, sub:implement-task, verify-task-commit"
REASON = "A human must resolve the test blocker."


def event(path: str, name: str, **data: object) -> str:
    fixture = FIXTURE.read_text()
    matches = re.findall(r"(?:\[[^]]+\] )?" + name + r" (\{.*\})", fixture)
    assert matches, name
    keys = {key for payload in matches for key in json.loads(payload)}
    assert data.keys() <= keys, (name, data.keys() - keys)
    return "2026-01-01T00:00:00Z " + (f"[{path}] " if path else "") + name + " " + json.dumps(data)


def blocked(
    path: str = PATH,
    response: str = REASON + "\nREPAIR_BLOCKED",
    failure_kind: str = "step",
    declaration: bool = True,
) -> str:
    lines = [event("", "run_start")]
    parts = path.split(", ")
    lines.append(event(path, "step_start"))
    if declaration:
        lines.append(event(path, "repair_blocked", response=response))
    lines.append(event(path, "step_end", outcome="failed", repair_blocked=declaration))
    for index in range(len(parts) - 1, 0, -1):
        ancestor = ", ".join(parts[:index])
        name = "sub_workflow_end" if parts[index - 1].startswith("sub:") else "step_end"
        lines.append(event(ancestor, name, outcome="failed"))
    lines.append(event("", "run_end", outcome="failed", failure_kind=failure_kind))
    return "\n".join(lines) + "\n"
