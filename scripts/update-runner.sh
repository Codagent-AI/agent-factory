#!/usr/bin/env bash
# Bring the operator's Agent Runner checkout up to origin/main before make build.
# Exit status follows update-checkout.sh: 0 ready to build, 3 left alone, else error.
if [[ ${1:-} == --check-only ]]; then shift; exec "$(dirname "$0")/update-checkout.sh" --check-only "Agent Runner" "$@"; fi
exec "$(dirname "$0")/update-checkout.sh" "Agent Runner" "$@"
