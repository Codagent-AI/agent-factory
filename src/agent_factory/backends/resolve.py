"""Resolve durable and legacy execution plans to their owning mechanism."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from agent_factory.controller import ExecutionPlan

if TYPE_CHECKING:
    from agent_factory.backends import ExecutionBackend


def plan_hints(plan: object) -> Mapping[str, object]:
    """The ownership hints of a durable or legacy plan, or an empty mapping."""
    value: object = None
    if isinstance(plan, ExecutionPlan):
        value = plan.ownership_hints
    elif isinstance(plan, Mapping):
        value = cast(Mapping[str, object], plan).get("ownership_hints")
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def backend_name(hints: Mapping[str, object], *, argv: Sequence[str] = ()) -> str | None:
    explicit = hints.get("backend")
    if explicit is not None:
        return (
            explicit
            if isinstance(explicit, str) and explicit in {"host", "docker", "fly-machine"}
            else None
        )
    sandbox = hints.get("sandbox")
    suite = hints.get("suite")
    if sandbox == "host":
        return None if suite is not None else "host"
    if sandbox == "docker":
        return None if "runner_executable" in hints else "docker"
    if sandbox is not None:
        return None
    if suite == "and-scene":
        return "docker" if not argv or Path(argv[0]).name == "run.sh" else None
    if suite is not None:
        return None
    return "host" if set(hints) == {"artifact_path"} else None


def backend_for(plan: object) -> ExecutionBackend | None:
    hints: object = None
    argv: object = ()
    if isinstance(plan, ExecutionPlan):
        hints, argv = plan.ownership_hints, plan.argv
    elif isinstance(plan, Mapping):
        values = cast(Mapping[str, object], plan)
        hints, argv = values.get("ownership_hints"), values.get("argv", ())
    if not isinstance(hints, Mapping):
        return None
    name = backend_name(
        cast(Mapping[str, object], hints),
        argv=cast(Sequence[str], argv) if isinstance(argv, (tuple, list)) else (),
    )
    if name == "host":
        from agent_factory.backends.host import HostProcessBackend

        return HostProcessBackend()
    if name == "docker":
        from agent_factory.backends.docker import DockerContainerBackend

        return DockerContainerBackend()
    if name == "fly-machine":
        from agent_factory.fly.backend import FlyMachineBackend

        return FlyMachineBackend()
    return None


def adoption_action(
    state: str, *, result_present: bool
) -> Literal["observe", "finish-result", "interrupt", "hold"]:
    if state == "alive":
        return "observe"
    if state == "gone":
        return "finish-result" if result_present else "interrupt"
    return "hold"
