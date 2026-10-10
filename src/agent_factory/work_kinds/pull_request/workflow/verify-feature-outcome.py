#!/usr/bin/env python3
import json
import sys
from pathlib import Path

value = json.loads(Path(sys.argv[1]).read_text())
if value.get("contract") != "factory-feature/1" or value.get("outcome") not in (
    "pull-request",
    "needs-input",
    "failed",
):
    raise SystemExit("invalid feature outcome")

for key in ("blocked_step", "stopped_step"):
    if key in value and (not isinstance(value[key], str) or not value[key]):
        raise SystemExit("invalid " + key)
if value["outcome"] == "needs-input":
    if "blocked_step" not in value and (
        not isinstance(value.get("stopped_step"), str)
        or (value.get("stopped_step") != "preflight" and not isinstance(value.get("branch"), str))
    ):
        raise SystemExit("invalid needs-input outcome")
    if (
        not isinstance(value.get("questions"), list)
        or not value["questions"]
        or not all(isinstance(question, str) and question for question in value["questions"])
        or not isinstance(value.get("direction_summary"), str)
        or not value["direction_summary"]
        or ("blocked_step" in value and "branch" in value and not isinstance(value["branch"], str))
        or "pr" in value
    ):
        raise SystemExit("invalid needs-input outcome")
