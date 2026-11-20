#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("phase", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("branch_name", ""))')"
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("change_name", ""))')"
  # "false" when the change was verified before this checkpoint, "true" when it was not yet.
  set -- "$@" "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("verify_pending", ""))')"
fi
phase=$1
branch=$2
change_name=${3:-}
verify_pending=${4:-}
if [ "$phase" = implemented ]; then
  if [ -z "$change_name" ] || [ "$(basename -- "$change_name")" != "$change_name" ]; then
    echo 'implemented checkpoint requires one change name' >&2
    exit 1
  fi
  git add -A -- . ':(exclude).agent-runner' ':(exclude)openspec/changes/*'
  git add -A -- "openspec/changes/$change_name"
else
  git add -A -- . ':(exclude).agent-runner'
fi
# Factory-Verified marks a checkpoint after the change's validator and acceptance ran, so a
# resume does not repeat them. A checkpoint without it predates verification.
trailers="Factory-Checkpoint: $phase"
if [ "$verify_pending" = false ]; then
  trailers="$trailers
Factory-Verified: true"
fi
if git diff --cached --quiet; then
  git commit --allow-empty -m "[factory-feature] chore: mark $phase checkpoint" -m "$trailers"
else
  git commit -m "[factory-feature] chore: complete $phase phase" -m "$trailers"
fi
git push origin "HEAD:refs/heads/$branch"
