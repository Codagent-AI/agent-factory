#!/usr/bin/env python3
"""Merge a guard's gate sources into the final inventory every gate is exercised from.

The lead's inventory is the starting point. Every triage gate, implementor-changed gate,
and independently derived gate whose command is not already listed is added, renamed if
its name is taken. No source can drop another's command: a reused name with a different
command adds an exercise, never a skipped gate and never a stopped attempt. The merged list
is written back over the inventory file. A gate without a name, command and violation
still fails.
"""

import json
import sys
from pathlib import Path
from typing import Any

FIELDS = ("name", "command", "violation")


def load_gates(path: Path | None) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["gates"] if path is not None else []


def merge(
    triage: list[dict[str, Any]],
    inventory: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    derived: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    final = [dict(gate) for gate in inventory]
    names = [gate.get("name") for gate in final]
    if len(names) != len(set(names)):
        raise ValueError("gate names must be unique")

    def named(name: object) -> dict[str, Any] | None:
        return next((gate for gate in final if gate.get("name") == name), None)

    def listed(command: object) -> bool:
        return any(gate.get("command") == command for gate in final)

    def add(gate: dict[str, Any], source: str) -> None:
        name = gate.get("name")
        while named(name) is not None:
            name = f"{name} ({source})"
        final.append({**gate, "name": name})

    for source, gates in (("triage", triage), ("changed", changes), ("diff", derived)):
        for gate in gates:
            if not listed(gate.get("command")):
                add(gate, source)
    for gate in final:
        if not all(isinstance(gate.get(field), str) and gate[field] for field in FIELDS):
            raise ValueError(f"gate {gate.get('name')!r} needs a name, command and violation")
    return final


def check(
    triage_file: Path,
    inventory_file: Path,
    changes_file: Path | None = None,
    diff_file: Path | None = None,
) -> list[dict[str, Any]]:
    final = merge(
        load_gates(triage_file),
        load_gates(inventory_file),
        load_gates(changes_file),
        load_gates(diff_file),
    )
    inventory_file.write_text(json.dumps({"gates": final}) + "\n")
    return final


if __name__ == "__main__":
    try:
        check(
            Path(sys.argv[1]),
            Path(sys.argv[2]),
            Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None,
            Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None,
        )
    except (KeyError, IndexError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        sys.exit(f"gate inventory failed: {error}")
