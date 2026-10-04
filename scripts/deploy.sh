#!/usr/bin/env bash
# Deploy the latest Agent Factory, Agent Runner, and Agent Validator to the live service.
#
# Usage: scripts/deploy.sh [--no-runner] [--no-validator] [ref]
#   ref          factory ref to deploy (default: origin/main)
#   --no-runner  leave the Agent Runner checkout and the installed runner alone
#   --no-validator  leave the Agent Validator checkout and build alone
#
# Agent Factory: each deploy is an immutable release, a detached worktree of the
# service clone at ~/.agent-factory/releases/<commit> with its own venv. The
# LaunchAgent runs the newest release; processes a running job started keep the
# release they started from, so deploy (or restart the resident) at any time,
# including while fixes, features, and evals run. Only the resident restarts,
# and supervisors and Fly launchers survive that. Releases beyond the newest
# AGENT_FACTORY_KEEP_RELEASES (default 2) are removed, but only while every
# slot is free and no process references them.
# A rollback to a release without fixture revision support is refused while
# unfinished fixture-pinned eval claims exist, both before and after pausing.
#
# Agent Runner: unless --no-runner, the operator's checkout is fast-forwarded
# to origin/main (scripts/update-runner.sh), and make build updates the host
# runner that fix and feature runs use. Nothing is pushed. go build replaces the
# binary by renaming a new file over it, so a running fix or feature keeps the
# binary it started with; later launches use the new one. The step is skipped
# with a warning when the checkout is not on main, has uncommitted changes, or
# has commits not on origin/main.
#
# Evals need nothing here: each admission fetches Agent Evals harness_ref and
# pins its own Agent Runner ref.
#
# Environment overrides: AGENT_FACTORY_SERVICE_CLONE (default
# ~/.agent-factory/agent-factory), AGENT_FACTORY_RELEASES (default
# ~/.agent-factory/releases), AGENT_FACTORY_KEEP_RELEASES (default 2),
# AGENT_FACTORY_CONFIG (default ~/.agent-factory/config.toml),
# AGENT_FACTORY_PLIST (default
# ~/Library/LaunchAgents/com.codagent.agent-factory.plist),
# AGENT_FACTORY_RUNNER_CHECKOUT (default: [repositories] agent_runner in the
# local configuration), AGENT_FACTORY_VALIDATOR_CHECKOUT (default:
# [repositories] agent_validator, or a sibling of agent_runner).
set -euo pipefail

build_runner=true
build_validator=true
ref=origin/main
while (($#)); do
  case $1 in
    --no-runner) build_runner=false ;;
    --no-validator) build_validator=false ;;
    -*) printf 'deploy: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *) ref=$1 ;;
  esac
  shift
done

base=${AGENT_FACTORY_SERVICE_CLONE:-$HOME/.agent-factory/agent-factory}
releases=${AGENT_FACTORY_RELEASES:-$HOME/.agent-factory/releases}
keep=${AGENT_FACTORY_KEEP_RELEASES:-2}
config=${AGENT_FACTORY_CONFIG:-$HOME/.agent-factory/config.toml}
plist=${AGENT_FACTORY_PLIST:-$HOME/Library/LaunchAgents/com.codagent.agent-factory.plist}
label=com.codagent.agent-factory
domain=gui/$(id -u)
repo_url=https://github.com/Codagent-AI/agent-factory.git

say() { printf 'deploy: %s\n' "$*"; }
warn() { printf 'deploy: warning: %s\n' "$*" >&2; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

[[ $keep =~ ^[1-9][0-9]*$ ]] || die "AGENT_FACTORY_KEEP_RELEASES must be a positive integer"
[[ -f $plist ]] || die "LaunchAgent plist not found: $plist"
[[ -f $config ]] || die "local configuration not found: $config"

# Read status with the executable the service runs now, before touching anything.
running=$(plutil -extract ProgramArguments.0 raw -o - "$plist")
read_status() { "$running" --config "$config" status; }
status_text=$(read_status) || die "could not read factory status"
was_paused=false
grep -q '^paused: true$' <<<"$status_text" && was_paused=true
# shellcheck source=scripts/slots.sh
source "$(dirname "$0")/slots.sh"
source "$(dirname "$0")/validator.sh"
# shellcheck source=scripts/fixture-guard.sh
source "$(dirname "$0")/fixture-guard.sh"

# Everything that can fail without changing the deployment happens before the pause.
if [[ ! -d $base/.git ]]; then
  say "cloning $repo_url into $base"
  git clone -q "$repo_url" "$base"
fi
git -C "$base" fetch -q origin
sha=$(git -C "$base" rev-parse -q --verify "$ref^{commit}") || die "unknown ref: $ref"
short=${sha:0:12}
release=$releases/$short
executable=$release/.venv/bin/agent-factory

runner=${AGENT_FACTORY_RUNNER_CHECKOUT:-}
if [[ -z $runner ]]; then
  runner=$(sed -n 's/^agent_runner = "\(.*\)"$/\1/p' "$config" | head -n 1)
fi
if [[ $runner == '~/'* ]]; then runner=$HOME/${runner#'~/'}; fi
if [[ $build_validator == true ]]; then
  validator_checkout
  validator_preflight
fi
if [[ $build_runner == true ]]; then
  # Only the source moves here; the installed runner changes at make build, after the pause.
  runner_status=0
  "$(dirname "$0")/update-runner.sh" "$runner" || runner_status=$?
  case $runner_status in
    0) ;;
    3) build_runner=false ;;
    *) die "could not update the Agent Runner checkout; nothing is deployed" ;;
  esac
fi

# Build the release. A release is complete only once its marker exists; it is never changed after.
mkdir -p "$releases"
if [[ -f $release/.release-complete ]]; then
  [[ -z $(git -C "$release" status --porcelain --untracked-files=no) ]] || die "release $release has local changes"
  say "release $short already built"
else
  if [[ -e $release ]]; then
    git -C "$base" worktree remove --force "$release" 2>/dev/null || rm -rf "$release"
    git -C "$base" worktree prune
  fi
  git -C "$base" worktree add -q --detach "$release" "$sha"
  (cd "$release" && uv sync -q --frozen) || die "uv sync failed in $release; nothing is deployed"
  [[ -x $executable ]] || die "uv sync did not produce $executable; nothing is deployed"
  touch "$release/.release-complete"
  say "built release $short ($ref) at $release"
fi

fixture_guard "$executable" "$running" before
"$running" --config "$config" pause >/dev/null
say "paused the factory"

if [[ $build_runner == true ]]; then
  make -s -C "$runner" build >/dev/null || die "make build failed in $runner; the factory stays paused"
  say "built Agent Runner at $(git -C "$runner" rev-parse --short HEAD) in $runner"
fi
if [[ $build_validator == true ]]; then
  if validator_status=$("$executable" --config "$config" status); then
    validator_build "$validator_status"
  else
    warn "could not read factory status with the new release; skipping the Agent Validator update and build"
  fi
fi

fixture_guard "$executable" "$running" after
# Point the LaunchAgent and the local configuration at the release. The plist gets the
# resolved path, never the current symlink, so each process keeps the release it started from.
grep -q '^shared_config = ' "$config" || die "no shared_config line in $config; the factory stays paused"
previous_shared=$(sed -n 's/^shared_config = "\(.*\)"$/\1/p' "$config" | head -n 1)
point_at() {
  local exe=$1 shared=$2 path new_path entry
  local -a entries=()
  # PlistBuddy Set replaces in place; plutil -replace on an array index inserts instead.
  /usr/libexec/PlistBuddy -c "Set :ProgramArguments:0 $exe" "$plist"
  path=$(plutil -extract EnvironmentVariables.PATH raw -o - "$plist" 2>/dev/null || true)
  if [[ -n $path ]]; then
    IFS=: read -r -a entries <<<"$path"
  fi
  new_path=$(dirname "$exe")
  for entry in ${entries[@]+"${entries[@]}"}; do
    [[ $entry == */.venv/bin ]] || new_path+=":$entry"
  done
  /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:PATH $new_path" "$plist"
  plutil -lint -s "$plist"
  sed -i '' "s#^shared_config = .*#shared_config = \"$shared\"#" "$config"
}
point_at "$executable" "$release/config/codagent.toml"
say "LaunchAgent and shared_config point at release $short"

if ! doctor_output=$(PATH=$release/.venv/bin:$PATH "$executable" --config "$config" doctor 2>&1); then
  grep -v ': OK' <<<"$doctor_output" >&2 || true
  point_at "$running" "$previous_shared"
  die "doctor failed; the LaunchAgent and shared_config point at the previous release again, and the factory stays paused"
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
ln -sfn "$release" "$releases/current"
say "LaunchAgent reloaded on release $short"

if [[ $was_paused == true ]]; then
  say "left paused, as it was before the deploy"
else
  "$executable" --config "$config" resume >/dev/null
  say "resumed the factory"
fi
PATH=$release/.venv/bin:$PATH "$executable" --config "$config" tick

# Remove releases beyond the newest $keep, never the live one and never one in use.
prune_status=$("$executable" --config "$config" status) || prune_status=
if ! slots_free "$prune_status"; then
  say "kept old releases: a slot is busy, so a running job may still use one"
else
  in_use=$(ps -axww -o command=)
  position=0
  # Markers are listed newest first. The current symlink is skipped: it would
  # take a keep position, and Git resolves it to the release it points at.
  while IFS= read -r dir; do
    [[ -n $dir ]] || continue
    position=$((position + 1))
    ((position > keep)) || continue
    [[ $dir == "$release" ]] && continue
    if grep -qF "$dir/" <<<"$in_use"; then
      say "kept release $(basename "$dir"): a process still uses it"
      continue
    fi
    git -C "$base" worktree remove --force "$dir" 2>/dev/null || rm -rf "$dir"
    say "removed release $(basename "$dir")"
  done < <(for marker in "$releases"/*/.release-complete; do
    dir=$(dirname "$marker")
    [[ -e $marker && ! -L $dir ]] || continue
    printf '%s %s\n' "$(stat -f %m "$marker")" "$dir"
  done | sort -rn | cut -d' ' -f2-)
  git -C "$base" worktree prune
fi
say "deployed $short"
