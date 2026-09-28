#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["review_file"])')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin)["artifact_dir"])')"
fi
review_file=$1
artifact_dir=$2
base_head=$(python3 - "$review_file" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get('base_head', ''))
PY
)
if [ -z "$base_head" ]; then
  printf none
  exit 0
fi
branch=$(git branch --show-current)
mkdir -p "$artifact_dir"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/merge-base.sh"
merge_base "$base_head" "$(python3 - "$review_file" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get('target_at_admission', ''))
PY
)" "$branch" "$artifact_dir" '' ''
printf '%s' "$merge_status"
