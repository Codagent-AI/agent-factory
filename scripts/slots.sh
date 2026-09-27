# Slot predicates over `agent-factory status` output, sourced by scripts/deploy.sh.
# A missing status (for example, status failed) counts as busy.

# Fix and feature runs execute the host agent-runner that `make build` replaces.
# The feature slot line is absent when features are not configured.
host_runner_busy() {
  ! grep -q '^fix slot: free$' <<<"$1" || grep -E '^feature slot: ' <<<"$1" | grep -qv ': free$'
}

# Every slot is free, so no running job can still use an old release.
slots_free() {
  grep -q '^eval slot: free$' <<<"$1" && grep -q '^fix slot: free$' <<<"$1" \
    && ! grep -E '^[a-z-]+ slot: ' <<<"$1" | grep -qv ': free$'
}
