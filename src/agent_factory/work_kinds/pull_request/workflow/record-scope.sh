#!/bin/sh
set -eu
payload=$(cat)
PAYLOAD="$payload" PYTHONPATH="$(dirname "$0")${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PYCODE'
import json
import os
import sys
from pathlib import Path
from decision_json import decision_objects

try:
    data = json.loads(os.environ["PAYLOAD"])
    path = Path(data["scope_path"])
except (json.JSONDecodeError, KeyError, TypeError) as exc:
    sys.exit(f"record-scope: invalid input: {exc}")


def verdict(value):
    return (isinstance(value, dict)
            and isinstance(value.get("crossed"), bool)
            and isinstance(value.get("reasons"), list))


def parse():
    raw = data["decision"]
    if not isinstance(raw, str):
        raise ValueError("decision must be text")
    candidates = decision_objects(raw, verdict)
    if len(candidates) != 1:
        detail = "no decision object" if not candidates else f"{len(candidates)} decision objects"
        raise ValueError(detail)
    floor = json.loads(data["floor"])
    if not isinstance(floor, list):
        raise ValueError("floor must be an array")
    decision = candidates[0]
    reasons = [str(reason) for reason in decision["reasons"]]
    reasons.extend(f"Task boundary crossed by {item}; belongs in a Feature" for item in floor)
    return {"crossed": bool(floor) or decision["crossed"], "reasons": reasons}


try:
    result = parse()
except (ValueError, KeyError, TypeError) as exc:
    detail = f"Task scope guard: scope verdict could not be parsed ({exc})"
    result = {"crossed": True, "reasons": [detail]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result) + "\n")
    sys.exit(f"record-scope: {detail}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(result) + "\n")
print("crossed" if result["crossed"] else "clean", end="")
PYCODE
