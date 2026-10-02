#!/bin/sh
set -eu
payload=$(cat)
PAYLOAD="$payload" python3 - <<'PY'
import json
import os
import re
import subprocess
from pathlib import Path

data = json.loads(os.environ["PAYLOAD"])
artifact_dir = Path(data["artifact_dir"])
issue = json.loads(Path(data["issue_file"]).read_text())
branch = subprocess.check_output(["git", "branch", "--show-current"], text=True).strip()
listing = json.loads(subprocess.check_output(
    ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--limit", "1"], text=True))
if not listing:
    print("failed", end="")
    raise SystemExit(1)
number = listing[0]["number"]
# gh api expands {owner} and {repo} from the current repository (gh api --help).
url = f"repos/{{owner}}/{{repo}}/pulls/{number}"
pr = json.loads(subprocess.check_output(["gh", "api", url], text=True))
body = pr.get("body") or ""
title = pr.get("title") or ""
issue_number = issue["number"]
keyword = rf"(?:refs|close[sd]?|fix(?:e[sd])?|resolve[sd]?)[ \t]*:?[ \t]+#{issue_number}\b"
body = re.sub(rf"(?im)^[ \t]*{keyword}[ \t]*(?:\n|$)", "", body)
body = re.sub(rf"(?i)\b{keyword}", f"#{issue_number}", body)
marker = f"<!-- agent-factory:claim:{issue.get('claim_id', '')} -->"
if marker not in body:
    body = f"{marker}\n\n" + body
body = f"Closes #{issue_number}\n" + body
evidence_marker = "<!-- agent-factory:task-evidence -->"
evidence_end = "<!-- agent-factory:task-evidence-end -->"


def evidence(name):
    path = artifact_dir / name
    return path.read_text().strip() if path.exists() else "[]"


section = (f"{evidence_marker}\n## Task evidence\n"
           f"Non-chore CI commits: `{evidence('nonchore-commits.json')}`\n{evidence_end}")
if evidence_marker in body:
    before, old_section = body.split(evidence_marker, 1)
    if evidence_end in old_section:
        after = old_section.split(evidence_end, 1)[1]
    else:
        # The end marker was edited away; consume only the section's known lines.
        known = re.match(r"\n## Task evidence\n(?:Choices: .*?\nGate exercises: .*?\n)?Non-chore CI commits: .*?\n", old_section, re.DOTALL)
        if known is None:
            print("failed", end="")
            raise SystemExit(1)
        after = old_section[known.end():]
    body = before + section + after
else:
    body += "\n\n" + section
new_title = title
if not title.startswith("chore:"):
    new_title = "chore: " + re.sub(r"^[a-z][a-z0-9-]*(?:\([^)]*\))?!?:\s*", "", title)
fields = []
if body != pr.get("body"):
    fields += ["-f", f"body={body}"]
if new_title != title:
    fields += ["-f", f"title={new_title}"]
if fields:
    result = subprocess.run(["gh", "api", "-X", "PATCH", url, *fields],
                            capture_output=True, text=True)
    if result.returncode and new_title != title:
        # The Task design records this error while leaving the PR outcome unchanged.
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "retitle-failed").write_text(result.stderr)
        if body != pr.get("body"):
            result = subprocess.run(["gh", "api", "-X", "PATCH", url, "-f", f"body={body}"],
                                    capture_output=True, text=True)
        else:
            print("passed", end="")
            raise SystemExit(0)
    if result.returncode:
        print("failed", end="")
        raise SystemExit(1)
print("passed", end="")
PY
