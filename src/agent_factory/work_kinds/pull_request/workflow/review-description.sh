#!/bin/sh
set -eu

# Keeps a feature pull request's factory-owned description across a review round, and
# brings it up to date with the round. "save" records the description before the round.
# "restore" rebuilds it from that saved description: it adds a section naming the round's
# commits and, once validation passed and the round pushed, the feedback ids they addressed
# (the posted replies hold the detail). It refreshes the commits after acceptance and marks
# items those commits may have fixed (mark-later-commits.py). Anything finalization or an
# agent wrote over it is replaced, and kept in pr-description-overwritten.md. A fix pull
# request's description is left as it is.

payload=$(cat)
MARKER="$(dirname "$0")/mark-later-commits.py" PAYLOAD="$payload" python3 - <<'PY'
import html
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["MARKER"]).parent))
from pr_description import fit

payload = json.loads(os.environ["PAYLOAD"])
review = json.loads(Path(payload["review_file"]).read_text())
if review.get("kind") != "feature":
    raise SystemExit(0)
number = str(review["pull_request"]["number"])
artifacts = Path(payload["artifact_dir"])
saved = artifacts / "pr-description-before-review.md"
updated = artifacts / "pr-description-after-review.md"


def body() -> str:
    # Parsed from JSON: "-q .body" would add a newline to the restored description.
    return json.loads(
        subprocess.run(
            ["gh", "pr", "view", number, "--json", "body"],
            capture_output=True, text=True, check=True,
        ).stdout
    )["body"]


def one_line(text: object) -> str:
    # Agent-written text must not open or close sections or forge the factory's markers.
    return html.escape(" ".join(str(text).split()), quote=False)


def round_section(eol: str) -> list[str]:
    """The round's commits and the feedback it changed code for; empty without commits."""
    start = review.get("head_sha") or review["pull_request"].get("head_sha")
    if not start:
        return []
    try:
        log = subprocess.run(
            ["git", "log", "--first-parent", "--reverse", "--format=%H%x00%s", f"{start}..HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return []
    commits = [line.split("\0", 1) for line in log.splitlines() if "\0" in line]
    if not commits:
        return []
    try:
        decision = json.loads(payload.get("decision") or "{}")
        items = [item for item in decision.get("items", []) if item.get("decision") == "change"]
    except (json.JSONDecodeError, AttributeError, TypeError):
        items = []
    lines = [
        f"### 🔁 Review round after `{start[:7]}`",
        "",
        "Acceptance was not re-run; the evidence below still describes the accepted commit.",
        "",
        *(f"- `{sha[:7]}` {one_line(subject)}" for sha, subject in commits),
        "",
    ]
    try:
        result = json.loads((artifacts / "implement-result.json").read_text())
        validator = result.get("validator") if isinstance(result, dict) else None
        pushed = isinstance(validator, dict) and validator.get("status") == "passed"
    except (OSError, ValueError):
        pushed = False
    if not pushed:
        lines += [
            "The commits above were not pushed; see the replies on this pull request.",
            "",
        ]
    elif items:
        lines += [
            "Feedback addressed (details in the replies on this pull request):",
            *(f"- {one_line(item.get('source', ''))} {one_line(item.get('id', ''))}" for item in items),
            "",
        ]
    return lines


def rebuilt(original: str) -> str:
    eol = "\r\n" if "\r\n" in original else "\n"
    section = round_section(eol)
    if section:
        lines = original.split(eol)
        at = next((i for i, line in enumerate(lines) if line == "## Change summary"), None)
        if at is None:
            lines += ["", *section]
        else:
            lines[at:at] = section
        original = eol.join(lines)
    updated.write_text(original, newline="")
    # Best effort: without the marks the round's section is still published.
    subprocess.run(["python3", os.environ["MARKER"], str(updated)], check=False)
    with updated.open(newline="") as source:
        fitted = fit(source.read())
    updated.write_text(fitted, newline="")
    return fitted


# newline="" keeps a description's CRLF line endings as they are on both sides.
if payload["mode"] == "save":
    saved.write_text(body(), newline="")
elif saved.is_file() and (original := saved.open(newline="").read()).strip():
    try:
        wanted = rebuilt(original)
        current = body()
        if current != wanted:
            if current != original:
                # Keep what is replaced, so an edit made during the round can be recovered.
                (artifacts / "pr-description-overwritten.md").write_text(current, newline="")
            # REST, not `gh pr edit`, which also reads project items (see annotate-pr.py).
            subprocess.run(
                [
                    "gh", "api", "--method", "PATCH", f"repos/{{owner}}/{{repo}}/pulls/{number}",
                    "-F", f"body=@{updated}", "--silent",
                ],
                capture_output=True, text=True, check=True,
            )
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
        detail = getattr(error, "stderr", None) or error
        message = f"could not restore the description of pull request #{number}: {detail}"
        (artifacts / "description-restore-failed").write_text(message + "\n")
        raise SystemExit(message) from error
PY
