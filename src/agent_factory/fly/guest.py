"""The small, dependency-free process that owns work inside a Fly Machine."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import cast


def guest_init_script() -> str:
    """Return the bash init program.

    ``FACTORY_ROOT`` is deliberately a test seam; it is empty in a Machine, where
    the absolute paths below are the contract with the launcher.
    """
    return r"""#!/usr/bin/env bash
set -u
ROOT="${FACTORY_ROOT:-}"
ARTIFACTS="$ROOT/artifacts"
DEADLINE_FILE="$ROOT/var/lib/factory/deadline"
WATCHDOG_SECONDS="${FACTORY_WATCHDOG_SECONDS:-30}"
mkdir -p "$ARTIFACTS/.factory/job" "$(dirname "$DEADLINE_FILE")"

deadline() {
  local value="${FACTORY_DEADLINE_EPOCH:-0}" stored=0
  if [ -r "$DEADLINE_FILE" ]; then stored="$(cat "$DEADLINE_FILE" 2>/dev/null || printf 0)"; fi
  case "$stored" in (*[!0-9]*|'') stored=0;; esac
  case "$value" in (*[!0-9]*|'') value=0;; esac
  [ "$stored" -gt "$value" ] && value="$stored"
  printf '%s\n' "$value"
}

# Deadline enforcement cannot wait for the suite job to return.  The process
# group kill also stops descendants the suite might have started.
watchdog() {
  while :; do
    current="$(deadline)" now="$(date +%s)"
    if [ "$current" -gt 0 ] && [ "$now" -ge "$current" ]; then
      kill -- -$$ 2>/dev/null || kill -TERM $$
      exit 1
    fi
    sleep "$WATCHDOG_SECONDS"
  done
}
watchdog &
watchdog_pid=$!
trap 'kill "$watchdog_pid" 2>/dev/null || true' EXIT

while :; do
  job=1
  while [ -e "$ARTIFACTS/.factory/job/$job/DONE" ]; do job=$((job + 1)); done
  directory="$ARTIFACTS/.factory/job/$job"
  if [ -e "$directory/start" ] && [ -x "$directory/job.sh" ]; then
    "$directory/job.sh" >"$directory/job.log" 2>&1
    status=$?
    printf '%s\n' "$status" >"$directory/exit-code"
    (cd "$ARTIFACTS" && find . -path './.factory/staging' -prune -o -type f -exec sh -c '
      for file; do
        if stat -c "%n\t%s\t%Y" "$file" >/dev/null 2>&1; then
          stat -c "%n\t%s\t%Y" "$file"
        else
          stat -f "%N\t%z\t%m" "$file"
        fi
      done
    ' sh {} +) | sed 's|^./||' | sort >"$directory/files.txt"
    touch "$directory/DONE"
    continue
  fi
  sleep "$WATCHDOG_SECONDS"
done
"""


def job_script(manifest: Mapping[str, object], suite_script: str) -> str:
    """Build the guest job wrapper from non-secret manifest data and suite text."""
    repositories = _object(manifest, "repositories")
    commits = _object(manifest, "commits")
    runner_url = _required(repositories, "runner")
    skills_url = _required(repositories, "skills")
    runner_commit = _required(commits, "runner")
    skills_commit = _required(commits, "skills")
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "git clone " + shlex.quote(runner_url) + " /agent-runner-source",
            "git -C /agent-runner-source checkout --detach " + shlex.quote(runner_commit),
            "git clone " + shlex.quote(skills_url) + " /agent-skills-source",
            "git -C /agent-skills-source checkout --detach " + shlex.quote(skills_commit),
            "/agent-runner-source/scripts/sandbox-sync-home.sh",
            "export CI=1 HOME=/workspace/home AGENT_RUNNER_SOURCE_COMMIT="
            + shlex.quote(runner_commit)
            + (
                " AGENT_RUNNER_SOURCE_DIRTY=false AGENT_RUNNER_DEV_AUDIT=1"
                " AGENT_RUNNER_AUDIT_SMOKE=0"
            ),
            "mkdir -p /workspace/bin /tmp/agent-runner-local",
            "tar --exclude ./.git --exclude ./bin --exclude ./artifacts "
            "--exclude ./worktrees -C /agent-runner-source -cf - . | "
            "tar -C /tmp/agent-runner-local -xf -",
            "cd /tmp/agent-runner-local",
            "dev_audit_root_encoded=$(printf '%s' /agent-runner-source | base64 | tr -d '\\n')",
            'dev_audit_ldflags="-X main.version=local-dev '
            "-X github.com/codagent/agent-runner/internal/devaudit."
            "BuildRootEncoded=${dev_audit_root_encoded} "
            "-X github.com/codagent/agent-runner/internal/devaudit."
            "BuildRevision=${AGENT_RUNNER_SOURCE_COMMIT} "
            "-X github.com/codagent/agent-runner/internal/devaudit."
            'BuildDirty=${AGENT_RUNNER_SOURCE_DIRTY}"',
            'go build -tags dev_audit -ldflags "$dev_audit_ldflags" '
            "-o /workspace/bin/agent-runner ./cmd/agent-runner",
            "cd /workspace",
            "set -a; . /run/factory/env; set +a",
            "trap 'rm -rf /host-home /workspace/home/.codex "
            "/workspace/home/.claude /run/factory/env' EXIT",
            suite_script,
            "",
        )
    )


def _object(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"manifest has no {key}")
    return cast(Mapping[str, object], item)


def _required(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"manifest has no {key}")
    return item
