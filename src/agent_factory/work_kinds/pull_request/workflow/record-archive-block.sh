#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  artifact_dir=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')
  branch=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["branch_name"])')
  session_dir=$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_dir"])')
else
  artifact_dir=$1
  branch=$2
  session_dir=$3
fi
python3 - "$session_dir/audit.log" "$artifact_dir/feature-outcome.json" "$branch" <<'PY'
import json
import re
import sys
from pathlib import Path

audit = Path(sys.argv[1])
response = None
for line in audit.read_text().splitlines():
    match = re.search(r'\[archive, sub:archive-change(?:, [^]]+)*\] repair_blocked (\{.*\})$', line)
    if match:
        response = json.loads(match.group(1)).get('response')

if not isinstance(response, str):
    raise SystemExit('archive repair was not declared blocked')
explanation = re.sub(r'(?:^|\n)REPAIR_BLOCKED\s*$', '', response).strip()
if not explanation:
    raise SystemExit('archive repair block has no explanation')

Path(sys.argv[2]).write_text(json.dumps({
    'contract': 'factory-feature/1',
    'outcome': 'needs-input',
    'stopped_step': 'archive',
    'reasons': [explanation],
    'questions': [explanation],
    'direction_summary': 'Implementation is complete and pushed; archiving the OpenSpec change is blocked until the cause above is fixed (for example a main spec under openspec/specs outside the change directory). The next attempt resumes at archive.',
    'branch': sys.argv[3],
}) + '\n')
PY
