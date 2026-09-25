#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')"
fi
artifact_dir=$1
if [ -d openspec ]; then exit 0; fi
mkdir -p "$artifact_dir"
python3 - "$artifact_dir/feature-outcome.json" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'contract': 'factory-feature/1', 'outcome': 'needs-input', 'reasons': ['target repository is not initialized for OpenSpec'], 'stopped_step': 'preflight', 'questions': ['Initialize OpenSpec in the target repository'], 'direction_summary': 'Definition has not started'}) + '\n')
PY
