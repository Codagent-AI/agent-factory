#!/usr/bin/env bash
# Sourced by deploy.sh; functions also support isolated deploy integration tests.

validator_checkout() {
  validator_explicit=false
  validator=${AGENT_FACTORY_VALIDATOR_CHECKOUT:-}
  if [[ -n $validator ]]; then
    validator_explicit=true
    if [[ $validator == '~/'* ]]; then validator=$HOME/${validator#'~/'}; fi
    return
  fi
  validator=$(sed -n 's/^agent_validator = "\(.*\)"$/\1/p' "$config" | head -n 1)
  if [[ -n $validator ]]; then
    validator_explicit=true
    if [[ $validator == '~/'* ]]; then validator=$HOME/${validator#'~/'}; fi
    return
  fi
  validator=$(dirname "$runner")/agent-validator
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
  local status_text=$1 update_status=0
  if ! host_slots_free "$status_text"; then
    warn "a host attempt is running; skipping the Agent Validator update and build"
    return
  fi
  "$(dirname "${BASH_SOURCE[0]}")/update-validator.sh" "$validator" || update_status=$?
  case $update_status in
    0) ;;
    3) return ;;
    *) die "could not update Agent Validator in $validator; the factory stays paused" ;;
  esac
  (cd "$validator" && bun install --frozen-lockfile && bun run build:local) \
    || die "Agent Validator build failed in $validator; the factory stays paused"
  say "built Agent Validator at $(git -C "$validator" rev-parse --short HEAD) in $validator"
  validator_path_check
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
