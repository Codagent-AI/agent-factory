"""The small, dependency-free process that owns work inside a Fly Machine."""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from agent_factory.fly.transport import mapping_field, string_field

# The job's user owns the credential directories and the env file but not their
# root-owned parents, so it cannot unlink them. It deletes the directories' contents
# and truncates the env file; the launcher removes the empty shells as root.
_CREDENTIAL_CLEANUP = (
    "find /host-home/codex /host-home/claude -mindepth 1 -delete 2>/dev/null; "
    "[ -e /run/factory/env ] && : > /run/factory/env"
)


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
KILL_GRACE_SECONDS="${FACTORY_KILL_GRACE_SECONDS:-30}"
mkdir -p "$ARTIFACTS/.factory/job" "$(dirname "$DEADLINE_FILE")"

deadline() {
  local value="${FACTORY_DEADLINE_EPOCH:-0}" stored=0
  if [ -r "$DEADLINE_FILE" ]; then stored="$(cat "$DEADLINE_FILE" 2>/dev/null || printf 0)"; fi
  case "$stored" in (*[!0-9]*|'') stored=0;; esac
  case "$value" in (*[!0-9]*|'') value=0;; esac
  [ "$stored" -gt "$value" ] && value="$stored"
  printf '%s\n' "$value"
}

# Deadline enforcement cannot wait for the suite job to return.  Jobs run in
# their own session; this supervisor remains alive to preserve DONE evidence.
watchdog() {
  while :; do
    current="$(deadline)" now="$(date +%s)"
    if [ "$current" -gt 0 ] && [ "$now" -ge "$current" ]; then
      touch "$ARTIFACTS/.factory/deadline-expired"
      if [ -r "$ARTIFACTS/.factory/active-job-pgid" ]; then
        pgid="$(cat "$ARTIFACTS/.factory/active-job-pgid")"
        case "$pgid" in (*[!0-9]*|'') ;; *)
          kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pgid" 2>/dev/null || true
          sleep "$KILL_GRACE_SECONDS"
          kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pgid" 2>/dev/null || true
        ;; esac
      fi
      exit 1
    fi
    sleep "$WATCHDOG_SECONDS"
  done
}
watchdog &
watchdog_pid=$!
trap 'kill "$watchdog_pid" 2>/dev/null || true' EXIT

while :; do
  [ -e "$ARTIFACTS/.factory/deadline-expired" ] && exit 1
  job=1
  while [ -e "$ARTIFACTS/.factory/job/$job/DONE" ]; do job=$((job + 1)); done
  directory="$ARTIFACTS/.factory/job/$job"
  if [ -e "$directory/start" ] && [ -x "$directory/job.sh" ]; then
    if command -v setsid >/dev/null 2>&1; then
      setsid "$directory/job.sh" >"$directory/job.log" 2>&1 &
    else
      "$directory/job.sh" >"$directory/job.log" 2>&1 &
    fi
    job_pid=$!
    printf '%s\n' "$job_pid" >"$ARTIFACTS/.factory/active-job-pgid"
    wait "$job_pid"
    status=$?
    rm -f "$ARTIFACTS/.factory/active-job-pgid"
    printf '%s\n' "$status" >"$directory/exit-code"
    # One real tab-separated line per file: path, size, mtime. GNU find does it in
    # a single process; the loop is the portable fallback. Neither stat flavour
    # expands "\t" in a format, so tabs come from printf.
    (cd "$ARTIFACTS" && {
      find . -path './.factory/staging' -prune -o -type f -printf '%P\t%s\t%T@\n' 2>/dev/null ||
      find . -path './.factory/staging' -prune -o -type f -print | while IFS= read -r file; do
        size="$(stat -c %s "$file" 2>/dev/null || stat -f %z "$file")"
        mtime="$(stat -c %Y "$file" 2>/dev/null || stat -f %m "$file")"
        printf '%s\t%s\t%s\n' "${file#./}" "$size" "$mtime"
      done
    }) | sort >"$directory/files.txt"
    touch "$directory/DONE"
    [ -e "$ARTIFACTS/.factory/deadline-expired" ] && exit 1
    continue
  fi
  sleep "$WATCHDOG_SECONDS"
done
"""


def job_script(manifest: Mapping[str, object], suite_script: str) -> str:
    """Build the guest job wrapper from non-secret manifest data and suite text."""
    repositories = mapping_field(manifest, "repositories")
    commits = mapping_field(manifest, "commits")
    runner_url = _anonymous_url(string_field(repositories, "runner"))
    skills_url = _anonymous_url(string_field(repositories, "skills"))
    runner_commit = string_field(commits, "runner")
    skills_commit = string_field(commits, "skills")
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            # Armed first: a failed clone or build must not leave credentials behind.
            f"trap '{_CREDENTIAL_CLEANUP}; "
            "rm -rf /workspace/home/.codex /workspace/home/.claude' EXIT",
            # A recovery job runs in the same Machine, where the clones already exist.
            "[ -d /agent-runner-source/.git ] || git clone "
            + shlex.quote(runner_url)
            + " /agent-runner-source",
            "git -C /agent-runner-source checkout --detach " + shlex.quote(runner_commit),
            "[ -d /agent-skills-source/.git ] || git clone "
            + shlex.quote(skills_url)
            + " /agent-skills-source",
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
            suite_script,
            "",
        )
    )


def stand_in_script(script: str) -> str:
    """Wrap an operator script so delivered credentials never outlive the job."""
    return "\n".join(
        (
            "#!/usr/bin/env bash",
            f"trap '{_CREDENTIAL_CLEANUP}' EXIT",
            "set -a; [ -r /run/factory/env ] && . /run/factory/env; set +a",
            script,
            "",
        )
    )


def _anonymous_url(url: str) -> str:
    """A Machine holds no SSH key, so an scp-style GitHub remote is read over HTTPS."""
    prefix = "git@github.com:"
    if url.startswith(prefix):
        return "https://github.com/" + url[len(prefix) :]
    return url
