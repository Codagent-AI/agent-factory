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
python3 - "$artifact_dir/define-stop.json" <<'PY'
import json, sys
from pathlib import Path
stop = json.loads(Path(sys.argv[1]).read_text())
questions = stop.get('questions')
if (
    not isinstance(stop.get('step'), str) or not stop['step'].strip()
    or not isinstance(questions, list) or not questions
    or not all(isinstance(question, str) and question.strip() for question in questions)
    or not isinstance(stop.get('direction_summary'), str)
    or not stop['direction_summary'].strip()
):
    raise SystemExit('invalid definition stop')
PY
git add -A -- . ':(exclude).agent-runner'
if ! git diff --cached --quiet; then
  git commit -m "[define-stop] docs: record drafted feature definition"
fi
git push origin "HEAD:refs/heads/$branch"
python3 - "$artifact_dir/define-stop.json" "$artifact_dir/feature-outcome.json" "$branch" <<'PY'
import json, sys
from pathlib import Path
stop = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps({'contract': 'factory-feature/1', 'outcome': 'needs-input', 'reasons': stop['questions'], 'stopped_step': stop['step'], 'questions': stop['questions'], 'direction_summary': stop['direction_summary'], 'branch': sys.argv[3]}) + '\n')
PY
