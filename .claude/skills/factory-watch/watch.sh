#!/usr/bin/env bash
# Block until the live factory does something worth a look, print it, exit 0.
#
# Events (one line each, prefixed):
#   FAILURE    a run that is still failed/interrupted/cancelled/timed_out after
#              the grace period (a host fix run passes through "interrupted"
#              until the next tick consumes its fix-outcome.json)
#   CLAIM      a newly admitted claim
#   EVAL-DONE  an eval run that finished
#
# Each event falls in exactly one check window, so restarting with the printed
# "next: --since ..." value never repeats or skips an event. A FAILURE's window
# is shifted back by the grace period.
#
# Usage: watch.sh [--since ISO8601 UTC] [--grace-minutes N] [--interval SECONDS] [--no-claims]
set -euo pipefail

root="${AGENT_FACTORY_ROOT:-$HOME/.agent-factory}"
db="$root/state.sqlite3"
since=""
grace=7        # the resident ticks every poll_minutes (5); allow one tick plus slack
interval=90
claims=1

# An option's value must be present and must not be the next option.
value() {
  case "${2-}" in
    ''|--*) echo "$1 needs a value" >&2; exit 2 ;;
  esac
}

# A whole number within [min, max].
bounded() {
  case "$2" in
    ''|*[!0-9]*) echo "$1 must be a whole number" >&2; exit 2 ;;
  esac
  # Too many digits would overflow the integer comparison below.
  if [ "${#2}" -gt 5 ] || [ "$2" -lt "$3" ] || [ "$2" -gt "$4" ]; then
    echo "$1 must be between $3 and $4" >&2; exit 2
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --since) value "$1" "${2-}"; since="$2"; shift 2 ;;
    --grace-minutes) value "$1" "${2-}"; grace="$2"; shift 2 ;;
    --interval) value "$1" "${2-}"; interval="$2"; shift 2 ;;
    --no-claims) claims=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -r "$db" ] || { echo "state database not readable: $db" >&2; exit 2; }
bounded --grace-minutes "$grace" 0 1440
bounded --interval "$interval" 1 3600
if [ -z "$since" ]; then
  since=$(sqlite3 "$db" "select strftime('%Y-%m-%dT%H:%M:%S','now')")
else
  # Normalize to the UTC form the queries compare against; reject what SQLite cannot parse.
  case "$since" in *\'*) echo "invalid --since: $since" >&2; exit 2 ;; esac
  normalized=$(sqlite3 "$db" "select strftime('%Y-%m-%dT%H:%M:%S', '$since')")
  [ -n "$normalized" ] || { echo "invalid --since: $since" >&2; exit 2; }
  since="$normalized"
fi

echo "watching $db for events after $since (grace ${grace}m, every ${interval}s)"
while true; do
  now=$(sqlite3 "$db" "select strftime('%Y-%m-%dT%H:%M:%S','now')")
  events=$(sqlite3 -separator ' ' "$db" "
    select 'FAILURE', c.repository||'#'||c.issue_number, r.kind, r.id, r.status,
           r.finished_at, substr(r.result_json, 1, 200)
      from run r join claim c on c.id = r.claim_id
     where r.status in ('failed', 'interrupted', 'cancelled', 'timed_out')
       and r.finished_at > strftime('%Y-%m-%dT%H:%M:%S', '$since', '-$grace minutes')
       and r.finished_at <= strftime('%Y-%m-%dT%H:%M:%S', '$now', '-$grace minutes');
    select 'EVAL-DONE', c.repository||'#'||c.issue_number, r.id, r.status, r.finished_at
      from run r join claim c on c.id = r.claim_id
     where r.kind = 'eval' and r.finished_at > '$since' and r.finished_at <= '$now'
       and r.status not in ('failed', 'interrupted', 'cancelled', 'timed_out');
    select 'CLAIM', repository||'#'||issue_number, kind, id, lifecycle, created_at
      from claim
     where $claims = 1 and created_at > '$since' and created_at <= '$now';")
  if [ -n "$events" ]; then
    printf '%s\n' "$events"
    echo "next: --since $now"
    exit 0
  fi
  sleep "$interval"
done
