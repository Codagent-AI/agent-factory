#!/usr/bin/env python3
"""Check that a guard's final gate inventory retains the triage obligations."""

import json
import sys
from pathlib import Path
from typing import Any


def load_gates(path: Path | None) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["gates"] if path is not None else []


def check(
    triage_file: Path,
    inventory_file: Path,
    changes_file: Path | None = None,
    diff_file: Path | None = None,
) -> None:
    triage = load_gates(triage_file)
    inventory = load_gates(inventory_file)
    names = [gate["name"] for gate in inventory]
    if len(names) != len(set(names)):
        raise ValueError("gate names must be unique")
    by_name = {gate["name"]: gate for gate in inventory}
    for gate in triage:
        current = by_name.get(gate["name"])
        if current is None or not current.get("command") or not current.get("violation"):
            raise ValueError(f"triage gate {gate['name']} is missing")
    # Gates the implementor changed and gates derived from the diff must both be in the
    # inventory with their current command.
    for source, path in (("changed", changes_file), ("diff", diff_file)):
        for gate in load_gates(path):
            current = by_name.get(gate["name"])
            if current is None or current.get("command") != gate.get("command"):
                raise ValueError(f"{source} gate {gate['name']} is missing or stale")
    for gate in inventory:
        if not all(
            isinstance(gate.get(field), str) and gate[field]
            for field in ("name", "command", "violation")
        ):
            raise ValueError("every gate needs a name, command and violation")


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
