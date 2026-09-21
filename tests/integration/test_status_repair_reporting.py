"""A status correction is explained only when it undoes someone else's move."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from agent_factory import runtime
from agent_factory.config import SharedConfig
from agent_factory.controller import ClaimPresentation, Controller
from agent_factory.github import GitHubClient, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore

CONFIG = Path(__file__).resolve().parents[2] / "config" / "codagent.toml"


class RunningPresentation:
    def presentation(self, claim_id: str) -> ClaimPresentation:
        return ClaimPresentation("Running", None, ())

    def deliver_reports(self, claim_id: str) -> None:
        return None


class BoardClient:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self.writes.append(option_id)

    def clear_field(self, project_id: str, item_id: str, field_id: str) -> None:
        return None


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ClaimStore]:
    opened = ClaimStore(tmp_path / "state.sqlite3")
    yield opened
    opened.close()


@pytest.fixture
def shared() -> SharedConfig:
    return SharedConfig.from_toml(CONFIG.read_text(encoding="utf-8"))


def _active_claim(store: ClaimStore) -> str:
    claim = store.create_claim(
        ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {"settings": {}})
    )
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="unused")
    store.mark_running(run.id, {})
    return claim.id


def _card(shared: SharedConfig, status: str) -> ProjectQueueItem:
    return ProjectQueueItem(
        "P1",
        "I1",
        {
            shared.project.owner.id: shared.project.owner.option("factory"),
            shared.project.status.id: shared.project.status.option(status),
        },
        SourceItem("I1", "example/evals", 1, "writer", frozenset(), "Eval", "open"),
    )


def _report(store: ClaimStore, shared: SharedConfig, card: ProjectQueueItem, claim_id: str) -> None:
    runtime._report(  # pyright: ignore[reportPrivateUsage]
        store,
        cast(Controller, RunningPresentation()),
        cast(GitHubClient, BoardClient()),
        shared,
        card,
        claim_id,
        None,
    )


def _repairs(store: ClaimStore, claim_id: str) -> list[str]:
    return [e.key for e in store.pending_events(claim_id) if e.key.startswith("status-repair")]


def test_moving_a_newly_admitted_card_to_running_is_not_reported_as_a_repair(
    store: ClaimStore, shared: SharedConfig
) -> None:
    # Admission launches from a Ready card and then presents Running in the same cycle;
    # that is the factory's own transition, not a correction of someone else's move.
    claim_id = _active_claim(store)

    _report(store, shared, _card(shared, "ready"), claim_id)

    assert _repairs(store, claim_id) == []


def test_restoring_running_after_a_drag_during_execution_is_explained(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim_id = _active_claim(store)
    _report(store, shared, _card(shared, "ready"), claim_id)

    _report(store, shared, _card(shared, "review"), claim_id)

    assert _repairs(store, claim_id) == ["status-repair:Review:1"]
