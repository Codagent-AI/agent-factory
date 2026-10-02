#!/usr/bin/env python3
"""Check that a guard's final gate inventory retains the triage obligations."""

import json
import sys
from pathlib import Path
from typing import Any


def check(
    triage_file: Path,
    inventory_file: Path,
    changes_file: Path | None = None,
    diff_file: Path | None = None,
) -> None:
    triage: list[dict[str, Any]] = json.loads(triage_file.read_text())["gates"]
    inventory: list[dict[str, Any]] = json.loads(inventory_file.read_text())["gates"]
    changes: list[dict[str, Any]] = (
        json.loads(changes_file.read_text())["gates"] if changes_file is not None else []
    )
    derived: list[dict[str, Any]] = (
        json.loads(diff_file.read_text())["gates"] if diff_file is not None else []
    )
    names = [gate["name"] for gate in inventory]
    if len(names) != len(set(names)):
        raise ValueError("gate names must be unique")
    by_name = {gate["name"]: gate for gate in inventory}
    for gate in triage:
        current = by_name.get(gate["name"])
        if current is None or not current.get("command") or not current.get("violation"):
            raise ValueError(f"triage gate {gate['name']} is missing")
    for gate in changes:
        current = by_name.get(gate["name"])
        if current is None or current.get("command") != gate.get("command"):
            raise ValueError(f"changed gate {gate['name']} is missing or stale")
    for gate in derived:
        current = by_name.get(gate["name"])
        if current is None or current.get("command") != gate.get("command"):
            raise ValueError(f"diff gate {gate['name']} is missing or stale")
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
