"""Independent, durable supervision for a single reserved factory run.

The controller only reserves and starts this module.  Once started, this
process and the suite process both have their own sessions and all state needed
for replacement observation is in SQLite rather than a controller pipe.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent_factory.backends import Probe
from agent_factory.controller import ExecutionPlan
from agent_factory.fly.transport import (
    EXIT_COLLECTION_FAILED,
    EXIT_MACHINE_LOST,
    EXIT_MISMATCH,
    EXIT_TRANSPORT,
)
from agent_factory.store import NONTERMINAL_RUN_STATUSES, ClaimStore, Run
from agent_factory.suites.and_scene import ReadinessError, bounded_quota_deadline

if TYPE_CHECKING:
    from agent_factory.fly.backend import FlyMachineBackend

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
            if plan.ownership_hints.get("backend") == "fly-machine":
                _supervise_fly(store, run, plan, limits)
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


# USER and LOGNAME carry the login identity: Claude Code keys its macOS Keychain
# login by $USER and silently uses a separate, stale "unknown" entry without it.
_INHERITED_ENVIRONMENT = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL")


def _launch_and_observe(
    store: ClaimStore, run: Run, plan: ExecutionPlan, limits: SupervisionLimits
) -> None:
    output = Path(run.evidence_path) / "factory-suite.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = {key: os.environ[key] for key in _INHERITED_ENVIRONMENT if key in os.environ}
    environment.update({str(key): str(value) for key, value in plan.allowed_environment.items()})
    try:
        with output.open("ab", buffering=0) as stream:
            argv = list(plan.argv)
            if plan.ownership_hints.get("backend") == "fly-machine":
                argv = _recording_exit_code(argv, run.evidence_path)
            child = subprocess.Popen(
                argv,
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
    if identity is None:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            store.report_uncertainty(run.id, "new execution exit cannot be confirmed")
            return
        # Reconcile durable results and any surviving suite container through the
        # same observation path. A reaped child has no live process identity.
        identity = {}
    if not store.begin_run(
        run.id,
        launch_nonce=run.launch_nonce,
        supervisor=_supervisor_identity(),
        process=identity,
    ):
        # A stale watcher must not leave an untracked child behind.
        if _identity_status(identity) == "alive":
            _terminate(identity)
        return
    if plan.ownership_hints.get("backend") == "fly-machine":
        _supervise_fly(store, _required_run(store, run.id), plan, limits)
    else:
        _observe(store, run.id, plan, limits, identity)


_IDENTITY_WAIT_SECONDS = 300
_FLY_PROBE_SECONDS = 5


def _supervise_fly(
    store: ClaimStore, run: Run, plan: ExecutionPlan, limits: SupervisionLimits
) -> None:
    """Supervise a durable Machine while treating the local launcher as disposable."""
    from agent_factory.fly.backend import FlyMachineBackend

    backend = FlyMachineBackend()
    if run.status == "reserved":
        # A reserved attempt always goes through the launcher, which alone decides
        # whether to create a Machine or resume in the repetition's surviving one.
        # A record left by an earlier attempt is not this attempt's identity.
        _launch_and_observe(store, run, plan, limits)
        return
    identity = backend.identity_from_plan(plan, run)
    if identity is None:
        if _identity_status(run.process) == "alive":
            # The launcher names this attempt in the record only once the Machine
            # carries its identity, which for a stopped Machine includes a config
            # update and a boot. Absence during that window is normal.
            deadline = time.monotonic() + min(_IDENTITY_WAIT_SECONDS, limits.inactivity_seconds)
            while time.monotonic() < deadline and _identity_status(run.process) == "alive":
                time.sleep(_POLL_SECONDS)
                identity = backend.identity_from_plan(plan, _required_run(store, run.id))
                if identity is not None:
                    _supervise_fly(store, _required_run(store, run.id), plan, limits)
                    return
            store.report_uncertainty(run.id, "Fly Machine identity was not recorded by launcher")
        else:
            _finish_fly_launcher_exit(store, run, plan)
        return
    progress = dict(run.progress)
    probe = backend.probe(identity)
    progress["machine"] = _machine_progress(identity, probe.state)
    _copy_fly_heartbeat(plan, progress)
    progress["machine_provenance"] = dict(backend.provenance(identity))
    store.update_progress(run.id, progress)
    if probe.state == "gone":
        mismatch = store.get_setting("runtime", "fly:mismatch")
        if (
            mismatch
            and mismatch.get("run_id") == run.id
            and mismatch.get("machine_id") == identity.get("id")
        ):
            store.compare_and_set_setting("runtime", "fly:mismatch", mismatch, {})
        store.finish_run(run.id, execution_status="interrupted", result={"reason": "machine lost"})
        return
    if probe.state == "mismatch":
        store.set_setting(
            "runtime",
            "fly:mismatch",
            {
                "machine_id": identity.get("id"),
                "run_id": run.id,
                "expected": identity.get("expected_metadata"),
                "observed": probe.detail,
                "remedy": "destroy the Machine by hand or wait for its deadline",
            },
        )
        store.report_uncertainty(run.id, "Fly Machine ownership metadata does not match")
        return
    if probe.state == "unknown":
        store.report_uncertainty(run.id, f"Fly Machine ownership is unknown: {probe.detail}")
        return
    if probe.state == "stopped":
        if _number(progress.get("quota_until"), 0) > time.time():
            return
        store.finish_run(
            run.id, execution_status="interrupted", result={"reason": "Fly Machine stopped"}
        )
        return
    launcher = run.process
    if _identity_status(launcher) != "alive":
        try:
            launcher = _spawn_plan_process(
                plan,
                tuple(_recording_exit_code(backend.attach_argv(plan, run), run.evidence_path)),
                run.evidence_path,
            )
        except OSError as error:
            store.report_uncertainty(run.id, f"Fly Machine attach failed: {error}")
            return
        supervisor = _supervisor_identity()
        supervisor["process"] = launcher
        store.update_supervisor(run.id, supervisor)
    _observe_fly(store, run.id, plan, limits, identity, backend, launcher)


def _recording_exit_code(argv: Sequence[str], evidence_path: str) -> list[str]:
    """Wrap a launcher so its exit code outlives it, for fresh launch and attach alike."""
    status_file = Path(evidence_path) / ".factory" / "launcher-exit-code"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    # The artifact directory is shared by a repetition's attempts; a code left by
    # an earlier launcher must never be read as this one's.
    status_file.unlink(missing_ok=True)
    status_path = shlex.quote(str(status_file))
    return [
        "/bin/sh",
        "-c",
        f"{shlex.join(argv)}; status=$?; "
        f'printf \'%s\\n\' "$status" > {status_path}; exit "$status"',
    ]


def _finish_fly_launcher_exit(store: ClaimStore, run: Run, plan: ExecutionPlan) -> None:
    code = _launcher_exit_code(plan, run.evidence_path)
    if code in {EXIT_MACHINE_LOST, EXIT_COLLECTION_FAILED}:
        store.finish_run(run.id, execution_status="interrupted", result={"reason": "machine lost"})
    elif code == EXIT_MISMATCH:
        store.report_uncertainty(run.id, "Fly launcher reported ownership mismatch")
    elif code == EXIT_TRANSPORT:
        store.finish_run(
            run.id, execution_status="failed", result={"reason": "Fly launcher failed"}
        )
    else:
        store.finish_run(
            run.id, execution_status="interrupted", result={"reason": "Fly launcher exited"}
        )


def _launcher_exit_code(plan: ExecutionPlan, evidence_path: str) -> int | None:
    path = Path(_artifact_root(plan, evidence_path)) / ".factory" / "launcher-exit-code"
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _spawn_plan_process(
    plan: ExecutionPlan, argv: tuple[str, ...], evidence_path: str
) -> Mapping[str, object]:
    output = Path(evidence_path) / "factory-suite.log"
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = {key: os.environ[key] for key in _INHERITED_ENVIRONMENT if key in os.environ}
    environment.update(plan.allowed_environment)
    with output.open("ab", buffering=0) as stream:
        child = subprocess.Popen(
            list(argv),
            cwd=plan.working_directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return _process_identity(child.pid, plan) or {}


def _machine_progress(identity: Mapping[str, object], state: str) -> dict[str, object]:
    """What status shows for the attempt: the recorded identity, its state, and deadline."""
    values = {key: value for key, value in identity.items() if key != "token_file"}
    values["state"] = state
    # The launcher records the deadline it set; status names it as the Machine does.
    values.setdefault("deadline_epoch", identity.get("deadline"))
    return values


def _copy_fly_heartbeat(plan: ExecutionPlan, progress: dict[str, object]) -> None:
    artifact = _artifact_root(plan, "")
    path = Path(artifact) / ".factory" / "heartbeat.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if (
        isinstance(data, Mapping)
        and cast(Mapping[str, object], data).get("checkpoint_seen") is True
    ):
        progress["checkpoint_seen"] = True


def _observe_fly(
    store: ClaimStore,
    run_id: str,
    plan: ExecutionPlan,
    limits: SupervisionLimits,
    identity: Mapping[str, object],
    machine_backend: FlyMachineBackend,
    launcher: Mapping[str, object],
) -> None:
    run = _required_run(store, run_id)
    progress = dict(run.progress)
    wall_anchor = time.time()
    monotonic_anchor = time.monotonic()
    started = _number(progress.get("started_at"), wall_anchor)
    last_progress = _number(progress.get("last_progress_at"), started)
    sources = _saved_source_versions(progress.get("sources")) or _source_versions(
        plan.progress_sources
    )
    if "elapsed_seconds" in progress:
        gap = max(0.0, wall_anchor - _number(progress.get("persisted_at"), wall_anchor))
        started = wall_anchor - _number(progress.get("elapsed_seconds"), 0) - gap
        last_progress = wall_anchor - _number(progress.get("idle_seconds"), 0) - gap
    state: Probe | None = None
    last_probe = float("-inf")
    while True:
        run = _required_run(store, run_id)
        progress = dict(run.progress)
        now = wall_anchor + (time.monotonic() - monotonic_anchor)
        _copy_fly_heartbeat(plan, progress)
        changed, sources = _progress_changed(plan.progress_sources, sources)
        if changed:
            last_progress = now
        result = _load_result(_artifact_root(plan, run.evidence_path))
        launcher_missing = _identity_status(launcher) == "missing"
        # A probe is a REST round trip; like the container probe below it runs on
        # an interval, and at once when the launcher has gone away.
        if state is None or now - last_probe >= _FLY_PROBE_SECONDS or launcher_missing:
            state = machine_backend.probe(identity)
            last_probe = now
        progress.update(
            {
                "started_at": started,
                "last_progress_at": last_progress,
                "sources": sources,
                "persisted_at": time.time(),
                "elapsed_seconds": now - started,
                "idle_seconds": now - last_progress,
                "machine": _machine_progress(identity, state.state),
            }
        )
        store.update_progress(run_id, progress)
        if result.result is not None and (launcher_missing or state.state == "gone"):
            store.finish_run(
                run_id, execution_status=_result_status(result.result), result=result.result
            )
            return
        if state.state == "gone":
            store.finish_run(
                run_id, execution_status="interrupted", result={"reason": "machine lost"}
            )
            return
        if state.state == "stopped":
            if result.result is not None:
                store.finish_run(
                    run_id, execution_status=_result_status(result.result), result=result.result
                )
            elif _number(progress.get("quota_until"), 0) <= now:
                store.finish_run(
                    run_id,
                    execution_status="interrupted",
                    result={"reason": "Fly Machine stopped"},
                )
            return
        if state.state in {"mismatch", "unknown"}:
            _supervise_fly(store, run, plan, limits)
            return
        if launcher_missing and result.result is None:
            _finish_fly_launcher_exit(store, run, plan)
            return
        timeout = _timeout(now, started, last_progress, limits)
        cancelling = run.cancellation_requested
        if cancelling or timeout is not None:
            if not machine_backend.terminate(identity):
                store.report_uncertainty(
                    run_id, "Fly Machine termination ownership could not be verified"
                )
                return
            deadline = time.monotonic() + _collection_grace(plan)
            while _identity_status(launcher) == "alive" and time.monotonic() < deadline:
                time.sleep(_POLL_SECONDS)
            if _identity_status(launcher) == "alive":
                _terminate(launcher)
                result_value: dict[str, object] = {"collection": "failed", "reason": "machine lost"}
            else:
                collected = _load_result(_artifact_root(plan, run.evidence_path)).result
                result_value = (
                    dict(collected)
                    if collected is not None
                    else ({"reason": "cancelled"} if cancelling else {"timeout": timeout})
                )
            if cancelling:
                machine_backend.dispose(identity, "destroy")
                store.finish_run(run_id, execution_status="cancelled", result=result_value)
            else:
                store.finish_run(run_id, execution_status="timed_out", result=result_value)
            return
        time.sleep(_POLL_SECONDS)


def _collection_grace(plan: ExecutionPlan) -> float:
    manifest = Path(_artifact_root(plan, "")) / ".factory" / "manifest.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        document: Mapping[str, object] = (
            cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}
        )
        deadline = document.get("deadline")
        if isinstance(deadline, Mapping):
            return _number(
                cast(Mapping[str, object], deadline).get("collection_grace_seconds"), 900
            )
    except (OSError, json.JSONDecodeError):
        pass
    return 900


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
    paused_seconds = _number(progress.get("quota_wait_seconds"), 0)
    quota_until = _number(progress.get("quota_until"), 0)
    if "elapsed_seconds" in progress:
        gap = max(0.0, wall_anchor - _number(progress.get("persisted_at"), wall_anchor))
        started = wall_anchor - _number(progress.get("elapsed_seconds"), 0) - gap
        last_progress = wall_anchor - _number(progress.get("idle_seconds"), 0) - gap
        wait_gap = _wait_overlap(wall_anchor - gap, wall_anchor, quota_until)
        paused_seconds += wait_gap
        last_progress += wait_gap
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
        image_tag = plan.ownership_hints.get("image_tag")
        if isinstance(image_tag, str) and image_tag:
            progress["image_tag"] = image_tag
        store.update_progress(run_id, progress)
        last_persisted = _number(progress["persisted_at"], 0)
    last_container_probe = float("-inf")
    previous_now = wall_anchor
    quota_observed = False
    while True:
        run = _required_run(store, run_id)
        # The wall clock is used only to anchor persisted timestamps on attachment.
        # All decisions during this watcher lifetime advance by monotonic elapsed time.
        now = wall_anchor + (time.monotonic() - monotonic_anchor)
        result_read = _load_result(_artifact_root(plan, run.evidence_path))
        process_status = _identity_status(identity)
        if _needs_container_discovery(plan, identity) and (
            now - last_container_probe >= 5 or process_status == "missing"
        ):
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
        wait_elapsed = _wait_overlap(previous_now, now, quota_until)
        paused_seconds += wait_elapsed
        last_progress += wait_elapsed
        previous_now = now
        previous_quota = quota_until
        changed, observed_sources = _progress_changed(plan.progress_sources, observed_sources)
        if plan.ownership_hints.get("suite") == "and-scene" and (changed or not quota_observed):
            try:
                quota_until = (
                    bounded_quota_deadline(Path(_artifact_root(plan, run.evidence_path)), now=now)
                    or 0
                )
            except ReadinessError as error:
                store.report_uncertainty(run_id, str(error))
                return
            quota_observed = True
        if quota_until <= now:
            quota_until = 0
        progress["quota_until"] = quota_until
        progress["quota_wait_seconds"] = paused_seconds
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
        if (
            changed
            or diagnostic_changed
            or quota_until != previous_quota
            or now - last_persisted >= _PROGRESS_HEARTBEAT_SECONDS
        ):
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
        timeout = _timeout(now, started, last_progress, limits, paused_seconds=paused_seconds)
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
    entries = _inspection_entries(result.stdout)
    if len(entries) != 1:
        raise ProcessProbeError("invalid Docker inspection")
    return entries[0]


def _inspection_entries(output: str) -> list[dict[str, object]]:
    try:
        values: object = json.loads(output)
    except json.JSONDecodeError as error:
        raise ProcessProbeError("invalid Docker inspection") from error
    if not isinstance(values, list):
        raise ProcessProbeError("invalid Docker inspection")
    entries = cast(list[object], values)
    if not all(isinstance(entry, dict) for entry in entries):
        raise ProcessProbeError("invalid Docker inspection")
    return [cast(dict[str, object], entry) for entry in entries]


def _discovers_container(plan: ExecutionPlan) -> bool:
    """Plans that run inside the Docker sandbox are owned through their container too.

    A host-mode plan (``sandbox == "host"``) is owned through its process identity alone:
    no container is discovered, inspected, or stopped for it, and termination is the
    process-group kill that also covers the Runner's agent children.
    """
    hints = plan.ownership_hints
    if hints.get("sandbox") == "host":
        return False
    return hints.get("suite") == "and-scene" or hints.get("sandbox") == "docker"


def _needs_container_discovery(plan: ExecutionPlan, identity: Mapping[str, object]) -> bool:
    """Avoid probing Docker for an explicitly launched local replacement process.

    A processless observation still needs the conservative discovery path: the wrapper
    may already have exited after starting its owned container.
    """
    if not _discovers_container(plan):
        return False
    # A Docker sandbox launcher always owns a container; only an and-scene wrapper can be
    # swapped for a local process.
    if plan.ownership_hints.get("sandbox") == "docker":
        return True
    return not plan.argv or Path(plan.argv[0]).name == "run.sh" or not identity


def discover_container(artifact: str) -> dict[str, object] | None:
    result = subprocess.run(
        ["docker", "ps", "--no-trunc", "-q", "--filter", "volume=/artifacts"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ProcessProbeError("Docker ownership discovery unavailable")
    identifiers = result.stdout.splitlines()
    if not identifiers:
        return None
    inspection = subprocess.run(
        ["docker", "inspect", *identifiers],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if inspection.returncode != 0:
        raise ProcessProbeError("Docker ownership inspection unavailable")
    matches: list[dict[str, object]] = []
    for observed in _inspection_entries(inspection.stdout):
        container_id = observed.get("Id")
        if container_id not in identifiers:
            raise ProcessProbeError("unexpected Docker inspection identity")
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


def _wait_overlap(previous: float, now: float, deadline: float) -> float:
    return max(0.0, min(now, deadline) - previous)


def _timeout(
    now: float,
    started: float,
    last_progress: float,
    limits: SupervisionLimits,
    *,
    paused_seconds: float = 0,
) -> str | None:
    if now - started >= limits.total_seconds:
        return "total"
    if now - started - paused_seconds >= limits.execution_seconds:
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
    return {source: _source_mtime(source) for source in sources}


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


def _source_mtime(source: str) -> float:
    if source.startswith("glob:"):
        matches = (_mtime(path) for path in glob.iglob(source.removeprefix("glob:")))
        return max(matches, default=-1.0)
    return _mtime(source)


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
