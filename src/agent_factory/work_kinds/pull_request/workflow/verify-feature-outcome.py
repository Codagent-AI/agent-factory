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

# Preserve the original standalone validation for outcomes without the additive field.
if "blocked_step" in value:
    for key in ("blocked_step", "stopped_step"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise SystemExit("invalid " + key)
    if value["outcome"] == "needs-input" and (
        not isinstance(value.get("questions"), list)
        or not value["questions"]
        or not all(isinstance(question, str) and question for question in value["questions"])
        or not isinstance(value.get("direction_summary"), str)
        or not value["direction_summary"]
        or ("branch" in value and not isinstance(value["branch"], str))
        or "pr" in value
    ):
        raise SystemExit("invalid needs-input outcome")
