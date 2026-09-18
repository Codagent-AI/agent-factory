#!/bin/sh
set -eu

# Reduces the lead's review triage to one token for the workflow's gates:
# "needs-input" when a human decision is required, "true" when at least one item
# asks for a code change, "false" when every item only needs an answer.
# Requires python3 for the same reason as record-triage.sh.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"record-review-triage: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(parsed, dict) or not isinstance(parsed.get("decision"), str):
    print("record-review-triage: input must be a JSON object with a string decision", file=sys.stderr)
    sys.exit(2)


def is_decision(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("needs_input"), list)
        and isinstance(value.get("items"), list)
    )


text = parsed["decision"]
try:
    decision = json.loads(text)
except json.JSONDecodeError:
    decision = None
if not is_decision(decision):
    # Agents still wrap the object in prose or a fence: take the single decision object.
    decoder = json.JSONDecoder()
    candidates = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if is_decision(value):
            candidates.append(value)
        index = text.find("{", max(end, index + 1))
    if len(candidates) != 1:
        print(
            f"record-review-triage: expected one review decision object, found {len(candidates)}",
            file=sys.stderr,
        )
        sys.exit(2)
    decision = candidates[0]

items = decision["items"]
if not all(isinstance(item, dict) and item.get("decision") in {"change", "answer"} for item in items):
    print("record-review-triage: every item needs a decision of change or answer", file=sys.stderr)
    sys.exit(2)

if decision["needs_input"]:
    sys.stdout.write("needs-input")
elif any(item["decision"] == "change" for item in items):
    sys.stdout.write("true")
else:
    sys.stdout.write("false")
PY
