#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')"
fi
artifact_dir=$1
# Preflight: the target needs OpenSpec for definition and Agent Validator configuration for the
# Runner's plan commit and verification. Stop before any branch is created when either is missing.
if [ ! -d openspec ]; then
  reason='target repository is not initialized for OpenSpec'
  question='Initialize OpenSpec in the target repository'
elif [ ! -s .validator/config.yml ]; then
  reason='target repository has no Agent Validator configuration (.validator/config.yml)'
  question='Configure Agent Validator in the target repository (agent-validator init)'
else
  exit 0
fi
mkdir -p "$artifact_dir"
python3 - "$artifact_dir/feature-outcome.json" "$reason" "$question" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'contract': 'factory-feature/1', 'outcome': 'needs-input', 'reasons': [sys.argv[2]], 'stopped_step': 'preflight', 'questions': [sys.argv[3]], 'direction_summary': 'Definition has not started'}) + '\n')
PY
