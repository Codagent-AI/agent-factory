#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("target_head", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("resume_from", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prior_branch", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')"
fi
branch=$1
target=$2
resume=${3:-}
prior=${4:-}
artifact_dir=$5
mkdir -p "$artifact_dir"
# The clone starts at the target commit; keep it available even after checkout.
git cat-file -e "$target^{commit}"
fallback=''
if [ -n "$prior" ]; then
  if git fetch origin "refs/heads/$prior:refs/remotes/origin/$prior" 2>/dev/null; then
    git checkout -B "$branch" "refs/remotes/origin/$prior"
    if ! git merge --no-edit "$target"; then
      git merge --abort
      fallback='prior branch could not merge the recorded target commit'
    fi
  else
    fallback='prior branch unavailable'
  fi
elif [ -n "$resume" ]; then
  if git fetch origin "refs/heads/$branch:refs/remotes/origin/$branch" 2>/dev/null; then
    git checkout -B "$branch" "refs/remotes/origin/$branch"
  else
    fallback='resume branch unavailable'
  fi
fi
if [ -z "$resume" ] && [ -z "$prior" ] || [ -n "$fallback" ]; then
  git checkout -B "$branch" "$target"
fi
if [ -n "$fallback" ]; then
  resume_if_available=''
  python3 - "$artifact_dir/resume.json" "$fallback" <<'PY'
import json, sys
with open(sys.argv[1], 'w') as handle:
    json.dump({'fallback': sys.argv[2], 'resume_from': ''}, handle)
    handle.write('\n')
PY
fi
printf '%s' "${resume_if_available:-$resume}"
