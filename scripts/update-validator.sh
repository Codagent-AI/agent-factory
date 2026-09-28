#!/usr/bin/env bash
if [[ ${1:-} == --check-only ]]; then shift; exec "$(dirname "$0")/update-checkout.sh" --check-only "Agent Validator" "$@"; fi
exec "$(dirname "$0")/update-checkout.sh" "Agent Validator" "$@"
