#!/usr/bin/env python3
"""Read or update a Task guard's scope-<mode>.json evidence file."""

import json
import sys
from pathlib import Path


def load(path: Path) -> dict[str, object]:
    text = path.read_text() if path.is_file() else ""
    return json.loads(text) if text.strip() else {}


def main(argv: list[str]) -> None:
    command, path = argv[0], Path(argv[1])
    state = load(path)
    if command == "status":
        # Only a completed, uncrossed guard lets a push go ahead.
        print("clean" if state.get("complete") and not state.get("crossed") else "crossed", end="")
        return
    if command == "cross":
        reason = argv[2]
        if len(argv) > 3:
            reason += ": " + ", ".join(json.loads(Path(argv[3]).read_text()))
        state["crossed"] = True
        state["reasons"] = [*state.get("reasons", []), reason]  # type: ignore[misc]
    elif command == "complete":
        state["complete"] = True
    else:
        raise ValueError(f"unknown command {command}")
    path.write_text(json.dumps(state) + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: scope-state.py status|complete PATH | cross PATH REASON [LIST_FILE]")
    main(sys.argv[1:])
