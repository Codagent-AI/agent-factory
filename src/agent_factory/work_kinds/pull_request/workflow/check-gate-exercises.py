#!/usr/bin/env python3
"""Validate a Task gate's planted positive and negative exercise evidence."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def check(artifact_dir: Path, gates_file: Path | None = None) -> None:
    triage: dict[str, Any] = json.loads(
        (gates_file or artifact_dir / "task-triage.json").read_text()
    )
    exercises: list[dict[str, Any]] = json.loads((artifact_dir / "gate-exercises.json").read_text())
    verdicts: list[dict[str, Any]] = json.loads((artifact_dir / "gate-verdicts.json").read_text())
    by_name = {item["name"]: item for item in exercises}
    confirmed = {item["name"]: item for item in verdicts}
    gates = triage.get("gates", [])
    if len(by_name) != len(gates) or len(confirmed) != len(gates):
        raise ValueError("every named gate needs exactly one exercise and verdict")
    for gate in gates:
        name = gate["name"]
        exercise = by_name[name]
        verdict = confirmed[name]
        positive = exercise["positive"]
        negative = exercise["negative"]
        command = gate["command"]
        if any(item.get("command") != command for item in (exercise, positive, negative)):
            raise ValueError(f"gate {name}: command differs from triage")
        if positive["exit"] != 0 or negative["exit"] == 0:
            raise ValueError(f"gate {name}: positive or negative exit is wrong")
        patch = artifact_dir / negative["patch"]
        if not patch.is_file() or not patch.read_bytes():
            raise ValueError(f"gate {name}: planted patch is empty")
        good = (artifact_dir / positive["log"]).read_text()
        bad = (artifact_dir / negative["log"]).read_text()
        diagnostic = verdict.get("diagnostic")
        if (
            verdict.get("confirmed") is not True
            or verdict.get("criterion_met") is not True
            or not isinstance(diagnostic, str)
            or not diagnostic
            or diagnostic not in bad
            or diagnostic in good
        ):
            raise ValueError(f"gate {name}: negative diagnostic was not confirmed")
    if subprocess.check_output(["git", "status", "--porcelain"]):
        raise ValueError("the working tree is not clean")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    except (KeyError, IndexError, OSError, ValueError, json.JSONDecodeError) as error:
        sys.exit(f"gate exercises failed: {error}")
    print("passed")
