"""Docker container ownership and lifecycle operations."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from agent_factory.backends import Disposal, Probe
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.operations import Diagnostic
from agent_factory.store import Run
from agent_factory.supervisor import (  # pyright: ignore[reportPrivateUsage]
    ProcessProbeError,
)
from agent_factory.supervisor import (
    process_identity_status as _identity_status,
)
from agent_factory.supervisor import (
    terminate_owned_process as _terminate,
)


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


class DockerContainerBackend:
    name = "docker"
    supports_attach = False

    def discover(self, artifact: str) -> Mapping[str, object] | None:
        return discover_container(artifact)

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        from agent_factory.operations import (
            command_check,
            docker_reclaimable_diagnostic,
        )

        docker = command_check(
            "Docker",
            ("docker", "info"),
            "Start Docker Desktop, then rerun doctor.",
            timeout=30,
            group="eval-sandbox",
        )
        checks: list[Diagnostic] = [
            docker,
            docker_reclaimable_diagnostic(docker_available=docker.available),
        ]
        return checks

    def memory_readiness(self, reservation_gib: int) -> Diagnostic:
        from agent_factory.operations import check_memory_headroom

        return check_memory_headroom(reservation_gib)

    def identity_from_plan(self, plan: object, run: object) -> Mapping[str, object] | None:
        hints: Mapping[str, object] | None = None
        if isinstance(plan, ExecutionPlan):
            hints = plan.ownership_hints
        elif isinstance(plan, Mapping):
            value = cast(Mapping[str, object], plan).get("ownership_hints")
            hints = cast(Mapping[str, object], value) if isinstance(value, Mapping) else None
        process = run.process if isinstance(run, Run) else {}
        progress = run.progress if isinstance(run, Run) else {}
        return {
            "launcher": process,
            "container": progress.get("container"),
            "artifact_path": hints.get("artifact_path") if isinstance(hints, Mapping) else None,
        }

    def probe(self, identity: Mapping[str, object]) -> Probe:
        launcher = identity.get("launcher")
        status = _identity_status(
            cast(Mapping[str, object], launcher) if isinstance(launcher, Mapping) else {}
        )
        if status == "unknown":
            return Probe("unknown", "launcher identity cannot be probed")
        container = identity.get("container")
        if not isinstance(container, Mapping):
            if status == "alive":
                return Probe("alive")
            artifact = identity.get("artifact_path")
            if not isinstance(artifact, str) or not artifact:
                return Probe("gone")
            try:
                container = discover_container(artifact)
            except (OSError, subprocess.TimeoutExpired, ProcessProbeError) as error:
                return Probe("unknown", str(error))
            if container is None:
                return Probe("gone")
        try:
            recorded = cast(Mapping[str, object], container)
            observed = inspect_container(str(recorded.get("id", "")))
            if observed is None:
                artifact = recorded.get("artifact_path")
                if isinstance(artifact, str) and artifact:
                    replacement = discover_container(artifact)
                    if replacement is not None:
                        return Probe(
                            "mismatch"
                            if replacement.get("id") != recorded.get("id")
                            else "unknown",
                            "recorded container identity changed during inspection",
                        )
                return Probe("alive" if status == "alive" else "gone")
            if not container_matches_recorded_ownership(recorded, observed):
                return Probe("mismatch", "recorded container ownership changed")
            state = observed.get("State")
            running = (
                isinstance(state, Mapping)
                and cast(Mapping[str, object], state).get("Running") is True
            )
            return Probe("alive" if status == "alive" or running else "gone")
        except (OSError, subprocess.TimeoutExpired, ProcessProbeError) as error:
            return Probe("unknown", str(error))

    def adopt(self, plan: object, run: object, store: object) -> Probe:
        return self.probe(self.identity_from_plan(plan, run) or {})

    def terminate(self, identity: Mapping[str, object]) -> bool:
        launcher = identity.get("launcher")
        return _terminate_execution(
            cast(Mapping[str, object], launcher) if isinstance(launcher, Mapping) else {},
            identity.get("container"),
        )

    def dispose(
        self, identity: Mapping[str, object], decision: Disposal, store: object | None = None
    ) -> None:
        pass

    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]:
        raise NotImplementedError("Docker launchers cannot be reattached")

    def reconcile(self, store: object) -> list[str]:
        return []

    def provenance(self, identity: Mapping[str, object]) -> Mapping[str, object]:
        container = identity.get("container")
        return (
            {"container": dict(cast(Mapping[str, object], container))}
            if isinstance(container, Mapping)
            else {}
        )
