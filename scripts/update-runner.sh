#!/usr/bin/env bash
# Bring the operator's Agent Runner checkout up to origin/main before make build.
#
# Usage: scripts/update-runner.sh <checkout>
#
# Fix runs use the agent-runner that make build installs from this checkout, and
# evals pin Agent Runner main themselves, so only main matters here. The checkout
# is fast-forwarded to origin/main and never pushed.
#
# Exit status: 0 when the checkout is at origin/main and ready to build; 3 when it
# is left alone (another branch, uncommitted changes, or commits not on
# origin/main), with a warning on stderr; anything else is an error.
set -euo pipefail

runner=${1:-}
warn() { printf 'deploy: warning: %s\n' "$*" >&2; }
skip() { warn "$*; skipping the Agent Runner update and build: $runner"; exit 3; }

[[ -n $runner ]] && git -C "$runner" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { printf 'deploy: Agent Runner checkout not found: %s\n' "${runner:-unset}" >&2; exit 1; }
[[ $(git -C "$runner" branch --show-current) == main ]] || skip "the Agent Runner checkout is not on main"
[[ -z $(git -C "$runner" status --porcelain) ]] || skip "the Agent Runner checkout has uncommitted changes"
git -C "$runner" fetch -q origin
# Anything not on origin/main is unreviewed; never build or push it.
git -C "$runner" merge-base --is-ancestor HEAD origin/main \
  || skip "the Agent Runner checkout has commits not on origin/main"
git -C "$runner" merge -q --ff-only origin/main
