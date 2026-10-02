#!/usr/bin/env python3
"""Record already pushed non-chore subjects without rewriting them."""

import json
import re
import subprocess
import sys
from pathlib import Path


def check(base: str, output: Path) -> None:
    subjects = subprocess.check_output(
        ["git", "log", "--format=%s", f"{base}..HEAD"], text=True
    ).splitlines()
    bad = [
        subject for subject in subjects if re.match(r"^(?:\[[^]]+\]\s*)?chore:\s+", subject) is None
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bad) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: check-chore-subjects.py BASE OUTPUT")
    check(sys.argv[1], Path(sys.argv[2]))
