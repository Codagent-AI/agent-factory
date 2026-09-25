#!/usr/bin/env python3
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
for tier in ("red", "orange", "yellow", "white"):
    if not isinstance(value.get(tier), list):
        raise SystemExit(f"{tier} must be a list")
if not isinstance(value.get("accepted_head"), str) or not isinstance(
    value.get("later_commits"), list
):
    raise SystemExit("missing acceptance commit or later commits")
