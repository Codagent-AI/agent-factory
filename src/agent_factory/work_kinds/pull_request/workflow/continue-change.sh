#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  payload=$(cat)
  set -- "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("prior_branch", ""))')" \
    "$(printf %s "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("change_name", ""))')"
fi
prior=${1:-}
change=${2:-}
[ -n "$prior" ] && [ -n "$change" ] || exit 0
prior_change=$(printf %s "${prior#factory/}" | tr / -)
[ "$prior_change" != "$change" ] || exit 0
if [ -d "openspec/changes/$prior_change" ] && [ ! -e "openspec/changes/$change" ]; then
  git mv "openspec/changes/$prior_change" "openspec/changes/$change"
fi
for archived in openspec/changes/archive/*-"$prior_change"; do
  [ -d "$archived" ] || continue
  renamed="${archived%"$prior_change"}$change"
  [ -e "$renamed" ] || git mv "$archived" "$renamed"
done
if ! git diff --cached --quiet; then
  git commit -q -m "[factory-feature] chore: continue $prior_change as $change"
fi
