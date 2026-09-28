#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  artifact_dir=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')
else
  artifact_dir=$1
fi
git merge --abort
python3 - "$artifact_dir" <<'PY'
import json, sys
from pathlib import Path
artifacts = Path(sys.argv[1])
conflict = json.loads((artifacts / 'merge-conflict.json').read_text())
stop = json.loads((artifacts / 'merge-stop.json').read_text())
reasons = [f"Merging {conflict['base_head'][:7]} conflicts in: {', '.join(conflict['conflicted'])}", *stop['questions']]
(artifacts / 'review-outcome.json').write_text(json.dumps({'contract': 'factory-review/1', 'outcome': 'needs-input', 'reasons': reasons, 'answered': [], 'changed': []}) + '\n')
PY
