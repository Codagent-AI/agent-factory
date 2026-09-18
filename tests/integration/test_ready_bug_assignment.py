from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent_factory import runtime
from agent_factory.config import SharedConfig
from agent_factory.github import GitHubApiError, ProjectQueueItem
from agent_factory.routing import SourceItem


class PermissionClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.permission_calls = 0
        self.assignments: list[str] = []

    def get_permission(self, repository: str, login: str) -> str | None:
        self.permission_calls += 1
        if self.fail:
            raise GitHubApiError("temporary failure")
        return "write"

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self.assignments.append(item_id)


def _card(shared: SharedConfig, item_id: str) -> ProjectQueueItem:
    return ProjectQueueItem(
        item_id,
        f"content-{item_id}",
        {shared.project.status.id: shared.project.status.option("ready")},
        SourceItem(
            f"content-{item_id}",
            "Codagent-AI/agent-runner",
            111,
            "writer",
            frozenset(),
            shared.routing.bug_type,
            "OPEN",
        ),
    )


def test_ready_bug_permission_is_cached_per_author() -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    client = PermissionClient()
    cache: dict[tuple[str, str], str | None] = {}

    runtime._assign_ready_bug(client, shared, _card(shared, "card-1"), cache)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    runtime._assign_ready_bug(client, shared, _card(shared, "card-2"), cache)  # pyright: ignore[reportPrivateUsage, reportArgumentType]

    assert client.permission_calls == 1
    assert client.assignments == ["card-1", "card-2"]


def test_ready_bug_permission_failure_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    client = PermissionClient(fail=True)
    cache: dict[tuple[str, str], str | None] = {}

    with caplog.at_level(logging.WARNING):
        runtime._assign_ready_bug(client, shared, _card(shared, "card-1"), cache)  # pyright: ignore[reportPrivateUsage, reportArgumentType]
        runtime._assign_ready_bug(client, shared, _card(shared, "card-2"), cache)  # pyright: ignore[reportPrivateUsage, reportArgumentType]

    assert client.permission_calls == 1
    assert client.assignments == []
    assert "repository=Codagent-AI/agent-runner author=writer card=card-1" in caplog.text
