#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["branch_name"])')"
fi
artifact_dir=$1
branch=$2
# The workflow merges only on a resume or continuation, which always names its resume point;
# refuse before touching the clone or the remote when that invariant does not hold.
python3 - "$artifact_dir/merge-conflict.json" <<'PY'
import json, sys
if not json.load(open(sys.argv[1])).get('resume_from'):
    raise SystemExit('merge-conflict.json names no resume point')
PY
git merge --abort
if git ls-remote --exit-code origin "refs/heads/$branch" >/dev/null 2>&1; then
  :
else
  status=$?
  [ "$status" -eq 2 ] || exit "$status"
  git push origin "HEAD:refs/heads/$branch"
fi
python3 - "$artifact_dir" "$branch" <<'PY'
import json, sys
from pathlib import Path
artifacts, branch = Path(sys.argv[1]), sys.argv[2]
conflict = json.loads((artifacts / 'merge-conflict.json').read_text())
stop = json.loads((artifacts / 'merge-stop.json').read_text())
files = ', '.join(conflict['conflicted'])
questions = [f"Merging {conflict['base_head'][:7]} conflicts in: {files}", *stop['questions']]
stopped_step = conflict['resume_from']
(artifacts / 'feature-outcome.json').write_text(json.dumps({'contract': 'factory-feature/1', 'outcome': 'needs-input', 'stopped_step': stopped_step, 'questions': questions, 'reasons': questions, 'direction_summary': stop['direction_summary'], 'branch': branch}) + '\n')
PY
