#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')"
fi
phase=$1
branch=$2
if [ "$phase" = implemented ]; then
  python3 - <<'PYTASK'
from pathlib import Path
for path in Path('openspec/changes').glob('*/tasks.md'):
    text = path.read_text()
    path.write_text(text.replace('- [ ]', '- [x]'))
PYTASK
fi
git add -A -- . ':(exclude).agent-runner'
if git diff --cached --quiet; then
  git commit --allow-empty -m "[factory-feature] chore: mark $phase checkpoint" -m "Factory-Checkpoint: $phase"
else
  git commit -m "[factory-feature] chore: complete $phase phase" -m "Factory-Checkpoint: $phase"
fi
git push origin "HEAD:refs/heads/$branch"
