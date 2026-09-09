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
from agent_factory.store import ClaimStore, Run

_POLL_SECONDS = 0.05


@dataclass(frozen=True)
class SupervisionLimits:
    inactivity_seconds: float = 30 * 60
    execution_seconds: float = 6 * 60 * 60
    total_seconds: float = 12 * 60 * 60


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
        log_dir = state_path.parent / "logs" / "runs" / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        arguments = [sys.executable, "-m", "agent_factory.supervisor", "--state", str(state_path)]
        arguments.extend(("--run-id", run_id, "--nonce", run.launch_nonce))
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
    finally:
        store.close()


def resume_supervisor(state_path: Path, run_id: str) -> subprocess.Popen[bytes] | None:
    """Attach a fresh watcher to a nonterminal durable run without relaunching it."""
    store = ClaimStore(state_path)
    try:
        run = store.get_run(run_id)
        if run is None or run.status not in {"reserved", "running", "observing"}:
            return None
        log_dir = state_path.parent / "logs" / "runs" / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "supervisor.log").open("ab", buffering=0) as output:
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "agent_factory.supervisor",
                    "--state",
                    str(state_path),
                    "--run-id",
                    run_id,
                    "--nonce",
                    run.launch_nonce,
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    finally:
        store.close()


def supervise(state_path: Path, run_id: str, nonce: str) -> None:
    """Observe or launch exactly one suite process under the per-run lock."""
    with _run_lock(state_path, run_id):
        store = ClaimStore(state_path)
        try:
            run = _required_run(store, run_id)
            if run.launch_nonce != nonce:
                return
            if run.status not in {"reserved", "running", "observing"}:
                return
            plan = _plan_from_document(run.plan)
            limits = _limits_from_document(run.plan)
            existing = run.process
            if existing and _process_matches(existing):
                watcher = _supervisor_identity()
                watcher["process"] = existing
                store.update_supervisor(run.id, watcher)
                _observe(store, run.id, plan, limits, existing)
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
    environment = {str(key): str(value) for key, value in plan.allowed_environment.items()}
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
    identity = _process_identity(child.pid, plan)
    if identity is None or not store.begin_run(
        run.id,
        launch_nonce=run.launch_nonce,
        supervisor=_supervisor_identity(),
        process=identity,
    ):
        # A stale watcher must not leave an untracked child behind.
        if identity is not None and _process_matches(identity):
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
    progress = dict(run.progress)
    started = _number(progress.get("started_at"), time.time())
    last_progress = _number(progress.get("last_progress_at"), started)
    observed_sources = _source_versions(plan.progress_sources)
    if not progress:
        progress = {
            "started_at": started,
            "last_progress_at": last_progress,
            "sources": observed_sources,
        }
        store.update_progress(run_id, progress)
    while True:
        run = _required_run(store, run_id)
        result = _load_result(plan.ownership_hints.get("artifact_path", run.evidence_path))
        now = time.time()
        changed, observed_sources = _progress_changed(plan.progress_sources, observed_sources)
        if changed:
            last_progress = now
        progress.update(
            {"started_at": started, "last_progress_at": last_progress, "sources": observed_sources}
        )
        store.update_progress(run_id, progress)
        if result is not None:
            store.finish_run(run_id, execution_status=_result_status(result), result=result)
            return
        if not _process_matches(identity):
            store.finish_run(
                run_id,
                execution_status="interrupted",
                result={"reason": "owned process exited without durable result"},
            )
            return
        if run.cancellation_requested:
            if _terminate(identity):
                store.finish_run(
                    run_id, execution_status="cancelled", result={"reason": "cancelled"}
                )
            else:
                store.report_uncertainty(run_id, "cancellation ownership could not be verified")
            return
        timeout = _timeout(now, started, last_progress, limits)
        if timeout is not None:
            if _terminate(identity):
                store.finish_run(run_id, execution_status="timed_out", result={"timeout": timeout})
            else:
                store.report_uncertainty(
                    run_id, f"{timeout} timeout ownership could not be verified"
                )
            return
        time.sleep(_POLL_SECONDS)


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


def _load_result(evidence_path: str) -> dict[str, object] | None:
    path = Path(evidence_path) / "result.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None


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
    start = _process_start(os.getpid())
    return {"pid": os.getpid(), "start": start or "unknown"}


def _process_start(pid: int) -> str | None:
    completed = subprocess.run(
        ["ps", "-o", "stat=", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        return None
    state, _, started = value.partition(" ")
    return started.strip() if "Z" not in state and started.strip() else None


def _process_matches(identity: Mapping[str, object]) -> bool:
    pid = identity.get("pid")
    start = identity.get("start")
    return isinstance(pid, int) and isinstance(start, str) and _process_start(pid) == start


def _terminate(identity: Mapping[str, object]) -> bool:
    if not _process_matches(identity):
        return False
    pid = cast(int, identity["pid"])
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + 1
    while _process_matches(identity) and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
    if not _process_matches(identity):
        return True
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
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
