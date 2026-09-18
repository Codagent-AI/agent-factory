#!/bin/sh
set -eu

# Writes review-outcome.json from the triage decision and, when changes were
# attempted, the shared implementation's result file. Requires python3 for the
# same reason as record-triage.sh.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"record-review-outcome: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)
if not isinstance(parsed, dict):
    print("record-review-outcome: input must be a JSON object", file=sys.stderr)
    sys.exit(2)

outcome_path = parsed.get("outcome_path") or "/artifacts/review-outcome.json"
result_path = parsed.get("result_path") or "/artifacts/implement-result.json"
changes_needed = parsed.get("changes_needed") or "false"

decoder = json.JSONDecoder()
text = parsed.get("decision") or ""
decision = None
index = text.find("{")
while index != -1 and decision is None:
    try:
        value, end = decoder.raw_decode(text, index)
    except json.JSONDecodeError:
        index = text.find("{", index + 1)
        continue
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        decision = value
    index = text.find("{", max(end, index + 1))
if decision is None:
    print("record-review-outcome: no review decision object in the decision text", file=sys.stderr)
    sys.exit(2)

items = [item for item in decision.get("items", []) if isinstance(item, dict)]
answered = [str(item.get("id")) for item in items if item.get("decision") == "answer"]
changed = [str(item.get("id")) for item in items if item.get("decision") == "change"]
needs_input = [str(reason) for reason in decision.get("needs_input") or []]

outcome = {"contract": "factory-review/1", "answered": answered, "changed": changed}
if needs_input:
    outcome.update({"outcome": "needs-input", "reasons": needs_input})
elif changes_needed == "true":
    try:
        with open(result_path) as handle:
            result = json.load(handle)
    except (OSError, json.JSONDecodeError):
        result = None
    validator = (result or {}).get("validator", {}).get("status") if isinstance(result, dict) else None
    ci = (result or {}).get("ci", {}).get("status") if isinstance(result, dict) else None
    if result is None:
        outcome.update(
            {"outcome": "failed", "reasons": ["the implementation steps did not complete"]}
        )
    elif validator != "passed":
        outcome.update(
            {
                "outcome": "failed",
                "reasons": ["validator did not pass within its repair cycles"],
                "validator": {"status": "failed"},
            }
        )
    elif ci != "passed":
        outcome.update(
            {
                "outcome": "failed",
                "reasons": ["CI did not pass within its fix cycle"],
                "validator": {"status": "passed"},
                "ci": {"status": ci or "failed"},
            }
        )
    else:
        outcome.update(
            {
                "outcome": "pull-request",
                "validator": {"status": "passed"},
                "ci": {"status": "passed"},
                "head_sha": result.get("head_sha", ""),
            }
        )
else:
    outcome.update({"outcome": "pull-request", "validator": {"status": "skipped"}})

out_dir = os.path.dirname(outcome_path)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)
with open(outcome_path, "w") as handle:
    json.dump(outcome, handle)
    handle.write("\n")
PY
