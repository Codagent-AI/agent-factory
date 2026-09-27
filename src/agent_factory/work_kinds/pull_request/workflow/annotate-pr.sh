#!/bin/sh
set -eu
script="$(dirname "$0")/annotate-pr.py"
if [ "$#" -eq 0 ]; then
  exec python3 "$script" --json "$(cat)"
fi
exec python3 "$script" "$@"
