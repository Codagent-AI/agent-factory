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
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$script_dir/repair-block.py" archive --session-dir "$session_dir" --artifact-dir "$artifact_dir" --branch "$branch"
