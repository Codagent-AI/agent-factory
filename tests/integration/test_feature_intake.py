"""INT-005: a Feature enters only by the configured, authorized Ready handoff."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from dataclasses import replace

from agent_factory.config import FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.controller import RequestSnapshot
from agent_factory.github import IssueComment, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.work_kinds import handlers
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE


def _snapshot(permission: str = "write") -> RequestSnapshot:
    return RequestSnapshot(
        "example/work",
        12,
        "I12",
        "P12",
        "author",
        permission,
        "Feature",
        frozenset(),
        "Ready",
        "factory",
        None,
        "request",
        False,
    )


def test_feature_admission_requires_configuration_and_writer() -> None:
    local = LocalConfig.from_toml(_LOCAL_BASE)
    disabled = replace(
        SharedConfig.from_toml(_SHARED_BASE),
        fix=FixConfig(targets=(FixTarget("example/work"),)),
    )
    enabled = SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n")
    enabled = replace(enabled, fix=disabled.fix)
    assert not handlers(disabled, local)["feature"].handles(_snapshot())
    feature = handlers(enabled, local)["feature"]
    assert feature.handles(_snapshot())
    assert not feature.handles(_snapshot("read"))
    assert not feature.handles(replace(_snapshot(), repository="other/repo"))


def test_non_writer_ready_feature_gets_one_explanation() -> None:
    shared = SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n")
    shared = replace(shared, fix=FixConfig(targets=(FixTarget("example/work"),)))
    feature = handlers(shared, LocalConfig.from_toml(_LOCAL_BASE))["feature"]

    class GitHub:
        def __init__(self) -> None:
            self.comments: list[IssueComment] = []

        def get_permission(self, repository: str, login: str) -> str:
            return "read"

        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return self.comments

        def create_comment(self, repository: str, number: int, body: str) -> str:
            self.comments.append(IssueComment("1", body, "factory"))
            return "1"

    github = GitHub()
    feature.attach_github(github)  # pyright: ignore[reportArgumentType]
    card = ProjectQueueItem(
        "P12",
        "I12",
        {shared.project.status.id: shared.project.status.option("ready")},
        SourceItem("I12", "example/work", 12, "reader", frozenset(), "Feature", "OPEN"),
    )
    feature.ready_handoff(card, shared, {})
    feature.ready_handoff(card, shared, {})
    assert len(github.comments) == 1
    assert "write" in github.comments[0].body
    assert shared.project.owner.id not in card.fields
