#!/bin/sh
# Prints true when this attempt must run verification on its own, because implementation did
# not run in it and nothing records verification as done: a resume at verify (a resume that
# finds the Factory-Verified trailer became a resume at finalize in prepare-branch.sh), or a
# resume at archive without the trailer, or with a merge of the target branch that added
# commits. Otherwise implement-change verified the change itself, or verification is already
# behind the resume point.
set -eu
payload=$(cat)
resume=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("resume_from", ""))')
target=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("target_head", ""))')
artifact_dir=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("artifact_dir", ""))')
case "$resume" in
  verify)
    printf true
    ;;
  archive)
    merged=$(python3 - "$artifact_dir/base-merge.json" <<'PY'
import json, sys
try:
    status = json.load(open(sys.argv[1])).get("status", "")
except (OSError, ValueError):
    status = ""
print("true" if status in ("merged", "conflict") else "false")
PY
)
    if [ "$merged" = false ] && git log --first-parent --format='%(trailers:key=Factory-Verified,valueonly)' "$target..HEAD" 2>/dev/null | grep -qx true; then
      printf false
    else
      printf true
    fi
    ;;
  *)
    printf false
    ;;
esac
