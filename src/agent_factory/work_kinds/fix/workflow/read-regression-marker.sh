#!/bin/sh
set -eu

# The tester's report is untrusted agent output. It arrives here as JSON on stdin
# (script_inputs) and never becomes shell source; this reduces it to the fixed
# token the workflow branches on.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"read-regression-marker: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(parsed, dict):
    print("read-regression-marker: input must be a JSON object", file=sys.stderr)
    sys.exit(2)

report = parsed.get("report")
if not isinstance(report, str):
    print("read-regression-marker: report must be a string", file=sys.stderr)
    sys.exit(2)

lines = [line.strip() for line in report.splitlines() if line.strip()]
marker = lines[-1].strip("`*_ \t") if lines else ""
print("none" if marker == "NO_REGRESSIONS_FOUND" else "found")
PY
