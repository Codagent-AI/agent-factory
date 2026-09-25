from __future__ import annotations

import pytest

from agent_factory.backends.resolve import backend_for, backend_name
from agent_factory.controller import ExecutionPlan


@pytest.mark.parametrize(
    ("hints", "argv", "expected"),
    [
        ({"backend": "fly-machine", "suite": "and-scene"}, (), "fly-machine"),
        ({"backend": "host", "sandbox": "host"}, (), "host"),
        ({"backend": "docker", "sandbox": "docker"}, (), "docker"),
        ({"sandbox": "host", "artifact_path": "/tmp/a"}, (), "host"),
        ({"sandbox": "docker", "artifact_path": "/tmp/a"}, (), "docker"),
        ({"suite": "and-scene"}, (), "docker"),
        ({"suite": "and-scene"}, ("/tmp/run.sh",), "docker"),
        ({"artifact_path": "/tmp/a"}, ("python", "x.py"), "host"),
        ({}, (), None),
        ({"suite": "other"}, (), None),
        ({"backend": "unknown"}, (), None),
        ({"sandbox": "host", "suite": "and-scene"}, (), None),
        ({"sandbox": "docker", "runner_executable": "/tmp/run"}, (), None),
        ({"suite": "and-scene"}, ("python", "x.py"), None),
    ],
)
def test_legacy_backend_resolution(
    hints: dict[str, str], argv: tuple[str, ...], expected: str | None
) -> None:
    assert backend_name(hints, argv=argv) == expected


def test_resolved_plan_constructs_the_owning_backend() -> None:
    for name in ("host", "docker", "fly-machine"):
        plan = ExecutionPlan(("/bin/true",), "/tmp", {}, (), (), {"backend": name}, False)
        backend = backend_for(plan)
        assert backend is not None and backend.name == name
