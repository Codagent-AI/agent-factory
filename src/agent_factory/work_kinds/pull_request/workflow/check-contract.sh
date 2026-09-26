#!/bin/sh
set -eu
payload=$(cat)
PAYLOAD="$payload" python3 - <<'PYCODE'
import json
import os
import sys
try:
    data = json.loads(os.environ["PAYLOAD"])
    expected = data["expected_contract"]
    declared = data["declared_contract"]
except (ValueError, KeyError, TypeError) as error:
    print(f"check-contract: invalid input: {error}", file=sys.stderr)
    sys.exit(2)
if not isinstance(expected, str) or not isinstance(declared, str) or declared != expected:
    print(f"unsupported contract version: {declared} (expected {expected})", file=sys.stderr)
    sys.exit(1)
PYCODE
