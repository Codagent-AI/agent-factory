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

while [ $# -gt 0 ]; do
  case "$1" in
    --since) since="$2"; shift 2 ;;
    --grace-minutes) grace="$2"; shift 2 ;;
    --interval) interval="$2"; shift 2 ;;
    --no-claims) claims=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -r "$db" ] || { echo "state database not readable: $db" >&2; exit 2; }
case "$grace$interval" in *[!0-9]*|'') echo "grace and interval must be whole numbers" >&2; exit 2 ;; esac
[ "$interval" -ge 1 ] || { echo "interval must be at least 1 second" >&2; exit 2; }
[ -n "$since" ] || since=$(sqlite3 "$db" "select strftime('%Y-%m-%dT%H:%M:%S','now')")
case "$since" in *\'*) echo "invalid --since" >&2; exit 2 ;; esac

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
