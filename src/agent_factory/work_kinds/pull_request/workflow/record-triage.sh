#!/bin/sh
set -eu

# Nested JSON construction here is more involved than the flat single-field
# extraction Agent Runner's core/*.sh gate scripts do with a jq/python3 fallback,
# so this script requires python3 rather than duplicating the logic in jq.

payload=$(cat)

PAYLOAD="$payload" PYTHONPATH="$(dirname "$0")${PYTHONPATH:+:$PYTHONPATH}" python3 - <<'PY'
import json
import os
import sys
from decision_json import decision_objects

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"record-triage: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(parsed, dict):
    print("record-triage: input must be a JSON object", file=sys.stderr)
    sys.exit(2)

decision_raw = parsed.get("decision")
contract = parsed.get("contract", "factory-fix/1")
accept_field = parsed.get("accept_field", "fixable")
decision_path = parsed.get("decision_path")
# Only Task triage answers with "doable" and user visibility. The contract
# is configurable, so it cannot identify a Task.
task_schema = accept_field == "doable"
if not isinstance(contract, str) or not contract or not isinstance(accept_field, str) or not accept_field:
    print("record-triage: contract and accept_field must be non-empty strings", file=sys.stderr)
    sys.exit(2)
if decision_path is not None and (not isinstance(decision_path, str) or not decision_path):
    print("record-triage: decision_path must be a non-empty string", file=sys.stderr)
    sys.exit(2)
if not isinstance(decision_raw, str):
    print("record-triage: decision must be a string", file=sys.stderr)
    sys.exit(2)



def is_decision(value):
    basic = (
        isinstance(value, dict)
        and isinstance(value.get(accept_field), bool)
        and isinstance(value.get("reasons"), list)
        and isinstance(value.get("plan"), str)
    )
    if not basic or not task_schema:
        return basic
    return isinstance(value.get("user_visible"), bool)


try:
    decision = json.loads(decision_raw)
    parse_error = None
except json.JSONDecodeError as exc:
    decision = None
    parse_error = exc

# Valid JSON that is not itself a decision (an array holding one, say) gets the same
# search as prose does.
if not is_decision(decision):
    candidates = decision_objects(decision_raw, is_decision)
    if len(candidates) > 1:
        print(
            f"record-triage: triage decision is ambiguous: {len(candidates)} decision objects",
            file=sys.stderr,
        )
        sys.exit(2)
    if candidates:
        decision = candidates[0]
    elif parse_error is not None:
        print(f"record-triage: triage decision is not valid JSON: {parse_error}", file=sys.stderr)
        sys.exit(2)

if not isinstance(decision, dict):
    print("record-triage: triage decision must be a JSON object", file=sys.stderr)
    sys.exit(2)

if task_schema and not is_decision(decision):
    print("record-triage: task decision needs user_visible", file=sys.stderr)
    sys.exit(2)

fixable = decision.get(accept_field)
if not isinstance(fixable, bool):
    print(f"record-triage: triage decision is missing a boolean '{accept_field}' field", file=sys.stderr)
    sys.exit(2)

reasons = decision.get("reasons") or []
if not isinstance(reasons, list):
    print("record-triage: triage decision 'reasons' must be a list", file=sys.stderr)
    sys.exit(2)
reasons = [str(r) for r in reasons]
# A Task decline must tell the writer where the work belongs and what to decide. Triage is
# asked to put that in its reasons; when it lands in the plan instead, carry it along.
plan = decision.get("plan")
if task_schema and not fixable and isinstance(plan, str) and plan.strip():
    reasons.append(plan.strip())

if not fixable:
    outcome_path = parsed.get("outcome_path") or f"/artifacts/{contract.split('/', 1)[0].removeprefix('factory-')}-outcome.json"
    outcome = {
        "contract": contract,
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

if decision_path is not None:
    os.makedirs(os.path.dirname(decision_path) or ".", exist_ok=True)
    with open(decision_path, "w") as f:
        json.dump(decision, f)
        f.write("\n")

# Agent Runner keeps a text capture byte for byte, and skip_if compares it to "true".
sys.stdout.write("true" if fixable else "false")
PY
