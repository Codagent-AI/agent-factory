"""Independent, durable supervision for a single reserved factory run.

The controller only reserves and starts this module.  Once started, this
process and the suite process both have their own sessions and all state needed
for replacement observation is in SQLite rather than a controller pipe.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from agent_factory.controller import ExecutionPlan
from agent_factory.store import NONTERMINAL_RUN_STATUSES, ClaimStore, Run

_POLL_SECONDS = 0.05
_PROGRESS_HEARTBEAT_SECONDS = 5.0


class SupervisorLaunchError(RuntimeError):
    """A replacement watcher could not be started locally."""


class ProcessProbeError(RuntimeError):
    """The process probe could not provide a definitive answer."""


@dataclass(frozen=True)
class SupervisionLimits:
    inactivity_seconds: float = 30 * 60
    execution_seconds: float = 6 * 60 * 60
    total_seconds: float = 12 * 60 * 60


@dataclass(frozen=True)
class ResultRead:
    result: dict[str, object] | None
    error: str | None = None


def launch_supervisor(
    state_path: Path,
    run_id: str,
    plan: ExecutionPlan,
    limits: SupervisionLimits | None = None,
    *,
    config_path: Path | None = None,
) -> subprocess.Popen[bytes]:
    """Commit launch intent, then detach a watcher from the calling controller."""
    store = ClaimStore(state_path)
    try:
        effective_limits = limits or SupervisionLimits()
        store.configure_run(run_id, plan=_plan_document(plan), limits=asdict(effective_limits))
        run = _required_run(store, run_id)
        return _spawn_watcher(state_path, run, config_path)
    finally:
        store.close()


def resume_supervisor(
    state_path: Path, run_id: str, *, config_path: Path | None = None
) -> subprocess.Popen[bytes] | None:
    """Attach a fresh watcher to a nonterminal durable run without relaunching it."""
    store = ClaimStore(state_path)
    try:
        run = store.get_run(run_id)
        if run is None or run.status not in {"running", "observing"}:
            return None
        watcher_status = _identity_status(run.supervisor)
        if watcher_status == "alive":
            return None
        if watcher_status == "unknown":
            store.report_uncertainty(run.id, "supervisor process identity cannot be verified")
            return None
        if not store.claim_watcher_launch(run.id):
            return None
        try:
            return _spawn_watcher(state_path, run, config_path)
        except OSError as error:
            store.release_watcher_launch(run.id)
            raise SupervisorLaunchError(str(error)) from error
    finally:
        store.close()


def _spawn_watcher(state_path: Path, run: Run, config_path: Path | None) -> subprocess.Popen[bytes]:
    """Detach the watcher with the same identity, logging, and session setup on every launch."""
    log_dir = state_path.parent / "logs" / "runs" / run.id
    log_dir.mkdir(parents=True, exist_ok=True)
    arguments = [sys.executable, "-m", "agent_factory.supervisor", "--state", str(state_path)]
    arguments.extend(("--run-id", run.id, "--nonce", run.launch_nonce))
    if config_path is not None:
        arguments.extend(("--config", str(config_path)))
    # Popen owns only the parent's file descriptor; the child has a real file
    # descriptor and its own session, so controller exit cannot close suite I/O.
    with (log_dir / "supervisor.log").open("ab", buffering=0) as output:
        return subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )


def supervise(state_path: Path, run_id: str, nonce: str) -> None:
    """Observe or launch exactly one suite process under the per-run lock."""
    with _run_lock(state_path, run_id):
        store = ClaimStore(state_path)
        try:
            run = _required_run(store, run_id)
            if run.launch_nonce != nonce:
                return
            if run.status not in NONTERMINAL_RUN_STATUSES:
                return
            try:
                plan = _plan_from_document(run.plan)
                limits = _limits_from_document(run.plan)
            except RuntimeError:
                store.report_uncertainty(run.id, "invalid persisted plan")
                return
            existing = run.process
            existing_status = _identity_status(existing)
            if existing and existing_status == "alive":
                watcher = _supervisor_identity()
                watcher["process"] = existing
                store.update_supervisor(run.id, watcher)
                _observe(store, run.id, plan, limits, existing)
                return
            if existing_status == "unknown":
                store.report_uncertainty(run.id, "recorded execution identity cannot be probed")
                return
            if existing:
                # PID reuse or an unverified vanishing process is deliberately not a
                # launch signal.  Preserve the global slot for operator reconciliation.
                store.report_uncertainty(
                    run.id, "recorded execution identity is no longer verifiable"
                )
                return
            if run.status != "reserved":
                store.report_uncertainty(run.id, "running run has no recorded execution identity")
                return
            _launch_and_observe(store, run, plan, limits)
        finally:
            store.close()


def _launch_and_observe(
    store: ClaimStore, run: Run, plan: ExecutionPlan, limits: SupervisionLimits
) -> None:
    output = Path(run.evidence_path) / "factory-suite.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    environment.update({str(key): str(value) for key, value in plan.allowed_environment.items()})
    try:
        with output.open("ab", buffering=0) as stream:
            child = subprocess.Popen(
                list(plan.argv),
                cwd=plan.working_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    except OSError as error:
        store.finish_run(
            run.id,
            execution_status="failed",
            result={"reason": "suite launch failed", "error": str(error)},
        )
        return
    try:
        identity = _process_identity(child.pid, plan)
    except ProcessProbeError:
        store.report_uncertainty(run.id, "new execution identity cannot be probed")
        return
    if identity is None or not store.begin_run(
        run.id,
        launch_nonce=run.launch_nonce,
        supervisor=_supervisor_identity(),
        process=identity,
    ):
        # A stale watcher must not leave an untracked child behind.
        if identity is not None and _identity_status(identity) == "alive":
            _terminate(identity)
        return
    _observe(store, run.id, plan, limits, identity)


def _observe(
    store: ClaimStore,
    run_id: str,
    plan: ExecutionPlan,
    limits: SupervisionLimits,
    identity: Mapping[str, object],
) -> None:
    run = _required_run(store, run_id)
    progress: dict[str, object] = dict(run.progress)
    wall_anchor = time.time()
    monotonic_anchor = time.monotonic()
    started = _number(progress.get("started_at"), wall_anchor)
    last_progress = _number(progress.get("last_progress_at"), started)
    if "elapsed_seconds" in progress:
        gap = max(0.0, wall_anchor - _number(progress.get("persisted_at"), wall_anchor))
        started = wall_anchor - _number(progress.get("elapsed_seconds"), 0) - gap
        last_progress = wall_anchor - _number(progress.get("idle_seconds"), 0) - gap
    observed_sources = _saved_source_versions(progress.get("sources")) or _source_versions(
        plan.progress_sources
    )
    last_persisted = _number(progress.get("persisted_at"), 0)
    if not progress:
        progress = {
            "started_at": started,
            "last_progress_at": last_progress,
            "sources": observed_sources,
            "persisted_at": time.time(),
        }
        store.update_progress(run_id, progress)
        last_persisted = _number(progress["persisted_at"], 0)
    last_container_probe = float("-inf")
    while True:
        run = _required_run(store, run_id)
        # The wall clock is used only to anchor persisted timestamps on attachment.
        # All decisions during this watcher lifetime advance by monotonic elapsed time.
        now = wall_anchor + (time.monotonic() - monotonic_anchor)
        result_read = _load_result(_artifact_root(plan, run.evidence_path))
        process_status = _identity_status(identity)
        if plan.ownership_hints.get("suite") == "and-scene" and now - last_container_probe >= 5:
            try:
                recorded = progress.get("container")
                if not isinstance(recorded, Mapping):
                    discovered = discover_container(_artifact_root(plan, run.evidence_path))
                    if discovered is not None:
                        progress["container"] = discovered
                        store.update_progress(run_id, progress)
                last_container_probe = now
            except (OSError, subprocess.TimeoutExpired, ProcessProbeError) as error:
                store.report_uncertainty(run_id, str(error))
                return
        container = progress.get("container")
        if process_status == "missing" and isinstance(container, Mapping):
            try:
                record = cast(Mapping[str, object], container)
                observed = inspect_container(str(record["id"]))
                if observed is not None:
                    if not container_matches_recorded_ownership(record, observed):
                        raise ProcessProbeError("recorded container ownership changed")
                    state = observed.get("State")
                    if (
                        isinstance(state, Mapping)
                        and cast(Mapping[str, object], state).get("Running") is True
                    ):
                        process_status = "alive"
            except (OSError, subprocess.TimeoutExpired, ProcessProbeError) as error:
                store.report_uncertainty(run_id, str(error))
                return
        if process_status == "unknown":
            store.report_uncertainty(run_id, "owned process identity cannot be probed")
            return
        changed, observed_sources = _progress_changed(plan.progress_sources, observed_sources)
        if changed:
            last_progress = now
        progress.update(
            {"started_at": started, "last_progress_at": last_progress, "sources": observed_sources}
        )
        diagnostic_changed = progress.get("result_error") != result_read.error
        if result_read.error is not None:
            progress["result_error"] = result_read.error
        else:
            progress.pop("result_error", None)
        if changed or diagnostic_changed or now - last_persisted >= _PROGRESS_HEARTBEAT_SECONDS:
            progress["persisted_at"] = time.time()
            progress["elapsed_seconds"] = now - started
            progress["idle_seconds"] = now - last_progress
            store.update_progress(run_id, progress)
            last_persisted = now
        if result_read.result is not None and process_status == "missing":
            store.finish_run(
                run_id,
                execution_status=_result_status(result_read.result),
                result=result_read.result,
            )
            return
        if result_read.error is not None and process_status == "missing":
            store.finish_run(
                run_id,
                execution_status="failed",
                result={"reason": "invalid result.json", "error": result_read.error},
            )
            return
        if process_status == "missing":
            store.finish_run(
                run_id,
                execution_status="interrupted",
                result={"reason": "owned process exited without durable result"},
            )
            return
        if run.cancellation_requested:
            if _terminate_execution(identity, progress.get("container")):
                store.finish_run(
                    run_id, execution_status="cancelled", result={"reason": "cancelled"}
                )
            else:
                store.report_uncertainty(run_id, "cancellation ownership could not be verified")
            return
        timeout = _timeout(now, started, last_progress, limits)
        if timeout is not None:
            if _terminate_execution(identity, progress.get("container")):
                store.finish_run(run_id, execution_status="timed_out", result={"timeout": timeout})
            else:
                store.report_uncertainty(
                    run_id, f"{timeout} timeout ownership could not be verified"
                )
            return
        time.sleep(_POLL_SECONDS)


def inspect_container(container_id: str) -> dict[str, object] | None:
    result = subprocess.run(
        ["docker", "inspect", container_id], capture_output=True, text=True, check=False, timeout=10
    )
    if result.returncode != 0:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--no-trunc", "-q"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if listing.returncode != 0 or container_id in listing.stdout.splitlines():
            raise ProcessProbeError("container state cannot be verified")
        return None
    try:
        values: object = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ProcessProbeError("invalid Docker inspection") from error
    if not isinstance(values, list):
        raise ProcessProbeError("invalid Docker inspection")
    entries = cast(list[object], values)
    if len(entries) != 1 or not isinstance(entries[0], dict):
        raise ProcessProbeError("invalid Docker inspection")
    return cast(dict[str, object], entries[0])


def discover_container(artifact: str) -> dict[str, object] | None:
    result = subprocess.run(
        ["docker", "ps", "--no-trunc", "-q", "--filter", f"volume={artifact}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ProcessProbeError("Docker ownership discovery unavailable")
    matches: list[dict[str, object]] = []
    for container_id in result.stdout.splitlines():
        observed = inspect_container(container_id)
        if observed is None:
            continue
        record = {"id": container_id, "image": observed.get("Image"), "artifact_path": artifact}
        if container_matches_recorded_ownership(record, observed):
            matches.append(record)
    if len(matches) > 1:
        raise ProcessProbeError("multiple containers match the repetition artifact mount")
    return matches[0] if matches else None


def stop_owned_container(recorded: Mapping[str, object]) -> bool:
    """Stop only the recorded container whose current immutable identity and mount agree."""
    container_id = recorded.get("id")
    if not isinstance(container_id, str):
        return False
    try:
        observed = inspect_container(container_id)
        if observed is None:
            return True
        if not container_matches_recorded_ownership(recorded, observed):
            return False
        stopped = subprocess.run(
            ["docker", "stop", "--time", "10", container_id],
            capture_output=True,
            check=False,
            timeout=15,
        )
        return stopped.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ProcessProbeError):
        return False


def _terminate_execution(identity: Mapping[str, object], container: object) -> bool:
    if isinstance(container, Mapping) and not stop_owned_container(
        cast(Mapping[str, object], container)
    ):
        return False
    return _terminate(identity)


def container_matches_recorded_ownership(
    recorded: Mapping[str, object], inspect: Mapping[str, object]
) -> bool:
    """Require immutable container identity and the exact run artifact mount."""
    container_id = recorded.get("id")
    image = recorded.get("image")
    artifact = recorded.get("artifact_path")
    if not all(isinstance(value, str) and value for value in (container_id, image, artifact)):
        return False
    if inspect.get("Id") != container_id or inspect.get("Image") != image:
        return False
    expected = str(Path(cast(str, artifact)).resolve())
    mounts = inspect.get("Mounts")
    if not isinstance(mounts, list):
        return False
    return any(
        _mount_matches(cast(Mapping[str, object], mount), expected)
        for mount in cast(list[object], mounts)
        if isinstance(mount, Mapping)
    )


def _mount_matches(mount: Mapping[str, object], artifact_path: str) -> bool:
    return mount.get("Destination") == "/artifacts" and mount.get("Source") == artifact_path


def _timeout(
    now: float, started: float, last_progress: float, limits: SupervisionLimits
) -> str | None:
    if now - started >= limits.total_seconds:
        return "total"
    if now - started >= limits.execution_seconds:
        return "execution"
    if now - last_progress >= limits.inactivity_seconds:
        return "inactivity"
    return None


def _plan_document(plan: ExecutionPlan) -> dict[str, object]:
    return {
        "argv": list(plan.argv),
        "working_directory": plan.working_directory,
        "allowed_environment": dict(plan.allowed_environment),
        "credential_files": list(plan.credential_files),
        "progress_sources": list(plan.progress_sources),
        "ownership_hints": dict(plan.ownership_hints),
        "resume": plan.resume,
    }


def _plan_from_document(document: Mapping[str, object]) -> ExecutionPlan:
    argv = document.get("argv")
    working_directory = document.get("working_directory")
    if not isinstance(argv, list):
        raise RuntimeError("run has no valid persisted argument vector")
    argv_values = cast(list[object], argv)
    if not all(isinstance(value, str) for value in argv_values):
        raise RuntimeError("run has no valid persisted argument vector")
    if not isinstance(working_directory, str):
        raise RuntimeError("run has no valid persisted working directory")
    return ExecutionPlan(
        tuple(cast(str, value) for value in argv_values),
        working_directory,
        _string_map(document.get("allowed_environment")),
        tuple(_strings(document.get("credential_files"))),
        tuple(_strings(document.get("progress_sources"))),
        _string_map(document.get("ownership_hints")),
        document.get("resume") is True,
    )


def _limits_from_document(document: Mapping[str, object]) -> SupervisionLimits:
    raw = document.get("limits")
    if not isinstance(raw, Mapping):
        return SupervisionLimits()
    limits = cast(Mapping[str, object], raw)
    return SupervisionLimits(
        inactivity_seconds=_number(limits.get("inactivity_seconds"), 30 * 60),
        execution_seconds=_number(limits.get("execution_seconds"), 6 * 60 * 60),
        total_seconds=_number(limits.get("total_seconds"), 12 * 60 * 60),
    )


def _source_versions(sources: tuple[str, ...]) -> dict[str, float]:
    return {source: _mtime(source) for source in sources}


def _saved_source_versions(value: object) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    saved = cast(Mapping[object, object], value)
    result: dict[str, float] = {}
    for source, version in saved.items():
        if (
            not isinstance(source, str)
            or isinstance(version, bool)
            or not isinstance(version, int | float)
        ):
            return None
        result[source] = float(version)
    return result


def _progress_changed(
    sources: tuple[str, ...], prior: Mapping[str, float]
) -> tuple[bool, dict[str, float]]:
    current = _source_versions(sources)
    return current != prior, current


def _mtime(source: str) -> float:
    try:
        return Path(source).stat().st_mtime_ns / 1_000_000_000
    except OSError:
        return -1.0


def _artifact_root(plan: ExecutionPlan, fallback: str) -> str:
    artifact = plan.ownership_hints.get("artifact_path")
    return artifact if isinstance(artifact, str) and artifact.strip() else fallback


def _load_result(evidence_path: str) -> ResultRead:
    path = Path(evidence_path) / "result.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ResultRead(None)
    except OSError as error:
        return ResultRead(None, f"cannot read {path}: {error}")
    except json.JSONDecodeError as error:
        return ResultRead(None, f"invalid JSON in {path}: {error.msg}")
    if not isinstance(parsed, dict):
        return ResultRead(None, f"invalid JSON object in {path}")
    return ResultRead(cast(dict[str, object], parsed))


def _result_status(result: Mapping[str, object]) -> str:
    """Keep suite execution disposition independent from its product verdict."""
    disposition = result.get("evaluation_status")
    if disposition in {"failed", "cancelled", "interrupted"}:
        return cast(str, disposition)
    return "completed"


def _process_identity(pid: int, plan: ExecutionPlan) -> dict[str, object] | None:
    start = _process_start(pid)
    if start is None:
        return None
    return {
        "pid": pid,
        "start": start,
        "argv": list(plan.argv),
        "artifact_path": plan.ownership_hints.get("artifact_path", ""),
    }


def _supervisor_identity() -> dict[str, object]:
    try:
        start = _process_start(os.getpid())
    except ProcessProbeError:
        start = None
    return {"pid": os.getpid(), "start": start or "unknown"}


def _process_start(pid: int) -> str | None:
    try:
        completed = subprocess.run(
            ["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise ProcessProbeError(str(error)) from error
    value = completed.stdout.strip()
    if completed.returncode == 1:
        return None
    if completed.returncode != 0:
        raise ProcessProbeError(completed.stderr.strip() or "ps probe failed")
    if not value:
        raise ProcessProbeError("ps returned no process state")
    state, _, started = value.partition(" ")
    return started.strip() if "Z" not in state and started.strip() else None


def process_start_identity(pid: int) -> str | None:
    """Expose a process start identity for integration-boundary verification."""
    return _process_start(pid)


def _identity_status(identity: Mapping[str, object]) -> str:
    pid = identity.get("pid")
    start = identity.get("start")
    if not isinstance(pid, int) or not isinstance(start, str):
        return "missing"
    if start == "unknown":
        return "unknown"
    try:
        current = _process_start(pid)
    except ProcessProbeError:
        return "unknown"
    return "alive" if current == start else "missing"


def _terminate(identity: Mapping[str, object]) -> bool:
    if _identity_status(identity) == "missing":
        return True
    if _identity_status(identity) != "alive":
        return False
    pid = cast(int, identity["pid"])
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + 1
    while _identity_status(identity) == "alive" and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
    if _identity_status(identity) == "missing":
        return True
    if _identity_status(identity) != "alive":
        return False
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return True


@contextmanager
def _run_lock(state_path: Path, run_id: str):
    import fcntl

    path = state_path.parent / "locks" / f"run-{run_id}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _required_run(store: ClaimStore, run_id: str) -> Run:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    return run


def _number(value: object, default: float) -> float:
    return (
        float(value) if isinstance(value, int | float) and not isinstance(value, bool) else default
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    values = cast(list[object], value)
    return [item for item in values if isinstance(item, str)]


def _string_map(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    values = cast(Mapping[object, object], value)
    return {str(key): item for key, item in values.items() if isinstance(item, str)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observe one independently running factory attempt"
    )
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--nonce", required=True)
    arguments = parser.parse_args()
    supervise(arguments.state, arguments.run_id, arguments.nonce)


if __name__ == "__main__":
    main()
