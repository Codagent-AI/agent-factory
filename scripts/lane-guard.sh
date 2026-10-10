#!/usr/bin/env bash
# Sourced by deploy.sh. The live executable alone changes the database guard.
lanes_downgraded=false
lane_pointers_moved=false

lane_guard_recover() {
  local exit_status=$?
  trap - EXIT
  if [[ $lanes_downgraded == true ]]; then
    if [[ $lane_pointers_moved == true ]]; then
      point_at "$running" "$previous_shared" || warn "could not restore the live release pointers"
    fi
    "$running" --config "$config" lanes enable || warn "could not re-enable lanes; kind mode remains safe"
  fi
  exit "$exit_status"
}

lane_guard() {
  local stage=$1 supported live_status=0 claims guard_status=0 suffix
  suffix="nothing is deployed"
  [[ $stage == restore ]] && suffix="the factory stays paused"
  if "$executable" lanes supported 2>/dev/null | grep -qx priority-lanes; then
    return 0
  fi
  supported=$("$running" lanes supported 2>/dev/null) || live_status=$?
  if ((live_status != 0 && live_status != 2)); then
    die "cannot determine whether the live release supports lanes"
  fi
  if ((live_status == 2)) || ! grep -qx priority-lanes <<<"$supported"; then
    return 0
  fi
  if [[ $stage == before ]]; then
    claims=$("$running" --config "$config" lanes downgrade --check) || guard_status=$?
  else
    claims=$("$running" --config "$config" lanes downgrade) || guard_status=$?
  fi
  if ((guard_status != 0)); then
    die "cannot roll back past Priority lanes; unfinished attempts:
$claims
Roll back by: pause; let attempts settle, or cancel claims, until each kind has at most one unfinished attempt; deploy the older release. A hand rollback bypasses this guard; $suffix"
  fi
  if [[ $stage == restore ]]; then
    lanes_downgraded=true
    trap lane_guard_recover EXIT
  fi
}
