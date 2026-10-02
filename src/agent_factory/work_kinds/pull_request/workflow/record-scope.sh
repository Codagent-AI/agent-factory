#!/bin/sh
set -eu
payload=$(cat)
PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

data = json.loads(os.environ["PAYLOAD"])
decision = json.loads(data["decision"])
floor = json.loads(data["floor"])
if not isinstance(decision, dict) or not isinstance(decision.get("crossed"), bool):
    sys.exit("record-scope: expected crossed boolean")
if not isinstance(decision.get("reasons"), list) or not isinstance(floor, list):
    sys.exit("record-scope: expected reasons and floor arrays")
crossed = bool(floor) or decision["crossed"]
reasons = [str(reason) for reason in decision["reasons"]]
reasons.extend(f"Task boundary crossed by {path}; belongs in a Feature" for path in floor)
path = Path(data["scope_path"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({"crossed": crossed, "reasons": reasons}) + "\n")
print("crossed" if crossed else "clean", end="")
PY
