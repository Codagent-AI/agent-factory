#!/bin/sh
set -eu
payload=$(cat)
PAYLOAD="$payload" python3 - <<'PYCODE'
import json
import os
import sys
try:
    path = json.loads(os.environ["PAYLOAD"])["result_file"]
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
except (OSError, ValueError, KeyError, TypeError) as error:
    print(f"invalid watch result: {error}", file=sys.stderr)
    sys.exit(1)
if not isinstance(value, dict):
    print("watch result must be a JSON object", file=sys.stderr)
    sys.exit(1)
PYCODE
