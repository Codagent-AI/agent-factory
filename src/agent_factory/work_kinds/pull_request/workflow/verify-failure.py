#!/usr/bin/env python3
"""Record the actual validator result and the last failed verify leaf.

With --require-evidence, exit 3 after writing the record when it names no verify failure and
the validator did not fail: the workflow stopped before verification, which is a technical
failure for the factory to retry, not an outcome.
"""

import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

REPAIR_LIMIT = 1500


def in_verify(step: str) -> bool:
    """Whether a step path is the verification sub-workflow or inside it.

    Verification runs as the top-level `verify` step, or as the `verify-change` step of the
    Runner's implement-change workflow, whatever step of the factory workflow calls that.
    """
    return any(segment in ("verify", "verify-change") for segment in step.split("/"))


def failure(session: Path) -> dict[str, object]:
    fallback: dict[str, object] = {"validator": "failed", "reasons": []}
    try:
        output = session / "output"
        validator_result = output / "verify-change-validator-result.txt"
        validator = "failed"
        if validator_result.exists():
            if validator_result.read_text().strip() != "PASS":
                return fallback
            validator = "passed"
        failed_leaf = None
        repairs: dict[str, str] = {}
        for line in (session / "audit.log").read_text().splitlines():
            repair = re.search(r"\[([^]]+)\] (repair_blocked|step_start) (.*)$", line)
            if repair:
                step = "/".join(repair[1].split(", "))
                repairs.pop(step, None)
                if repair[2] == "step_start":
                    continue
                with suppress(ValueError):
                    payload: Any = json.loads(repair[3])
                    if isinstance(payload, dict):
                        response = cast(dict[str, Any], payload).get("response")
                        if isinstance(response, str):
                            repairs[step] = response
                continue
            match = re.search(r"\bstep_end (.*)$", line)
            if not match:
                continue
            event: Any = json.loads(match[1])
            if not isinstance(event, dict):
                return fallback
            event = cast(dict[str, Any], event)
            if event.get("outcome") == "success":
                prefix_match = re.search(r"\[([^]]+)\] step_end ", line)
                if prefix_match:
                    repairs.pop("/".join(prefix_match[1].split(", ")), None)
            if event.get("outcome") != "failed":
                continue
            identity = event.get("identity")
            if not isinstance(identity, dict):
                return fallback
            identity = cast(dict[str, Any], identity)
            prefix, step_id = identity.get("prefix"), identity.get("step_id")
            if not isinstance(prefix, str) or not isinstance(step_id, str) or not step_id:
                return fallback
            step = "/".join(part for part in (prefix, step_id) if part)
            if not in_verify(step):
                continue
            # Parent failures are logged after their failed child. Keep that
            # child, but replace earlier retry failures with a later failed leaf.
            # Counted loop parents omit the child's ":N" iteration suffix.
            if failed_leaf is None or not (
                failed_leaf[0].startswith(step + "/") or failed_leaf[0].startswith(step + ":")
            ):
                failed_leaf = (step, event.get("stderr") or "")
        if failed_leaf is None:
            return fallback
        step, stderr = failed_leaf
        if not isinstance(stderr, str):
            return fallback
        if not stderr.strip():
            # Runner persists the bracketed audit prefix with '/' mapped to '__'
            # and other punctuation to '_'; literal underscores are escaped.
            name = "".join(
                ch
                if ch.isascii() and (ch.isalnum() or ch in ".-")
                else "%5F"
                if ch == "_"
                else "__"
                if ch == "/"
                else "_"
                for ch in f"[{step}]"
            )
            with suppress(OSError, UnicodeError):
                stderr = (output / (name + ".err")).read_text()
        tail = "\n".join([line for line in stderr.splitlines() if line.strip()][-5:])
        reason = f"verify failed at {step}" + (f": {tail}" if tail else "")
        response = re.sub(r"(?:^|\n)REPAIR_BLOCKED\s*$", "", repairs.get(step, "")).strip()
        if len(response) > REPAIR_LIMIT:
            response = response[:REPAIR_LIMIT].rstrip() + "…"
        if response:
            reason += f"; repair: {response}"
        return {"validator": validator, "reasons": [reason]}
    except (OSError, ValueError):
        return fallback


if __name__ == "__main__":
    arguments = [argument for argument in sys.argv[1:] if argument != "--require-evidence"]
    if len(arguments) != 2:
        sys.exit("usage: verify-failure.py SESSION_DIR ARTIFACT_DIR [--require-evidence]")
    artifact = Path(arguments[1])
    artifact.mkdir(parents=True, exist_ok=True)
    record = failure(Path(arguments[0]))
    (artifact / "verify-failure.json").write_text(json.dumps(record) + "\n")
    if len(arguments) != len(sys.argv) - 1 and not record["reasons"]:
        result = Path(arguments[0]) / "output" / "verify-change-validator-result.txt"
        try:
            red = result.read_text().strip() != "PASS"
        except OSError:
            red = False
        if not red:
            sys.exit(3)
