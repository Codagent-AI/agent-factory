#!/bin/sh
# Appends the factory's implementation constraints to a change's design.md, once.
# A Task change also gets the Task boundary and the chore commit rule.
set -eu
payload=$(cat)
design_file=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("design_file", ""))')
kind=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("kind", ""))')
if [ -z "$design_file" ] || [ ! -f "$design_file" ]; then
  echo "add-constraints: design file is missing: $design_file" >&2
  exit 1
fi
marker='<!-- agent-factory:constraints -->'
if grep -q -F "$marker" "$design_file"; then
  exit 0
fi
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
{
  printf '\n%s\n' "$marker"
  cat "$here/factory-change-rules.md"
  if [ "$kind" = task ]; then
    printf '%s\n' '- Every commit subject uses the conventional type `chore:` after any workflow step marker.'
    printf '\n## Task boundary\n\n'
    sed '1d' "$here/factory-task-boundary.md"
  fi
} >> "$design_file"
