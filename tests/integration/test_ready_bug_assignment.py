from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.config import FixTarget, LocalConfig, SharedConfig
from agent_factory.github import GitHubApiError, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from agent_factory.work_kinds.pull_request.kinds import FIX


class PermissionClient:
    def __init__(self, *, fail: bool = False, assignment_fail: bool = False) -> None:
        self.fail = fail
        self.assignment_fail = assignment_fail
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
        if self.assignment_fail:
            raise GitHubApiError("assignment failed")
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
    handler = PullRequestHandler(
        FIX, shared, LocalConfig.from_file(Path("config/local.example.toml"))
    )
    handler.attach_github(client)  # pyright: ignore[reportArgumentType]

    handler.ready_handoff(_card(shared, "card-1"), shared, cache)
    handler.ready_handoff(_card(shared, "card-2"), shared, cache)

    assert client.permission_calls == 1
    assert client.assignments == ["card-1", "card-2"]


def test_ready_bug_permission_failure_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    client = PermissionClient(fail=True)
    cache: dict[tuple[str, str], str | None] = {}
    handler = PullRequestHandler(
        FIX, shared, LocalConfig.from_file(Path("config/local.example.toml"))
    )
    handler.attach_github(client)  # pyright: ignore[reportArgumentType]

    with caplog.at_level(logging.WARNING):
        handler.ready_handoff(_card(shared, "card-1"), shared, cache)
        handler.ready_handoff(_card(shared, "card-2"), shared, cache)

    assert client.permission_calls == 1
    assert client.assignments == []
    assert "repository=Codagent-AI/agent-runner author=writer card=card-1" in caplog.text


def test_ready_bug_assignment_failure_is_isolated_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    client = PermissionClient(assignment_fail=True)
    cache: dict[tuple[str, str], str | None] = {}
    handler = PullRequestHandler(
        FIX, shared, LocalConfig.from_file(Path("config/local.example.toml"))
    )
    handler.attach_github(client)  # pyright: ignore[reportArgumentType]
    card = _card(shared, "card-1")

    with caplog.at_level(logging.WARNING):
        handler.ready_handoff(card, shared, cache)

    assert client.permission_calls == 1
    assert client.assignments == []
    assert card.fields.get(shared.project.owner.id) is None
    assert "repository=Codagent-AI/agent-runner author=writer card=card-1" in caplog.text
    assert "assignment failed" in caplog.text


def test_second_pull_request_kind_uses_its_own_targets_for_handoff_and_snapshot(
    tmp_path: Path,
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))

    def feature_type(_shared: SharedConfig) -> str:
        return "Feature"

    def feature_targets(_shared: SharedConfig) -> tuple[FixTarget, ...]:
        return (FixTarget("example/features"),)

    other = replace(
        FIX,
        kind="feature",
        noun="Feature",
        issue_type=feature_type,
        targets=feature_targets,
    )
    handler = PullRequestHandler(other, shared, local)
    client = PermissionClient()
    handler.attach_github(client)  # pyright: ignore[reportArgumentType]
    original = _card(shared, "card-feature")
    card = replace(
        original,
        source=replace(original.source, repository="example/features", issue_type="Feature"),
    )

    handler.ready_handoff(card, shared, {})

    assert client.assignments == ["card-feature"]
    snapshot = handler.snapshot(card, client, shared)
    assert snapshot is not None
    targets: list[FixTarget] = []

    def resolve(target: FixTarget) -> tuple[str, str, str]:
        targets.append(target)
        return ("target-sha", "runner-sha", "skills-sha")

    draft = handler.accept(snapshot, ClaimStore(tmp_path / "state.db"), resolve)
    assert isinstance(draft, ClaimDraft)
    assert targets == [FixTarget("example/features")]
    assert draft.frozen_spec["target"] == {"repository": "example/features", "branch": "main"}
