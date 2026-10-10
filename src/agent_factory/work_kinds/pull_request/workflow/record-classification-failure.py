#!/usr/bin/env python3
"""Keep rejected classification evidence and report a durable failure reason."""

import json
import sys
from pathlib import Path
from typing import Any


def failure_reasons(artifact: Path, status: str, ci_status: str) -> list[str]:
    source = artifact / "review-attention.json"
    evidence = "no review-attention.json existed"
    try:
        if source.exists():
            source.replace(artifact / "review-attention.rejected.json")
            evidence = "kept as review-attention.rejected.json"
    except OSError as exc:
        evidence = (
            f"review-attention.json could not be moved to review-attention.rejected.json: {exc}"
        )
    if status == "session-failed":
        detail = "the classifying session failed"
        # Keep the standard session-failure reason; add evidence problems when necessary.
        if evidence != "kept as review-attention.rejected.json":
            detail += f" ({evidence})"
    else:
        detail = f"review-attention.json stayed invalid after repair ({evidence})"
    reasons = [
        f"review-attention classification failed after finalization: {detail}; "
        "the pull request was not annotated"
    ]
    if ci_status == "failed":
        reasons.append("CI did not pass within its fix cycle")
    return reasons


def main() -> None:
    payload: dict[str, Any] = json.load(sys.stdin)
    reasons = failure_reasons(
        Path(payload["artifact_dir"]), payload["classification_status"], payload["ci_status"]
    )
    sys.stdout.write(json.dumps(reasons) + "\n")


if __name__ == "__main__":
    main()
