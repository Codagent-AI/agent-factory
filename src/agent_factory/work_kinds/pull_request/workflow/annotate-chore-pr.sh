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
marker = f"<!-- agent-factory:claim:{issue.get('claim_id', '')} -->"
if marker not in body:
    body = f"{marker}\n\n" + body
if f"Refs #{issue['number']}" not in body:
    body = f"Refs #{issue['number']}\n" + body
evidence_marker = "<!-- agent-factory:task-evidence -->"
evidence_end = "<!-- agent-factory:task-evidence-end -->"
choices_path = artifact_dir / "task-choices.json"
gates_path = artifact_dir / "gate-exercises.json"
choices = choices_path.read_text() if choices_path.exists() else "[]"
gates = gates_path.read_text() if gates_path.exists() else "[]"
nonchore_path = artifact_dir / "nonchore-commits.json"
nonchore = nonchore_path.read_text() if nonchore_path.exists() else "[]"
section = (f"{evidence_marker}\n## Task evidence\n"
           f"Choices: `{choices.strip()}`\nGate exercises: `{gates.strip()}`\n"
           f"Non-chore CI commits: `{nonchore.strip()}`\n{evidence_end}")
if evidence_marker in body:
    before, old_section = body.split(evidence_marker, 1)
    if evidence_end in old_section:
        after = old_section.split(evidence_end, 1)[1]
    else:
        # Legacy annotations had no end marker; consume only their known lines.
        legacy = re.match(r"\n## Task evidence\nChoices: .*?\nGate exercises: .*?\nNon-chore CI commits: .*?\n", old_section, re.DOTALL)
        if legacy is None:
            print("failed", end="")
            raise SystemExit(1)
        after = old_section[legacy.end():]
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
