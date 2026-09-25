#!/bin/sh
set -eu
resume=${1:-}
prior=${2:-}
[ -n "$prior" ] && exit 1
case "$resume" in
  proposal|proposal-review|specs|design|test-plan|approach-review|write-tasks) exit 1 ;;
  *) exit 0 ;;
esac
