#!/usr/bin/env bash
# Sourced by deploy.sh; requires config, warn, and die from the caller.

fixture_guard() {
  local target=$1 live=$2 stage=$3 claims suffix live_revisions live_status
  if "$target" honored-revisions 2>/dev/null | grep -qx fixture; then
    return 0
  fi
  suffix="nothing is deployed"
  [[ $stage == after ]] && suffix="the factory stays paused"
  live_status=0
  live_revisions=$("$live" honored-revisions 2>/dev/null) || live_status=$?
  if ((live_status != 0 && live_status != 2)); then
    die "cannot determine whether the live release honors fixture revisions; $suffix"
  fi
  if ((live_status == 2)) || ! grep -qx fixture <<<"$live_revisions"; then
    warn "live release predates fixture revisions; none can be pinned"
    return 0
  fi
  if ! claims=$("$live" --config "$config" pinned-claims --revision fixture); then
    die "cannot list fixture-pinned claims with the live release; $suffix"
  fi
  [[ -z $claims ]] && return 0
  die "$target cannot honor frozen fixture revisions; unfinished claims:
$claims
Roll back by: pause; let each claim settle, or cancel it; deploy the older release. The older release cannot accept fixture_ref. Stay on a fixture-capable release if a pinned evaluation is still needed; a new request without the key evaluates only the default fixture; $suffix"
}
