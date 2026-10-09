#!/bin/sh
set -eu

# See record-triage.sh for why this requires python3 rather than a jq
# fallback: the outcome shape here is conditionally assembled, not a flat
# single-field extraction.

payload=$(cat)

PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import subprocess
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
scope_path = parsed.get("scope_path")
scope = {}
if scope_path and Path(scope_path).exists():
    try:
        scope = json.loads(Path(scope_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"record-outcome: cannot read scope: {exc}", file=sys.stderr)
        sys.exit(2)
verify_failure = {}
verify_failure_path = parsed.get("verify_failure")
if verify_failure_path and Path(verify_failure_path).exists():
    try:
        verify_failure = json.loads(Path(verify_failure_path).read_text())
        if (not isinstance(verify_failure, dict)
            or verify_failure.get("validator") not in ("passed", "failed")
            or not isinstance(verify_failure.get("reasons"), list)
            or not all(isinstance(reason, str) for reason in verify_failure["reasons"])):
            raise ValueError("invalid verify-failure record")
    except (OSError, ValueError) as exc:
        print(f"record-outcome: cannot read verify failure: {exc}", file=sys.stderr)
        sys.exit(2)
checks_status = validator_status
if contract == "factory-feature/1" and verify_failure.get("validator") == "passed":
    checks_status = "passed"
post_path = parsed.get("post_scope_path")
post_scope = {}
post_error = None
if post_path and Path(post_path).exists():
    try:
        post_scope = json.loads(Path(post_path).read_text())
        if not isinstance(post_scope, dict):
            raise ValueError("post-finalize scope is not a JSON object")
    except (OSError, ValueError) as exc:
        post_error = str(exc)
# Finalization's CI repair moved HEAD, so the post-finalize guard had to run.
post_required = False
prefinalize_head = parsed.get("prefinalize_head")
if isinstance(prefinalize_head, str) and prefinalize_head:
    current_head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    post_required = current_head.returncode == 0 and current_head.stdout.strip() != prefinalize_head


def pr_reference():
    return {
        "url": pr_url,
        "number": pr_details.get("number"),
        "branch": branch_name,
        "head_sha": pr_details.get("headRefOid") or pr_details.get("head_sha") or "",
    }


def failed_with_pr(reasons, validator="passed"):
    """A Task outcome whose pull request stays open for a human after a guard failure."""
    return {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons,
        "pr": pr_reference(),
        "validator": {"status": validator},
        "ci": {"status": ci_status or "failed"},
    }


if validator_status == "passed" and scope_path and not scope.get("complete"):
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": scope.get("reasons") or ["pre-push Task scope guard did not complete"],
        "validator": {"status": "passed"},
    }
elif post_error is not None:
    outcome = failed_with_pr(
        [f"post-finalize Task scope evidence is unreadable: {post_error}"], validator_status
    )
elif post_required and not post_scope.get("complete"):
    outcome = failed_with_pr(post_scope.get("reasons") or ["post-finalize Task scope guard did not complete"])
elif scope.get("crossed"):
    outcome = {
        "contract": contract,
        "outcome": "needs-input",
        "reasons": scope.get("reasons") or ["Task scope crosses its boundary; belongs in a Feature"],
        "validator": {"status": "passed"},
    }
elif post_scope.get("crossed"):
    outcome = failed_with_pr(post_scope.get("reasons") or ["Task scope crossed after CI repair"])
elif validator_status != "passed":
    outcome = {
        "contract": contract,
        "outcome": "failed",
        "reasons": reasons or verify_failure.get("reasons") or (
            ["verify did not complete"] if checks_status == "passed"
            else ["validator did not pass within its repair cycles"]
        ),
        "validator": {"status": "failed"},
    }
    if pr_url:
        outcome["pr"] = pr_reference()
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

if contract == "factory-feature/1":
    outcome["validator"]["checks"] = "passed" if checks_status == "passed" else "failed"
    compliance = None
    compliance_path = parsed.get("task_compliance")
    if isinstance(compliance_path, str) and compliance_path:
        try:
            loaded = json.loads(Path(compliance_path).read_text())
            if (isinstance(loaded, dict)
                and loaded.get("result") in ("passed", "failed", "not-run", "not-declared")
                and ((loaded.get("result") == "not-run" and isinstance(loaded.get("reason"), str))
                     or all(isinstance(loaded.get(key), str) and loaded[key]
                            for key in ("base", "reviewed_head", "tasks_sha256")))):
                compliance = {
                    key: loaded[key] for key in ("result", "reason", "base", "reviewed_head", "tasks_sha256")
                    if key in loaded
                }
        except (OSError, json.JSONDecodeError):
            pass
    if checks_status == "passed" and compliance is None:
        compliance = {"result": "not-run", "reason":
                      "verify failed before task-compliance" if validator_status != "passed"
                      else "no task-compliance record"}
    if compliance is not None:
        outcome["task_compliance"] = compliance
    if checks_status == "passed":
        outcome["validator"]["status"] = {
            "passed": "passed", "not-declared": "passed", "not-run": "incomplete",
            "failed": "review-failed",
        }[compliance["result"]]

if contract == "factory-feature/1" and outcome["outcome"] == "failed" and branch_name:
    outcome["branch"] = branch_name

out_dir = os.path.dirname(outcome_path)
if out_dir:
    os.makedirs(out_dir, exist_ok=True)
with open(outcome_path, "w") as f:
    json.dump(outcome, f)
    f.write("\n")
PY
