#!/bin/sh
# Turns the Runner's implementation-blocked record into a needs-input outcome.
#
# implement-change stops, without a model decision to fall back on, when the lead rules that
# no compliant implementation is possible without the user. That is a question for a human,
# not a failure. With no record, this does nothing. A feature outcome also needs the stopped
# step, questions, and branch its resume reads; a fix or task outcome carries reasons only.
set -eu
payload=$(cat)
PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    data = json.loads(os.environ["PAYLOAD"])
    contract = data["contract"]
    outcome_path = Path(data["outcome_path"])
    blocked = Path(data["blocked_file"])
    branch = data.get("branch_name") or ""
except (ValueError, KeyError, TypeError) as error:
    print(f"record-blocked: invalid input: {error}", file=sys.stderr)
    sys.exit(2)
if not blocked.is_file() or not blocked.read_text().strip():
    sys.exit(0)
text = blocked.read_text().strip()
if len(text) > 4000:
    text = text[:4000].rstrip() + "..."
outcome = {
    "contract": contract,
    "outcome": "needs-input",
    "reasons": [text],
    "validator": {"status": "skipped"},
}
if contract == "factory-feature/1":
    if not branch:
        print("record-blocked: branch_name is required for a feature", file=sys.stderr)
        sys.exit(2)
    # Keep the work done so far, so the resumed attempt continues from it. A push that fails
    # ends the attempt without an outcome, which the factory retries, rather than claim a branch
    # the remote does not hold.
    pushed = subprocess.run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"], capture_output=True, text=True
    )
    if pushed.returncode != 0:
        print(f"record-blocked: push failed: {pushed.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    outcome = {
        "contract": contract,
        "outcome": "needs-input",
        "reasons": [text],
        "stopped_step": "implement",
        "questions": [text],
        "direction_summary": (
            "Implementation stopped because no compliant implementation is possible without a "
            "decision from you, recorded above. Answer on the issue: the next attempt merges "
            "the target branch and resumes at implementation."
        ),
        "branch": branch,
    }
outcome_path.parent.mkdir(parents=True, exist_ok=True)
outcome_path.write_text(json.dumps(outcome) + "\n")
PY
