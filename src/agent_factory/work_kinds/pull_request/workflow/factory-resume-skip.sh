#!/bin/sh
set -eu
resume=${1:-}
step=${2:-}
[ -n "$resume" ] || exit 1
seen_step=false
for item in proposal proposal-review specs design test-plan approach-review write-tasks implement archive verify finalize; do
  if [ "$item" = "$step" ]; then seen_step=true; fi
  if [ "$item" = "$resume" ]; then
    [ "$seen_step" = true ] && [ "$step" != "$resume" ] && exit 0
    exit 1
  fi
done
exit 1
