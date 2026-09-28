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
for line in (audit.read_text() if audit.exists() else '').splitlines():
    match = re.search(r'\[archive, sub:archive-change(?:, [^]]+)*\] (\w+) (.*)$', line)
    if not match:
        continue
    event, payload = match.groups()
    if event in ('step_end', 'sub_workflow_end'):
        continue
    response = None
    if event == 'repair_blocked':
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if isinstance(data, dict):
            response = data.get('response')

if not isinstance(response, str):
    raise SystemExit('archive step failed without a REPAIR_BLOCKED declaration; see archive entries in ' + str(audit))
explanation = re.sub(r'(?:^|\n)REPAIR_BLOCKED\s*$', '', response).strip()
if not explanation:
    raise SystemExit('archive repair block has no explanation')

Path(sys.argv[2]).write_text(json.dumps({
    'contract': 'factory-feature/1',
    'outcome': 'needs-input',
    'stopped_step': 'archive',
    'reasons': [explanation],
    'questions': [explanation],
    'direction_summary': "Implementation is complete and pushed. Archiving is blocked by the cause above. Fix it on the target branch, or commit the fix to this branch, then comment on the issue: the next attempt merges the target branch and resumes at archive.",
    'branch': sys.argv[3],
}) + '\n')
PY
