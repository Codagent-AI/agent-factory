#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("change_name", ""))')"
fi
phase=$1
branch=$2
change_name=${3:-}
if [ "$phase" = implemented ]; then
  python3 - "$change_name" <<'PYTASK'
import re
import sys
from pathlib import Path
name = sys.argv[1]
if not name or Path(name).name != name:
    raise SystemExit('implemented checkpoint requires one change name')
path = Path('openspec/changes') / name / 'tasks.md'
text = path.read_text()
tasks = re.findall(r'^\s*-\s*\[[ xX]\]', text, flags=re.MULTILINE)
if len(tasks) != 1:
    raise SystemExit('implemented checkpoint requires exactly one planned task')
updated = re.sub(r'^(\s*-\s*)\[ \]', r'\1[x]', text, count=1, flags=re.MULTILINE)
path.write_text(updated)
PYTASK
  git add -A -- . ':(exclude).agent-runner' ':(exclude)openspec/changes/*'
  git add -A -- "openspec/changes/$change_name"
else
  git add -A -- . ':(exclude).agent-runner'
fi
if git diff --cached --quiet; then
  git commit --allow-empty -m "[factory-feature] chore: mark $phase checkpoint" -m "Factory-Checkpoint: $phase"
else
  git commit -m "[factory-feature] chore: complete $phase phase" -m "Factory-Checkpoint: $phase"
fi
git push origin "HEAD:refs/heads/$branch"
