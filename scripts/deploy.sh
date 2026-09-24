#!/usr/bin/env bash
# Deploy Agent Factory to the service clone that the LaunchAgent runs.
#
# Usage: scripts/deploy.sh [ref]   (default: origin/main)
#
# Refuses while an eval or fix run holds a slot, because the clone's editable
# venv makes a code switch live for every newly spawned process. It then pauses
# the factory, detaches the clone at the ref, syncs its venv, points the
# LaunchAgent and the local configuration at the clone, runs doctor, reloads
# the LaunchAgent, restores the previous pause state, and runs one tick.
#
# Environment overrides: AGENT_FACTORY_SERVICE_CLONE (default
# ~/.agent-factory/agent-factory), AGENT_FACTORY_CONFIG (default
# ~/.agent-factory/config.toml), AGENT_FACTORY_PLIST (default
# ~/Library/LaunchAgents/com.codagent.agent-factory.plist).
set -euo pipefail

ref=${1:-origin/main}
clone=${AGENT_FACTORY_SERVICE_CLONE:-$HOME/.agent-factory/agent-factory}
config=${AGENT_FACTORY_CONFIG:-$HOME/.agent-factory/config.toml}
plist=${AGENT_FACTORY_PLIST:-$HOME/Library/LaunchAgents/com.codagent.agent-factory.plist}
label=com.codagent.agent-factory
domain=gui/$(id -u)
repo_url=https://github.com/Codagent-AI/agent-factory.git

say() { printf 'deploy: %s\n' "$*"; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

[[ -f $plist ]] || die "LaunchAgent plist not found: $plist"
[[ -f $config ]] || die "local configuration not found: $config"

# Read status with the executable the service runs now, before touching any code.
running=$(plutil -extract ProgramArguments.0 raw -o - "$plist")
slots_free() {
  local status
  status=$("$running" --config "$config" status)
  grep -q '^eval slot: free$' <<<"$status" || { echo "an eval is running"; return 1; }
  grep -q '^fix slot: free$' <<<"$status" || { echo "a fix is running"; return 1; }
}
busy=$(slots_free) || die "$busy; deploy when the eval and fix slots are free"
was_paused=false
grep -q '^paused: true$' <<<"$("$running" --config "$config" status)" && was_paused=true

# Everything that can fail without changing the deployment happens before the pause.
if [[ ! -d $clone/.git ]]; then
  say "cloning $repo_url into $clone"
  git clone -q "$repo_url" "$clone"
fi
[[ -z $(git -C "$clone" status --porcelain) ]] || die "the service clone has local changes: $clone"
git -C "$clone" fetch -q origin
git -C "$clone" rev-parse -q --verify "$ref^{commit}" >/dev/null || die "unknown ref: $ref"

"$running" --config "$config" pause >/dev/null
# A run admitted between the first check and the pause would pick up the new code.
if ! busy=$(slots_free); then
  [[ $was_paused == true ]] || "$running" --config "$config" resume >/dev/null
  die "$busy (admitted while pausing); nothing changed; deploy when the slots are free"
fi
say "paused the factory"

git -C "$clone" switch -q --detach "$ref"
sha=$(git -C "$clone" rev-parse --short HEAD)
say "service clone detached at $ref ($sha)"
(cd "$clone" && uv sync -q --frozen)
executable=$clone/.venv/bin/agent-factory
[[ -x $executable ]] || die "uv sync did not produce $executable; the factory stays paused"

# Point the LaunchAgent and the local configuration at the clone (idempotent).
# PlistBuddy Set replaces in place; plutil -replace on an array index inserts instead.
/usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $executable" "$plist"
path=$(plutil -extract EnvironmentVariables.PATH raw -o - "$plist" 2>/dev/null || true)
entries=()
if [[ -n $path ]]; then
  IFS=: read -r -a entries <<<"$path"
fi
new_path=$clone/.venv/bin
for entry in ${entries[@]+"${entries[@]}"}; do
  [[ $entry == */.venv/bin ]] || new_path+=":$entry"
done
/usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:PATH $new_path" "$plist"
plutil -lint -s "$plist"
shared=$clone/config/codagent.toml
grep -q '^shared_config = ' "$config" || die "no shared_config line in $config; the factory stays paused"
sed -i '' "s#^shared_config = .*#shared_config = \"$shared\"#" "$config"
say "LaunchAgent and shared_config point at $clone"

if ! doctor_output=$(PATH=$clone/.venv/bin:$PATH "$executable" --config "$config" doctor 2>&1); then
  grep -v ': OK' <<<"$doctor_output" >&2 || true
  die "doctor failed; the factory stays paused"
fi
say "doctor passed"

launchctl bootout "$domain/$label" 2>/dev/null || true
for _ in $(seq 60); do
  launchctl print "$domain/$label" >/dev/null 2>&1 || break
  sleep 1
done
launchctl print "$domain/$label" >/dev/null 2>&1 && die "the LaunchAgent did not unload; the factory stays paused"
launchctl bootstrap "$domain" "$plist"
launchctl print "$domain/$label" | grep -q "program = $executable" \
  || die "the reloaded LaunchAgent does not run $executable; the factory stays paused"
# A resident that exits at once (bad arguments, import error) shows as "spawn scheduled".
sleep 15
launchctl print "$domain/$label" | grep -q 'state = running' \
  || die "the resident is not running; see ~/.agent-factory/logs/controller.log; the factory stays paused"
say "LaunchAgent reloaded"

if [[ $was_paused == true ]]; then
  say "left paused, as it was before the deploy"
else
  "$executable" --config "$config" resume >/dev/null
  say "resumed the factory"
fi
PATH=$clone/.venv/bin:$PATH "$executable" --config "$config" tick
say "deployed $sha"
