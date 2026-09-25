#!/bin/sh
set -eu
resume=${1:-}
prior=${2:-}
[ -n "$prior" ] && exit 1
# Reconcile (exit 1) only when resuming at a definition step, one that precedes implement.
if "$(dirname "$0")/factory-resume-skip.sh" implement "$resume"; then
  exit 1
fi
exit 0
