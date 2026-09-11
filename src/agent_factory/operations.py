"""Read-only operator diagnostics, saved-state reporting, and launchd packaging."""

from __future__ import annotations

import html
import shutil
import stat
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent_factory.config import ConfigurationError, LocalConfig, SharedConfig
from agent_factory.github import (
    AppCredentials,
    GitHubApiError,
    GitHubClient,
    InstallationTokenProvider,
    SubprocessGhRunner,
)
from agent_factory.store import Claim, ClaimStore, Event
from agent_factory.suites.and_scene import AndSceneAdapter, ReadinessError

if TYPE_CHECKING:
    from agent_factory.store import Run


@dataclass(frozen=True)
class Diagnostic:
    name: str
    available: bool
    detail: str
    action: str


def doctor(config: LocalConfig) -> list[Diagnostic]:
    """Inspect prerequisites only; this function never starts work or repairs state."""
    diagnostics: list[Diagnostic] = []
    shared: SharedConfig | None = None
    try:
        shared = SharedConfig.from_file(config.shared_config)
        _project_mappings(shared)
        diagnostics.append(
            Diagnostic(
                "shared configuration",
                True,
                f"loaded pinned harness {shared.eval.harness_sha}",
                "No action required.",
            )
        )
    except (ConfigurationError, OSError) as error:
        diagnostics.append(
            Diagnostic(
                "shared configuration",
                False,
                str(error),
                "Install a valid, explicitly versioned shared TOML; do not use a branch "
                "or HEAD pin.",
            )
        )
    diagnostics.append(_private_file("GitHub App key", config.credentials.github_app_key))
    diagnostics.extend(_repository_checks(config))
    diagnostics.append(_suite_environment(config.credentials.suite_environment))
    diagnostics.append(
        _command_check(
            "Docker",
            ("docker", "info"),
            "Start Docker Desktop, then rerun doctor.",
            timeout=30,
        )
    )
    profiles = (
        {
            role: str(shared.eval.defaults.get(role, ""))
            for role in ("lead", "implementor", "tester")
        }
        if shared is not None
        else {}
    )
    diagnostics.extend(model_authentication(profiles))
    diagnostics.append(_free_space(config))
    if shared is not None and config.credentials.github_app_key.is_file():
        diagnostics.append(_github_access(shared, config.credentials.github_app_key))
    else:
        diagnostics.append(
            Diagnostic(
                "GitHub authentication and Project mapping",
                False,
                "App authentication cannot be attempted until shared configuration and key "
                "are available.",
                "Fix the shared configuration and private App key, then rerun doctor.",
            )
        )
    return diagnostics


def model_authentication(profiles: Mapping[str, str]) -> list[Diagnostic]:
    try:
        commands = AndSceneAdapter.authentication_commands(profiles)
    except ReadinessError as error:
        return [Diagnostic("model authentication", False, str(error), "Correct the role profiles.")]
    return [
        _command_check(
            (
                "cursor CLI availability"
                if command[0] == "cursor"
                else f"{command[0]} model authentication"
            ),
            command,
            (
                "Install Cursor and authenticate it on this Mac, then rerun doctor."
                if command[0] == "cursor"
                else f"Authenticate {command[0]} on this Mac, then rerun doctor."
            ),
            timeout=5 if command[0] == "cursor" else 30,
        )
        for command in commands
    ]


def format_doctor(diagnostics: Iterable[Diagnostic]) -> str:
    values = list(diagnostics)
    lines = [
        "doctor is diagnostic only: starts no evaluations and repairs no credentials or "
        "configuration."
    ]
    for item in values:
        state = "OK" if item.available else "FAIL"
        lines.extend((f"{item.name}: {state} — {item.detail}", f"  action: {item.action}"))
    return "\n".join(lines)


def status(store: ClaimStore, config: LocalConfig | None = None) -> str:
    """Render saved execution state without polling, admitting, or modifying controls."""
    lines = [f"paused: {str(store.is_paused()).lower()}"]
    claims = store.all_claims()
    active_by_claim = {run.claim_id: run for run in store.nonterminal_runs()}
    if not claims:
        lines.append("current: none")
    for diagnostic in ("readiness", "quota-error"):
        saved = store.get_setting("runtime", diagnostic)
        if saved and saved.get("reason"):
            lines.append(f"{diagnostic}: {saved['reason']}")
    for claim in claims:
        run = active_by_claim.get(claim.id)
        if run is not None:
            lines.append(_current_line(claim, run))
            lines.extend(_progress_lines(run))
        else:
            lines.append(f"claim: {claim.repository}#{claim.issue_number} ({claim.lifecycle})")
        lines.extend(_hold_lines(store, claim, config))
        lines.extend(_reporting_lines(claim))
        lines.extend(_cleanup_lines(claim))
    if config is not None:
        now = datetime.now(config.schedule.timezone)
        if not config.schedule.allows_admission(now):
            lines.append(
                f"admission window: closed; next permitted start: {_next_start(now, config)}"
            )
        else:
            lines.append("admission window: open")
    return "\n".join(lines)


def render_launch_agent(
    executable: Path, config_path: Path, root: Path, log_path: Path, credential_path: Path
) -> str:
    """Return a launchd-safe per-user controller definition with explicit paths."""
    values = {
        "__EXECUTABLE__": str(executable),
        "__CONFIG__": str(config_path),
        "__ROOT__": str(root),
        "__LOG__": str(log_path),
        "__CREDENTIAL__": str(credential_path),
    }
    rendered = _PLIST_TEMPLATE
    for token, value in values.items():
        rendered = rendered.replace(token, html.escape(value, quote=True))
    return rendered


def _project_mappings(config: SharedConfig) -> None:
    config.project.status.option("ready")
    config.project.status.option("running")
    config.project.status.option("review")
    config.project.status.option("done")
    config.project.owner.option("factory")
    config.project.verdict.option("pending-human-review")
    config.project.verdict.option("failed")


def _github_access(shared: SharedConfig, key: Path) -> Diagnostic:
    """Use the App identity to prove the configured Project is readable, without mutation."""
    try:
        runner = SubprocessGhRunner()
        token = InstallationTokenProvider(
            AppCredentials(shared.app_id, shared.installation_id, key)
        )
        client = GitHubClient(runner, token)
        client.validate_project(shared.project)
        client.list_project_items(shared.project.id)
    except (GitHubApiError, OSError) as error:
        return Diagnostic(
            "GitHub authentication and Project mapping",
            False,
            f"App authentication or Project access failed: {error}",
            "Restore App credentials and Project access, then rerun doctor.",
        )
    return Diagnostic(
        "GitHub authentication and Project mapping",
        True,
        f"App authentication and read access to Project {shared.project.id} succeeded.",
        "No action required.",
    )


def _private_file(name: str, path: Path) -> Diagnostic:
    try:
        metadata = path.stat()
    except OSError:
        return Diagnostic(name, False, f"file is unavailable: {path}", f"Create or restore {path}.")
    if not stat.S_ISREG(metadata.st_mode):
        return Diagnostic(
            name,
            False,
            f"path is not a regular file: {path}",
            f"Provide the expected private credential file at {path}.",
        )
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077:
        return Diagnostic(
            name,
            False,
            f"file permissions are too broad ({mode:04o}): {path}",
            f"Restrict {path} to its owner (for example, chmod 600).",
        )
    return Diagnostic(name, True, f"private file is readable: {path}", "No action required.")


def _repository_checks(config: LocalConfig) -> list[Diagnostic]:
    checks: list[tuple[str, Path, str]] = [
        ("eval repository", config.repositories.agent_evals, "and-scene harness and fixtures"),
        ("Agent Runner repository", config.repositories.agent_runner, "sandbox launcher"),
        ("Agent Skills repository", config.repositories.agent_skills, "selected Skills revision"),
    ]
    result: list[Diagnostic] = []
    for name, path, purpose in checks:
        available = path.is_dir() and (path / ".git").exists()
        detail = (
            f"repository is available: {path}"
            if available
            else f"repository/worktree is unavailable: {path}"
        )
        result.append(
            Diagnostic(
                name, available, detail, f"Clone or repair the configured repository for {purpose}."
            )
        )
    runner = config.repositories.agent_runner / "scripts" / "sandbox-run.sh"
    eval_entry = config.repositories.agent_evals / "evals/agent-runner/and-scene/run.sh"
    result.append(
        Diagnostic(
            "selected suite entry point and launcher",
            runner.is_file() and eval_entry.is_file(),
            "selected and-scene entry point and Runner launcher are present"
            if runner.is_file() and eval_entry.is_file()
            else "selected suite entry point or Runner launcher is unavailable",
            "Install the pinned suite and Runner revisions with their required launcher support.",
        )
    )
    return result


def _suite_environment(path: Path) -> Diagnostic:
    if not path.is_file():
        return Diagnostic(
            "suite candidate credentials",
            False,
            f"token environment file is unavailable: {path}",
            "Create the separately managed suite environment file; do not put the App key in it.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return Diagnostic(
            "suite candidate credentials",
            False,
            f"token environment file is unreadable: {error}",
            "Fix file permissions, path, or UTF-8 content, then rerun doctor.",
        )
    if not content.strip():
        return Diagnostic(
            "suite candidate credentials",
            False,
            "token environment file is empty",
            "Add suite credentials.",
        )
    return Diagnostic(
        "suite candidate credentials",
        True,
        f"environment file is present: {path}",
        "No action required.",
    )


def _command_check(
    name: str, command: tuple[str, ...], action: str, *, timeout: float = 5
) -> Diagnostic:
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(name, False, f"check could not run: {error}", action)
    if completed.returncode != 0:
        return Diagnostic(name, False, "check reported unavailable or unauthenticated", action)
    return Diagnostic(name, True, "check succeeded", "No action required.")


def _free_space(config: LocalConfig) -> Diagnostic:
    probe = config.storage_root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free_gib = shutil.disk_usage(probe).free / 1024**3
    except OSError as error:
        return Diagnostic(
            "free storage",
            False,
            f"cannot inspect storage at {probe}: {error}",
            "Restore access to the configured storage root, then rerun doctor.",
        )
    enough = free_gib >= config.limits.minimum_free_gib
    return Diagnostic(
        "free storage",
        enough,
        f"{free_gib:.1f} GiB free at {probe}; configured minimum is "
        f"{config.limits.minimum_free_gib} GiB",
        "Free storage or lower the configured floor before admitting another repetition."
        if not enough
        else "No action required.",
    )


def _current_line(claim: Claim, run: Run) -> str:
    return f"current: {claim.repository}#{claim.issue_number} {run.unit_key} ({run.status})"


def _progress_lines(run: Run) -> list[str]:
    values: list[str] = []
    if run.attempt_number >= 0:
        values.append(f"attempt: {run.attempt_number + 1} ({run.reason})")
    if run.progress:
        values.append(f"progress: {run.progress}")
    return values


def _hold_lines(store: ClaimStore, claim: Claim, config: LocalConfig | None) -> list[str]:
    lines: list[str] = []
    readiness = store.get_hold(claim.id, "readiness")
    if readiness is not None:
        reason = readiness.get("reason", "unknown prerequisite")
        lines.append(f"readiness: {reason}")
    quota = store.get_hold(claim.id, "quota")
    if quota is not None:
        until = quota.get("until")
        lines.append(
            f"quota hold: {until if isinstance(until, str) else 'operator action required'}"
        )
    if claim.lifecycle == "waiting" and readiness is None and quota is None:
        lines.append("blocking condition: waiting; inspect the latest controller report")
    if config is not None and store.is_paused():
        lines.append("blocking condition: paused; resume clears only this control")
    return lines


def _reporting_lines(claim: Claim) -> list[str]:
    pending: list[str] = [event.key for event in _events(claim) if event.comment_id is None]
    failures = claim.reporting.get("delivery_failures")
    if isinstance(failures, Mapping):
        failure_values = cast(Mapping[str, object], failures)
        pending.extend(key for key in failure_values if key not in pending)
    return [f"unfinished reporting: {', '.join(pending)}"] if pending else []


def _cleanup_lines(claim: Claim) -> list[str]:
    error = claim.cleanup.get("last_error")
    return [f"cleanup errors: {error}"] if error is not None else []


def _events(claim: Claim) -> list[Event]:
    raw = claim.reporting.get("events")
    if not isinstance(raw, Mapping):
        return []
    result: list[Event] = []
    values = cast(Mapping[str, object], raw)
    for key, value in values.items():
        if isinstance(value, Mapping):
            event = cast(Mapping[str, object], value)
            body = event.get("body")
            if not isinstance(body, str):
                continue
            comment_id = event.get("comment_id")
            result.append(Event(key, body, comment_id if isinstance(comment_id, str) else None))
    return result


def _next_start(now: datetime, config: LocalConfig) -> str:
    start = config.schedule.start_hour
    candidate = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.isoformat()


_PLIST_TEMPLATE = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict>
  <key>Label</key><string>com.codagent.agent-factory</string>
  <key>ProgramArguments</key><array><string>__EXECUTABLE__</string><string>--config</string><string>__CONFIG__</string><string>resident</string></array>
  <key>WorkingDirectory</key><string>__ROOT__</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>__LOG__</string>
  <key>StandardErrorPath</key><string>__LOG__</string>
  <key>EnvironmentVariables</key><dict><key>AGENT_FACTORY_ROOT</key><string>__ROOT__</string><key>AGENT_FACTORY_GITHUB_APP_KEY</key><string>__CREDENTIAL__</string></dict>
</dict></plist>
"""
