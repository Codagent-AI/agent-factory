"""Read-only operator diagnostics, saved-state reporting, and launchd packaging."""

from __future__ import annotations

import html
import re
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


def doctor(config: LocalConfig, *, include_fix: bool = True) -> list[Diagnostic]:
    """Inspect prerequisites only; this function never starts work or repairs state.

    ``include_fix`` is disabled by the runtime's shared-prerequisite gate: fix-kind
    readiness is diagnosed and admitted per kind through ``handler.readiness()``, and
    must never block eval admission just because a fix mirror or clone isn't ready yet.
    """
    diagnostics: list[Diagnostic] = []
    shared: SharedConfig | None = None
    try:
        shared = SharedConfig.from_file(config.shared_config)
        _project_mappings(shared)
        diagnostics.append(_harness_branch_diagnostic(shared, config))
    except (ConfigurationError, OSError) as error:
        diagnostics.append(
            Diagnostic(
                "shared configuration",
                False,
                str(error),
                "Install a valid shared TOML; configure eval.harness_ref as a branch "
                "name, not a commit SHA.",
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
    if shared is not None and include_fix:
        diagnostics.extend(_fix_diagnostics(config, shared))
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
    fix_heading_printed = False
    for item in values:
        if item.name.startswith("fix ") and not fix_heading_printed:
            lines.append("-- fix --")
            fix_heading_printed = True
        state = "OK" if item.available else "FAIL"
        lines.extend((f"{item.name}: {state} — {item.detail}", f"  action: {item.action}"))
    return "\n".join(lines)


def status(store: ClaimStore, config: LocalConfig | None = None) -> str:
    """Render saved execution state without polling, admitting, or modifying controls."""
    lines = [f"paused: {str(store.is_paused()).lower()}"]
    lines.extend(_slot_lines(store))
    claims = store.all_claims()
    active_by_claim = {run.claim_id: run for run in store.nonterminal_runs()}
    if not claims:
        lines.append("current: none")
    quota_error = store.get_setting("runtime", "quota-error")
    if quota_error and quota_error.get("reason"):
        lines.append(f"quota-error: {quota_error['reason']}")
    for key, saved in sorted(store.get_settings_by_prefix("runtime", "readiness:").items()):
        if saved.get("reason"):
            lines.append(f"{key}: {saved['reason']}")
    lines.extend(_quota_hold_lines(store, config))
    for claim in claims:
        run = active_by_claim.get(claim.id)
        if run is not None:
            lines.append(_current_line(claim, run))
            lines.extend(_progress_lines(run))
        else:
            lines.append(f"claim: {claim.repository}#{claim.issue_number} ({claim.lifecycle})")
        lines.extend(_blocked_lines(store, claim))
        lines.extend(_hold_lines(store, claim, config))
        lines.extend(_reporting_lines(claim))
        lines.extend(_cleanup_lines(claim))
        lines.extend(_sync_lines(store, claim))
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


def _harness_branch_diagnostic(shared: SharedConfig, config: LocalConfig) -> Diagnostic:
    """Resolve the configured harness branch locally; doctor never fetches or mutates state."""
    from agent_factory import runtime

    try:
        sha = runtime._resolve_revision(  # pyright: ignore[reportPrivateUsage]
            config.repositories.agent_evals, shared.eval.harness_ref, fetch=False
        )
    except ReadinessError as error:
        return Diagnostic(
            "shared configuration",
            False,
            f"harness branch {shared.eval.harness_ref} could not be resolved locally: {error}",
            "Fetch the configured agent_evals repository, then rerun doctor.",
        )
    return Diagnostic(
        "shared configuration",
        True,
        f"harness branch {shared.eval.harness_ref} → {sha}",
        "No action required.",
    )


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


_FIX_TOKEN_LINE = re.compile(r"^GH_TOKEN=(.+)$")


def _fix_diagnostics(local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
    """Fix-kind readiness, kept visibly distinct from shared and eval diagnostics."""
    if not shared.fix.targets:
        return []
    from agent_factory.work_kinds.fix.readiness import check_readiness

    # Readiness diagnostics already carry the "fix " prefix in their names.
    diagnostics = list(check_readiness(local, shared))
    diagnostics.extend(_mirror_diagnostics(local, shared))
    diagnostics.extend(_working_clone_diagnostics(local))
    memory = check_memory_headroom(local.limits.memory_reservation_gib)
    diagnostics.append(
        Diagnostic("fix docker memory", memory.available, memory.detail, memory.action)
    )
    credential_path = local.credentials.fix_environment
    token = _read_fix_token(credential_path)
    if token is not None:
        diagnostics.extend(_fix_identity_diagnostics(shared, token))
    elif credential_path is not None and credential_path.is_file():
        diagnostics.append(
            Diagnostic(
                "fix credential identity",
                False,
                f"no GH_TOKEN assignment found in {credential_path}; identity checks were skipped",
                "Add a GH_TOKEN=<value> line to the fix credential file.",
            )
        )
    return diagnostics


def _mirror_diagnostics(local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for target in shared.fix.targets:
        name = f"fix mirror {target.repository}"
        mirror_path = local.storage_root / "mirrors" / f"{target.repository.replace('/', '__')}.git"
        action = (
            f"Let fix admission create and fetch the mirror at {mirror_path}, or run "
            f"`git clone --mirror` manually."
        )
        if not mirror_path.is_dir():
            diagnostics.append(
                Diagnostic(name, False, f"mirror is not yet created: {mirror_path}", action)
            )
            continue
        try:
            completed = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(mirror_path),
                    "ls-remote",
                    "--exit-code",
                    "origin",
                    "HEAD",
                ],
                capture_output=True,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            diagnostics.append(
                Diagnostic(name, False, f"mirror fetch check failed: {error}", action)
            )
            continue
        if completed.returncode != 0:
            diagnostics.append(
                Diagnostic(name, False, f"mirror at {mirror_path} cannot reach origin", action)
            )
            continue
        diagnostics.append(Diagnostic(name, True, f"mirror is fetchable: {mirror_path}", ""))
    return diagnostics


def _working_clone_diagnostics(local: LocalConfig) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for key, path in local.repositories.working_clones.items():
        name = f"fix working clone {key}"
        available = path.is_dir() and (path / ".git").exists()
        detail = (
            f"working clone is available: {path}"
            if available
            else f"working clone is unavailable or not a Git repository: {path}"
        )
        diagnostics.append(
            Diagnostic(
                name,
                available,
                detail,
                "" if available else f"Clone {key} to {path} for the merge sync.",
            )
        )
    return diagnostics


def _read_fix_token(path: Path | None) -> str | None:
    """Extract GH_TOKEN even from a malformed file; shape correctness is check_readiness's job."""
    if path is None or not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _FIX_TOKEN_LINE.match(stripped)
        if match:
            return match.group(1)
    return None


def _fix_identity_diagnostics(shared: SharedConfig, token: str) -> list[Diagnostic]:
    action = "Restore a valid, non-App fix credential token."
    runner = SubprocessGhRunner()
    client = GitHubClient(runner, lambda: token)
    try:
        login = client.whoami()
    except (GitHubApiError, OSError) as error:
        return [
            Diagnostic(
                "fix credential identity",
                False,
                f"fix credential does not authenticate: {error}",
                action,
            )
        ]
    diagnostics = [
        Diagnostic("fix credential identity", True, f"fix credential authenticates as {login}", "")
    ]
    is_app_identity = login == shared.bot_login
    diagnostics.append(
        Diagnostic(
            "fix credential is not the App identity",
            not is_app_identity,
            f"fix credential identity is {login}"
            + (" (equals the App identity)" if is_app_identity else ""),
            "Use a distinct credential for fix PRs, separate from the App identity."
            if is_app_identity
            else "",
        )
    )
    try:
        role = client.organization_role(shared.organization, login)
    except OSError as error:
        role = None
        diagnostics.append(
            Diagnostic(
                "fix credential organization role",
                False,
                f"organization role check could not run: {error}",
                "Retry once the local `gh` invocation succeeds.",
            )
        )
    else:
        is_admin = role == "admin"
        diagnostics.append(
            Diagnostic(
                "fix credential organization role",
                True,
                f"fix credential organization role: {role or 'unknown'}"
                + (" — warning: this identity is an organization admin" if is_admin else ""),
                "Prefer a non-admin machine user for the fix credential." if is_admin else "",
            )
        )
    for target in shared.fix.targets:
        try:
            reachable = client.can_read_repository(target.repository)
        except OSError as error:
            diagnostics.append(
                Diagnostic(
                    f"fix credential reach {target.repository}",
                    False,
                    f"reachability check could not run: {error}",
                    "Retry once the local `gh` invocation succeeds.",
                )
            )
            continue
        diagnostics.append(
            Diagnostic(
                f"fix credential reach {target.repository}",
                reachable,
                f"fix credential can read {target.repository}"
                if reachable
                else f"fix credential cannot read {target.repository}",
                "" if reachable else f"Grant the fix credential access to {target.repository}.",
            )
        )
    return diagnostics


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


_MEMORY_UNITS: Mapping[str, float] = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def _parse_memory_amount(text: str) -> float:
    text = text.strip()
    for suffix in sorted(_MEMORY_UNITS, key=len, reverse=True):
        if text.lower().endswith(suffix):
            number = text[: -len(suffix)].strip()
            return float(number) * _MEMORY_UNITS[suffix]
    return float(text)


def check_memory_headroom(reservation_gib: int, *, docker: str = "docker") -> Diagnostic:
    """Compare Docker's memory allowance minus running containers' usage against a reservation."""
    try:
        total = subprocess.run(
            [docker, "info", "--format", "{{.MemTotal}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if total.returncode != 0:
            raise OSError(total.stderr.strip() or "docker info failed")
        total_bytes = int(total.stdout.strip())
        stats = subprocess.run(
            [docker, "stats", "--no-stream", "--format", "{{.MemUsage}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if stats.returncode != 0:
            raise OSError(stats.stderr.strip() or "docker stats failed")
        used_bytes = sum(
            _parse_memory_amount(line.split("/", 1)[0])
            for line in stats.stdout.splitlines()
            if line.strip()
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            "memory",
            False,
            f"memory probe failed: {error}",
            "Restore Docker access, then rerun admission.",
        )
    reservation_bytes = reservation_gib * 1024**3
    headroom_bytes = total_bytes - used_bytes
    available = headroom_bytes >= reservation_bytes
    return Diagnostic(
        "memory",
        available,
        f"{headroom_bytes / 1024**3:.2f} GiB headroom; configured reservation is "
        f"{reservation_gib} GiB",
        "Wait for memory to free up before admitting another attempt."
        if not available
        else "No action required.",
    )


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


def _slot_lines(store: ClaimStore) -> list[str]:
    lines: list[str] = []
    for kind in ("eval", "fix"):
        runs = store.nonterminal_runs(kind=kind)
        run = runs[0] if runs else None
        claim = store.get_claim(run.claim_id) if run is not None else None
        if run is None or claim is None:
            lines.append(f"{kind} slot: free")
            continue
        lines.append(
            f"{kind} slot: {claim.repository}#{claim.issue_number} {run.unit_key} ({run.status})"
        )
    return lines


def _blocked_lines(store: ClaimStore, claim: Claim) -> list[str]:
    """Report the decline tied to the claim's latest run, since stored events are key-sorted."""
    if claim.lifecycle != "blocked":
        return []
    reason = "needs input"
    fix_runs = [run for run in store.runs_for_claim(claim.id) if run.unit_key == "fix"]
    if fix_runs:
        latest_key = f"{fix_runs[-1].id}:needs-input"
        for event in _events(claim):
            if event.key == latest_key:
                body = event.body.removeprefix("Needs input.\n\n").strip()
                reason = body or reason
                break
    return [f"blocked: {claim.repository}#{claim.issue_number} — {reason}"]


def _quota_hold_lines(store: ClaimStore, config: LocalConfig | None) -> list[str]:
    holds = store.get_settings_by_prefix("admission", "quota:")
    if not holds:
        return []
    kind_providers = _kind_providers(config)
    lines: list[str] = []
    for key, hold in sorted(holds.items()):
        provider = key.split(":", 1)[1]
        until = hold.get("until")
        until_text = until if isinstance(until, str) else "operator action required"
        affected = sorted(
            kind for kind, providers in kind_providers.items() if provider in providers
        )
        blocks = ", ".join(affected) if affected else "none"
        lines.append(f"quota hold: {provider} until {until_text}; blocks: {blocks}")
    return lines


def _kind_providers(config: LocalConfig | None) -> dict[str, set[str]]:
    if config is None:
        return {}
    try:
        shared = SharedConfig.from_file(config.shared_config)
    except (ConfigurationError, OSError):
        return {}

    from agent_factory.work_kinds.base import providers_from_roles as providers_of

    return {"eval": providers_of(shared.eval.defaults), "fix": providers_of(shared.fix.defaults)}


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


def _sync_lines(store: ClaimStore, claim: Claim) -> list[str]:
    if claim.kind != "fix" or claim.lifecycle != "settled":
        return []
    from agent_factory.work_kinds.fix.sync import _find_pr  # pyright: ignore[reportPrivateUsage]

    if _find_pr(store, claim) is None:
        return []
    sync = claim.reporting.get("sync")
    sync_map: Mapping[str, object] = (
        cast(Mapping[str, object], sync) if isinstance(sync, Mapping) else {}
    )
    if sync_map.get("completed"):
        return []
    reason = sync_map.get("blocked_reason")
    detail = reason if isinstance(reason, str) else "awaiting merge"
    return [f"pending sync: {detail}"]


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
