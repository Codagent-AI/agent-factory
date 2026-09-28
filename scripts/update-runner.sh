#!/usr/bin/env bash
# Bring the operator's Agent Runner checkout up to origin/main before make build.
if [[ ${1:-} == --check-only ]]; then shift; exec "$(dirname "$0")/update-checkout.sh" --check-only "Agent Runner" "$@"; fi
exec "$(dirname "$0")/update-checkout.sh" "Agent Runner" "$@"
