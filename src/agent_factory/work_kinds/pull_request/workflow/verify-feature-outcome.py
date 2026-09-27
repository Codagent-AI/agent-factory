#!/usr/bin/env python3
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
if value.get("contract") != "factory-feature/1" or value.get("outcome") not in (
    "pull-request",
    "needs-input",
    "failed",
):
    raise SystemExit("invalid feature outcome")
