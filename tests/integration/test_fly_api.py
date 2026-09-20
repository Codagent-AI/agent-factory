"""INT-002: the Machines API client against the fake REST server.

The create body shape, registry basic auth, 429 retry, and ``auto_destroy`` cases
live in ``test_fly_readiness.py``; this file covers the remaining obligations.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from agent_factory.fly.api import FlyApiError, FlyMachinesClient
from tests.fixtures.fly.api import FakeMachinesApi

MARKER = {"factory-owner": "agent-factory"}


class Harness:
    def __init__(self, api: FakeMachinesApi, token: Path) -> None:
        self.api = api
        self.client = FlyMachinesClient("app", token, base_url=api.base_url)

    def machine(
        self,
        machine_id: str,
        *,
        state: str = "started",
        metadata: dict[str, str] | None = None,
        config: dict[str, object] | None = None,
    ) -> dict[str, object]:
        values: dict[str, object] = {"id": machine_id, "state": state, "config": dict(config or {})}
        if metadata is not None:
            cast(dict[str, object], values["config"])["metadata"] = dict(metadata)
        self.api.machines[machine_id] = values
        return values

    def metadata(self, machine_id: str) -> dict[str, str]:
        config = cast(dict[str, object], self.api.machines[machine_id]["config"])
        return cast(dict[str, str], config["metadata"])

    def requests(self, method: str) -> list[dict[str, object]]:
        return [r for r in self.api.requests if r["method"] == method]


@pytest.fixture
def fly(tmp_path: Path) -> Iterator[Harness]:
    token = tmp_path / "token"
    token.write_text("deploy-token\n", encoding="utf-8")
    token.chmod(0o600)
    with FakeMachinesApi() as api:
        yield Harness(api, token)


def test_get_machine_returns_the_recorded_machine_with_the_bearer_token(fly: Harness) -> None:
    fly.machine("machine-1", metadata=MARKER)

    observed = fly.client.get_machine("machine-1")

    assert observed["id"] == "machine-1" and observed["state"] == "started"
    request = fly.requests("GET")[-1]
    assert request["path"] == "/v1/apps/app/machines/machine-1"
    headers = {str(k).lower(): str(v) for k, v in cast(dict[str, str], request["headers"]).items()}
    assert headers["authorization"] == "Bearer deploy-token"


def test_metadata_update_changes_only_the_named_key(fly: Harness) -> None:
    fly.machine("machine-1", metadata={**MARKER, "run_id": "run-1", "deadline_epoch": "100"})

    fly.client.set_metadata("machine-1", "deadline_epoch", "200")

    assert fly.metadata("machine-1") == {
        **MARKER,
        "run_id": "run-1",
        "deadline_epoch": "200",
    }
    post = fly.requests("POST")[-1]
    assert post["path"] == "/v1/apps/app/machines/machine-1/metadata/deadline_epoch"
    assert post["body"] == {"value": "200"}


def test_list_filters_to_machines_carrying_the_owner_marker(fly: Harness) -> None:
    fly.machine("machine-ours", metadata=MARKER)
    fly.machine("machine-theirs", metadata={"factory-owner": "someone-else"})
    fly.machine("machine-untagged")

    listed = fly.client.list_machines("factory-owner", "agent-factory")

    assert [machine["id"] for machine in listed] == ["machine-ours"]
    assert fly.requests("GET")[-1]["path"] == (
        "/v1/apps/app/machines?metadata.factory-owner=agent-factory"
    )


def test_stop_and_start_move_the_machine_state(fly: Harness) -> None:
    fly.machine("machine-1", metadata=MARKER)

    fly.client.stop("machine-1")
    assert fly.api.machines["machine-1"]["state"] == "stopped"
    fly.client.start("machine-1")
    assert fly.api.machines["machine-1"]["state"] == "started"

    assert [r["path"] for r in fly.requests("POST")] == [
        "/v1/apps/app/machines/machine-1/stop",
        "/v1/apps/app/machines/machine-1/start",
    ]


def test_destroy_forces_deletion_and_a_second_destroy_treats_404_as_success(fly: Harness) -> None:
    fly.machine("machine-1", metadata=MARKER)

    fly.client.destroy("machine-1")
    assert "machine-1" not in fly.api.machines
    fly.client.destroy("machine-1")  # already gone: idempotent, no error

    deletes = fly.requests("DELETE")
    assert [r["path"] for r in deletes] == ["/v1/apps/app/machines/machine-1?force=true"] * 2


def test_server_error_surfaces_as_a_typed_error_naming_the_request_path(fly: Harness) -> None:
    fly.machine("machine-1", metadata=MARKER)
    fly.api.post_failures.append(503)

    with pytest.raises(FlyApiError) as raised:
        fly.client.stop("machine-1")

    assert raised.value.status == 503
    assert raised.value.path == "/v1/apps/app/machines/machine-1/stop"
    assert "/v1/apps/app/machines/machine-1/stop" in str(raised.value)
    assert "deploy-token" not in str(raised.value)
    # A 5xx is not a rate limit: it is reported at once, not retried.
    assert len(fly.requests("POST")) == 1
    assert fly.api.machines["machine-1"]["state"] == "started"


def test_update_stopped_deadline_merges_onto_the_observed_config_without_launching(
    fly: Harness,
) -> None:
    fly.machine(
        "machine-1",
        state="stopped",
        config={
            "image": "registry.fly.io/app@sha256:abc",
            "init": {"exec": ["bash", "-c", "guest"]},
            "guest": {"cpu_kind": "shared", "cpus": 4, "memory_mb": 8192},
            "env": {"FACTORY_RUN_ID": "run-1", "FACTORY_DEADLINE_EPOCH": "100"},
            "metadata": {**MARKER, "run_id": "run-1", "deadline_epoch": "100"},
        },
    )

    fly.client.update_stopped_deadline("machine-1", 200, {"run_id": "run-2"})

    update = fly.requests("POST")[-1]
    assert update["path"] == "/v1/apps/app/machines/machine-1"
    body = cast(dict[str, object], update["body"])
    assert body["skip_launch"] is True
    config = cast(dict[str, object], body["config"])
    assert config["image"] == "registry.fly.io/app@sha256:abc"
    assert config["init"] == {"exec": ["bash", "-c", "guest"]}
    assert config["guest"] == {"cpu_kind": "shared", "cpus": 4, "memory_mb": 8192}
    assert config["env"] == {"FACTORY_RUN_ID": "run-1", "FACTORY_DEADLINE_EPOCH": "200"}
    assert config["metadata"] == {**MARKER, "run_id": "run-2", "deadline_epoch": "200"}
    # The fake mirrors Fly: a stopped Machine is "replacing" after its update, not started.
    assert fly.api.machines["machine-1"]["state"] == "replacing"


@pytest.mark.parametrize("status", [408, 504])
def test_wait_state_reports_a_timed_out_wait_as_false(fly: Harness, status: int) -> None:
    fly.machine("machine-1", metadata=MARKER)
    fly.api.wait_failures.append(status)

    assert fly.client.wait_state("machine-1", "started", timeout_seconds=1) is False
    assert fly.client.wait_state("machine-1", "started", timeout_seconds=1) is True


def test_wait_state_raises_for_any_other_failure(fly: Harness) -> None:
    fly.machine("machine-1", metadata=MARKER)
    fly.api.wait_failures.append(500)

    with pytest.raises(FlyApiError) as raised:
        fly.client.wait_state("machine-1", "started", timeout_seconds=1)
    assert raised.value.status == 500
