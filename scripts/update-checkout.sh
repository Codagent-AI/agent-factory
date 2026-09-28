#!/usr/bin/env bash
# Fast-forward a safe checkout to origin/main, or inspect it with --check-only.
set -euo pipefail
check_only=false
if [[ ${1:-} == --check-only ]]; then check_only=true; shift; fi
label=${1:-}
checkout=${2:-}
warn() { printf 'deploy: warning: %s\n' "$*" >&2; }
skip() { warn "$*; skipping the $label update and build: $checkout"; exit 3; }
[[ -n $checkout ]] && git -C "$checkout" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { printf 'deploy: %s checkout not found: %s\n' "$label" "${checkout:-unset}" >&2; exit 1; }
[[ $(git -C "$checkout" branch --show-current) == main ]] || skip "the $label checkout is not on main"
[[ -z $(git -C "$checkout" status --porcelain) ]] || skip "the $label checkout has uncommitted changes"
git -C "$checkout" fetch -q origin
git -C "$checkout" merge-base --is-ancestor HEAD origin/main \
  || skip "the $label checkout has commits not on origin/main"
if [[ $check_only == false ]]; then git -C "$checkout" merge -q --ff-only origin/main; fi
