#!/usr/bin/env python3
"""Record the actual validator result and the innermost failed verify step."""

import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, cast


def failure(session: Path) -> dict[str, object]:
    fallback: dict[str, object] = {"validator": "failed", "reasons": []}
    try:
        output = session / "output"
        if (output / "verify-change-validator-result.txt").read_text().strip() != "PASS":
            return fallback
        deepest = None
        depth = -1
        for line in (session / "audit.log").read_text().splitlines():
            match = re.search(r"\bstep_end (.*)$", line)
            if not match:
                continue
            event: Any = json.loads(match[1])
            if not isinstance(event, dict):
                return fallback
            event = cast(dict[str, Any], event)
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
            if step != "verify" and not step.startswith("verify/"):
                continue
            prefix_depth = len(prefix.split("/")) if prefix else 0
            if prefix_depth >= depth:
                deepest = (step, event.get("stderr") or "")
                depth = prefix_depth
        if deepest is None:
            return fallback
        step, stderr = deepest
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
        return {"validator": "passed", "reasons": [reason]}
    except (OSError, ValueError):
        return fallback


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: verify-failure.py SESSION_DIR ARTIFACT_DIR")
    artifact = Path(sys.argv[2])
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "verify-failure.json").write_text(json.dumps(failure(Path(sys.argv[1]))) + "\n")
