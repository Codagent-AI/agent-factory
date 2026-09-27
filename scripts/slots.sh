# Slot predicates over `agent-factory status` output, sourced by scripts/deploy.sh.
# A missing status (for example, status failed) counts as busy.

# Every slot is free, so no running job can still use an old release.
slots_free() {
  grep -q '^eval slot: free$' <<<"$1" && grep -q '^fix slot: free$' <<<"$1" \
    && ! grep -E '^[a-z-]+ slot: ' <<<"$1" | grep -qv ': free$'
}
