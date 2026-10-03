#!/usr/bin/env python3
"""Print "true" when a JSON file exists and its KEY is truthy, otherwise "false"."""

import json
import sys
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: json-flag.py PATH KEY")
    path = Path(sys.argv[1])
    value = json.loads(path.read_text()).get(sys.argv[2]) if path.is_file() else None
    print("true" if value else "false", end="")
