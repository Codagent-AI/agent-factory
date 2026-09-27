#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  artifact_dir=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')
  branch=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["branch_name"])')
else
  artifact_dir=$1
  branch=$2
fi
git add -A -- . ':(exclude).agent-runner'
if ! git diff --cached --quiet; then
  git commit -m "[factory-feature] docs: preserve invalid OpenSpec draft"
fi
git push origin "HEAD:refs/heads/$branch"
python3 - "$artifact_dir" "$branch" <<'PY'
import json, sys
from pathlib import Path
artifact_dir, branch = Path(sys.argv[1]), sys.argv[2]
errors = (artifact_dir / 'validation.log').read_text(errors='replace')
(artifact_dir / 'feature-outcome.json').write_text(json.dumps({'contract': 'factory-feature/1', 'outcome': 'failed', 'reasons': [f'OpenSpec validation failed after repair: {errors}'], 'branch': branch}) + '\n')
PY
