from __future__ import annotations

from pytest import MonkeyPatch

from agent_factory.backends import docker


def test_probe_rejects_changed_recorded_container_even_with_a_live_launcher(
    monkeypatch: MonkeyPatch,
) -> None:
    def alive(_identity: object) -> str:
        return "alive"

    def changed(_container_id: str) -> dict[str, object]:
        return {"Id": "owned", "Image": "different", "Mounts": []}

    monkeypatch.setattr(docker, "_identity_status", alive)
    monkeypatch.setattr(
        docker,
        "inspect_container",
        changed,
    )
    identity: dict[str, object] = {
        "launcher": {"pid": 123, "start": "now"},
        "container": {"id": "owned", "image": "expected", "artifact_path": "/tmp/a"},
    }

    assert docker.DockerContainerBackend().probe(identity).state == "mismatch"


def test_probe_holds_when_a_different_container_reuses_the_artifact_mount(
    monkeypatch: MonkeyPatch,
) -> None:
    def gone(_identity: object) -> str:
        return "missing"

    def missing(_container_id: str) -> None:
        return None

    def replacement(_artifact: str) -> dict[str, object]:
        return {"id": "new", "image": "other", "artifact_path": "/tmp/a"}

    monkeypatch.setattr(docker, "_identity_status", gone)
    monkeypatch.setattr(docker, "inspect_container", missing)
    monkeypatch.setattr(docker, "discover_container", replacement)
    identity: dict[str, object] = {
        "launcher": {},
        "container": {"id": "old", "image": "expected", "artifact_path": "/tmp/a"},
    }

    assert docker.DockerContainerBackend().probe(identity).state == "mismatch"
