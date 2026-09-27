#!/bin/sh
set -eu

# Keeps a feature pull request's factory-owned description across a review round.
# "save" records the description before the round; "restore" puts it back when
# finalization rewrote it. A fix pull request's description is left as it is.

payload=$(cat)
PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import subprocess
from pathlib import Path

payload = json.loads(os.environ["PAYLOAD"])
review = json.loads(Path(payload["review_file"]).read_text())
if review.get("kind") != "feature":
    raise SystemExit(0)
number = str(review["pull_request"]["number"])
artifacts = Path(payload["artifact_dir"])
saved = artifacts / "pr-description-before-review.md"


def body() -> str:
    # Parsed from JSON: "-q .body" would add a newline to the restored description.
    return json.loads(
        subprocess.run(
            ["gh", "pr", "view", number, "--json", "body"],
            capture_output=True, text=True, check=True,
        ).stdout
    )["body"]


# newline="" keeps a description's CRLF line endings as they are on both sides.
if payload["mode"] == "save":
    saved.write_text(body(), newline="")
elif saved.is_file() and (original := saved.open(newline="").read()).strip():
    try:
        current = body()
        if current != original:
            # Keep what is replaced, so an edit made during the round can be recovered.
            (artifacts / "pr-description-overwritten.md").write_text(current, newline="")
            subprocess.run(["gh", "pr", "edit", number, "--body-file", str(saved)], check=True)
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
        message = f"could not restore the description of pull request #{number}: {error}"
        (artifacts / "description-restore-failed").write_text(message + "\n")
        raise SystemExit(message) from error
PY
