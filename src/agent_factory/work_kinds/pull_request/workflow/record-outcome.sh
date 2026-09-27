#!/bin/sh
set -eu

# See record-triage.sh for why this requires python3 rather than a jq
# fallback: the outcome shape here is conditionally assembled, not a flat
# single-field extraction.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    parsed = json.loads(os.environ["PAYLOAD"])
except json.JSONDecodeError as exc:
    print(f"record-outcome: invalid JSON input: {exc}", file=sys.stderr)
    sys.exit(2)

if not isinstance(parsed, dict):
    print("record-outcome: input must be a JSON object", file=sys.stderr)
    sys.exit(2)

contract = parsed.get("contract")
outcome_path = parsed.get("outcome_path")
if not isinstance(contract, str) or not contract or not isinstance(outcome_path, str) or not outcome_path:
    print("record-outcome: contract and outcome_path are required strings", file=sys.stderr)
    sys.exit(2)
validator_status = parsed.get("validator_status") or "failed"
ci_status = parsed.get("ci_status") or ""
annotation_status = parsed.get("annotation_status") or "passed"
branch_name = parsed.get("branch_name") or ""

# A malformed structured input means an upstream step produced something this
# script cannot trust; failing here makes verify-outcome report a technical
# failure instead of recording a plausible but wrong outcome.
pr_details_raw = parsed.get("pr_details") or "{}"
try:
    pr_details = json.loads(pr_details_raw)
except (json.JSONDecodeError, TypeError) as exc:
    print(f"record-outcome: pr_details is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(2)
if not isinstance(pr_details, dict):
    print("record-outcome: pr_details must be a JSON object", file=sys.stderr)
    sys.exit(2)

reasons_raw = parsed.get("reasons") or "[]"
try:
    reasons = json.loads(reasons_raw)
except (json.JSONDecodeError, TypeError) as exc:
    print(f"record-outcome: reasons is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(2)
if not isinstance(reasons, list):
    print("record-outcome: reasons must be a JSON array", file=sys.stderr)
    sys.exit(2)
reasons = [str(r) for r in reasons]

pr_url = pr_details.get("url") or ""


def pr_reference():
    return {
        "url": pr_url,
        "number": pr_details.get("number"),
        "branch": branch_name,
        "head_sha": pr_details.get("headRefOid") or pr_details.get("head_sha") or "",
    }


if validator_status != "passed":
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons or ["validator did not pass within its repair cycles"],
        "validator": {"status": "failed"},
    }
elif not pr_url:
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons or ["failed to push the branch or open a pull request"],
        "validator": {"status": "passed"},
    }
elif annotation_status != "passed":
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons or ["pull request annotation failed"],
        "pr": pr_reference(),
        "validator": {"status": "passed"},
        "ci": {"status": ci_status or "failed"},
    }
elif ci_status == "passed":
    outcome = {
        "contract": contract,
        "outcome": "pull-request",
        "pr": pr_reference(),
        "validator": {"status": "passed"},
        "ci": {"status": "passed"},
    }
else:
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons or ["CI did not pass within its fix cycle"],
        "pr": pr_reference(),
        "validator": {"status": "passed"},
        "ci": {"status": ci_status or "failed"},
    }

for key in ("stopped_step", "review_attention_counts", "resume"):
    if key not in parsed or parsed[key] in (None, ""):
        continue
    value = parsed[key]
    if key != "stopped_step" and isinstance(value, str):
        if value.startswith("/"):
            source = Path(value)
            if not source.exists():
                if key == "resume" or (key == "review_attention_counts" and outcome["outcome"] == "failed"):
                    continue
            if not source.is_file():
                print(f"record-outcome: {key} file is missing: {source}", file=sys.stderr)
                sys.exit(2)
            value = source.read_text()
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            print(f"record-outcome: {key} is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(2)
    if key == "stopped_step" and not isinstance(value, str):
        print("record-outcome: stopped_step must be a string", file=sys.stderr)
        sys.exit(2)
    if key != "stopped_step" and not isinstance(value, dict):
        print(f"record-outcome: {key} must be a JSON object", file=sys.stderr)
        sys.exit(2)
    if key == "review_attention_counts" and all(isinstance(value.get(tier), list) for tier in ("red", "orange", "yellow", "white")):
        value = {tier: len(value[tier]) for tier in ("red", "orange", "yellow", "white")}
    outcome[key] = value

if contract == "factory-feature/1" and outcome["outcome"] == "failed" and branch_name:
    outcome["branch"] = branch_name

out_dir = os.path.dirname(outcome_path)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)
with open(outcome_path, "w") as f:
    json.dump(outcome, f)
    f.write("\n")
PY
