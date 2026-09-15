#!/bin/sh
set -eu

# Nested JSON construction here is more involved than the flat single-field
# extraction Agent Runner's core/*.sh gate scripts do with a jq/python3 fallback,
# so this script requires python3 rather than duplicating the logic in jq.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"record-triage: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(parsed, dict):
    print("record-triage: input must be a JSON object", file=sys.stderr)
    sys.exit(2)

decision_raw = parsed.get("decision")
if not isinstance(decision_raw, str):
    print("record-triage: decision must be a string", file=sys.stderr)
    sys.exit(2)



def decision_objects(text):
    """Every JSON object in the captured text that has the decision's shape.

    The prompt asks for exactly one object and nothing else, but real agents still
    add prose or a markdown fence around it. A single left-to-right pass decodes at
    each opening brace and skips past what it decoded; only objects carrying a boolean
    'fixable', a 'reasons' list, and a 'plan' string count as candidates, so an
    illustrative object in prose is ignored
    and the caller can refuse an answer that offers more than one decision.
    """
    decoder = json.JSONDecoder()
    candidates = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("fixable"), bool)
            and isinstance(value.get("reasons"), list)
            and isinstance(value.get("plan"), str)
        ):
            candidates.append(value)
        index = text.find("{", max(end, index + 1))
    return candidates


try:
    decision = json.loads(decision_raw)
except json.JSONDecodeError as exc:
    candidates = decision_objects(decision_raw)
    if len(candidates) > 1:
        print(
            f"record-triage: triage decision is ambiguous: {len(candidates)} decision objects",
            file=sys.stderr,
        )
        sys.exit(2)
    if not candidates:
        print(f"record-triage: triage decision is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    decision = candidates[0]

if not isinstance(decision, dict):
    print("record-triage: triage decision must be a JSON object", file=sys.stderr)
    sys.exit(2)

fixable = decision.get("fixable")
if not isinstance(fixable, bool):
    print("record-triage: triage decision is missing a boolean 'fixable' field", file=sys.stderr)
    sys.exit(2)

reasons = decision.get("reasons") or []
if not isinstance(reasons, list):
    print("record-triage: triage decision 'reasons' must be a list", file=sys.stderr)
    sys.exit(2)
reasons = [str(r) for r in reasons]

if not fixable:
    outcome_path = parsed.get("outcome_path") or "/artifacts/fix-outcome.json"
    outcome = {
        "contract": "factory-fix/1",
        "outcome": "needs-input",
        "reasons": reasons,
        "validator": {"status": "skipped"},
    }
    out_dir = os.path.dirname(outcome_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(outcome_path, "w") as f:
        json.dump(outcome, f)
        f.write("\n")

print("true" if fixable else "false")
PY
