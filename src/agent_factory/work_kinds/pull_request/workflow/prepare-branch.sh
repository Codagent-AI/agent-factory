#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("target_head", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("resume_from", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prior_branch", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("base_head", ""))')"
fi
branch=$1
target=$2
resume=${3:-}
prior=${4:-}
artifact_dir=$5
base_head=${6:-}
mkdir -p "$artifact_dir"
git cat-file -e "$target^{commit}"
fallback=''
effective_resume=$resume
merge_status=''
prior_head=''
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$script_dir/merge-base.sh"
if [ -n "$prior" ]; then
  if git fetch origin "refs/heads/$prior:refs/remotes/origin/$prior" 2>/dev/null; then
    prior_head=$(git rev-parse "refs/remotes/origin/$prior")
    git checkout -B "$branch" "refs/remotes/origin/$prior" >&2
    merge_base "${base_head:-$target}" "$target" "$branch" "$artifact_dir" "$resume" "$prior"
  elif git fetch origin "refs/heads/$branch:refs/remotes/origin/$branch" 2>/dev/null; then
    git checkout -B "$branch" "refs/remotes/origin/$branch" >&2
    merge_base "${base_head:-$target}" "$target" "$branch" "$artifact_dir" "$resume" "$prior"
  else
    fallback='prior branch unavailable'
  fi
elif [ -n "$resume" ]; then
  if git fetch origin "refs/heads/$branch:refs/remotes/origin/$branch" 2>/dev/null; then
    git checkout -B "$branch" "refs/remotes/origin/$branch" >&2
    if [ -n "$base_head" ]; then
      merge_base "$base_head" "$target" "$branch" "$artifact_dir" "$resume" "$prior"
    fi
  else
    fallback='resume branch unavailable'
  fi
fi
if { [ -z "$resume" ] && [ -z "$prior" ]; } || [ -n "$fallback" ]; then
  git checkout -B "$branch" "$target" >&2
fi
if [ -n "$fallback" ]; then
  effective_resume=''
  python3 - "$artifact_dir/resume.json" "$fallback" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({'fallback': sys.argv[2], 'resume_from': ''}) + '\n')
PY
fi
# A merge that brought in commits invalidates earlier validation, so finalize re-verifies.
if [ "$effective_resume" = finalize ]; then
  case "${merge_status:-}" in
    merged|conflict) effective_resume=verify ;;
  esac
fi
python3 - "$artifact_dir/attempt-start.json" "$effective_resume" "$(git rev-parse HEAD)" "$prior_head" <<'PYTHON'
import json, sys
from pathlib import Path
value = {'resume_from': sys.argv[2], 'head': sys.argv[3]}
if sys.argv[4]:
    value['prior_head'] = sys.argv[4]
Path(sys.argv[1]).write_text(json.dumps(value) + '\n')
PYTHON
printf '%s' "$effective_resume"
