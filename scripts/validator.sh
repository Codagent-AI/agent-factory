#!/usr/bin/env bash
# Sourced by deploy.sh; functions also support isolated deploy integration tests.

validator_checkout() {
  validator=${AGENT_FACTORY_VALIDATOR_CHECKOUT:-$(sed -n 's/^agent_validator = "\(.*\)"$/\1/p' "$config" | head -n 1)}
  if [[ -z $validator ]]; then
    validator_explicit=false
    validator=$(dirname "$runner")/agent-validator
    return
  fi
  validator_explicit=true
  if [[ $validator == '~/'* ]]; then validator=$HOME/${validator#'~/'}; fi
}

validator_preflight() {
  if [[ $validator_explicit == false && ! -e $validator ]]; then
    warn "default Agent Validator checkout not found: $validator; skipping the Validator step"
    build_validator=false
    return
  fi
  git -C "$validator" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "Agent Validator checkout not found: $validator; nothing is deployed"
  local update_status=0
  "$(dirname "${BASH_SOURCE[0]}")/update-validator.sh" --check-only "$validator" \
    || update_status=$?
  case $update_status in
    0) ;;
    3) build_validator=false ;;
    *) die "could not check the Agent Validator checkout; nothing is deployed" ;;
  esac
  if [[ $build_validator == true ]]; then
    command -v bun >/dev/null 2>&1 \
      || die "bun is required to build Agent Validator; use --no-validator to skip it"
  fi
}

validator_build() {
  local status_text=$1 update_status=0 old backup output
  if ! host_slots_free "$status_text"; then
    warn "a host attempt is running; skipping the Agent Validator update and build"
    return
  fi
  old=$(git -C "$validator" rev-parse HEAD) \
    || die "could not read the Agent Validator revision; the factory stays paused"
  backup=$(mktemp -d "${TMPDIR:-/tmp}/agent-validator-build.XXXXXX") \
    || die "could not prepare an Agent Validator build backup; the factory stays paused"
  # The built executable is the only artifact a failed build must restore. Bun
  # can recreate dependencies and generated files on the next attempt.
  if [[ -e $validator/dist || -L $validator/dist ]]; then
    cp -a "$validator/dist" "$backup/dist" \
      || { rm -rf "$backup" || true; die "could not back up Agent Validator dist; the factory stays paused"; }
  fi
  "$(dirname "${BASH_SOURCE[0]}")/update-validator.sh" "$validator" || update_status=$?
  case $update_status in
    0) ;;
    3) rm -rf "$backup" || true; return ;;
    *) rm -rf "$backup" || true; die "could not update Agent Validator in $validator; the factory stays paused" ;;
  esac
  if ! (cd "$validator" && bun install --frozen-lockfile && bun run build:local); then
    # Every step here is guarded, so a failure still reaches a message that
    # tells the operator to rebuild before resuming.
    # A failed restore keeps the backup: it may be the only intact copy of the last build.
    validator_restore_dist "$backup" \
      || die "Agent Validator build failed and backup restore failed in $validator; the previous build is kept in $backup/dist; the host validator may be broken and must be restored from there or rebuilt before resuming"
    git -C "$validator" reset -q --hard "$old" \
      || { rm -rf "$backup" || true; die "Agent Validator build failed and checkout rollback failed in $validator; the host validator may be broken and must be rebuilt before resuming"; }
    rm -rf "$backup" || true
    die "Agent Validator build failed in $validator; the checkout was rolled back, but the host validator may be broken and must be rebuilt before resuming"
  fi
  rm -rf "$backup" || true
  say "built Agent Validator at $(git -C "$validator" rev-parse --short HEAD) in $validator"
  validator_path_check
}

# Replace whatever the failed build left in dist with the backup, if there is one.
validator_restore_dist() {
  local backup=$1
  rm -rf "$validator/dist" || return
  if [[ -e $backup/dist || -L $backup/dist ]]; then
    mv "$backup/dist" "$validator/dist"
  fi
}

validator_path_check() {
  local plist_path found expected actual
  plist_path=$(plutil -extract EnvironmentVariables.PATH raw -o - "$plist" 2>/dev/null || true)
  found=$(PATH="$plist_path" command -v agent-validator || true)
  expected=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$validator/dist/index.js")
  actual=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${found:-unavailable}")
  [[ $actual == "$expected" ]] \
    || warn "LaunchAgent PATH resolves agent-validator to $actual; expected $expected"
}
