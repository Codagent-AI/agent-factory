#!/bin/sh
set -eu
resume=${1:-}
prior=${2:-}
# Reconcile (exit 1) a continuation that resumes at or before implement, or any resume at a
# definition step, one that precedes implement. A continuation resuming at verification has
# an archived plan that a revision could no longer reach.
if [ -n "$prior" ] && { [ -z "$resume" ] || [ "$resume" = implement ]; }; then
  exit 1
fi
if "$(dirname "$0")/factory-resume-skip.sh" implement "$resume"; then
  exit 1
fi
exit 0
