"""A status correction is explained only when it undoes someone else's move."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from agent_factory import retention, runtime
from agent_factory.config import SharedConfig
from agent_factory.controller import ClaimPresentation, Controller
from agent_factory.github import GitHubClient, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.base import card_status

CONFIG = Path(__file__).resolve().parents[2] / "config" / "codagent.toml"


class RunningPresentation:
    def presentation(self, claim_id: str) -> ClaimPresentation:
        return ClaimPresentation("Running", None, ())

    def deliver_reports(self, claim_id: str) -> None:
        return None


class ReviewPresentation(RunningPresentation):
    def presentation(self, claim_id: str) -> ClaimPresentation:
        return ClaimPresentation("Review", "pending-human-review", ())


class BoardClient:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self.writes.append(option_id)

    def clear_field(self, project_id: str, item_id: str, field_id: str) -> None:
        return None

    def list_comment_records(self, repository: str, number: int) -> list[object]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        return "comment-id"


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


def _card(shared: SharedConfig, status: str, *, state: str = "open") -> ProjectQueueItem:
    return ProjectQueueItem(
        "P1",
        "I1",
        {
            shared.project.owner.id: shared.project.owner.option("factory"),
            shared.project.status.id: shared.project.status.option(status),
        },
        SourceItem("I1", "example/evals", 1, "writer", frozenset(), "Eval", state),
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


def _settled_claim(store: ClaimStore, kind: str) -> str:
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", kind, "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    return claim.id


def _review_report(
    store: ClaimStore,
    shared: SharedConfig,
    card: ProjectQueueItem,
    claim_id: str,
    client: BoardClient,
) -> None:
    runtime._report(  # pyright: ignore[reportPrivateUsage]
        store,
        cast(Controller, ReviewPresentation()),
        cast(GitHubClient, client),
        shared,
        card,
        claim_id,
        None,
    )


def test_closed_settled_fix_moves_review_card_to_done(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim_id = _settled_claim(store, "fix")
    card = _card(shared, "review", state="CLOSED")
    client = BoardClient()

    _review_report(store, shared, card, claim_id, client)

    assert card_status(shared, card) == "Done"
    assert shared.project.status.option("review") not in client.writes
    assert store.get_setting("field-delivery", f"{claim_id}:{shared.project.status.id}") == {
        "value": "Done"
    }
    claim = store.get_claim(claim_id)
    assert claim is not None
    retention.observe_done(store, claim, card_status(shared, card), datetime.now(UTC))
    assert (store.get_claim(claim_id) or claim).cleanup.get("done_observed_at") is not None


def test_open_settled_fix_stays_in_review(store: ClaimStore, shared: SharedConfig) -> None:
    claim_id = _settled_claim(store, "fix")
    card = _card(shared, "review", state="OPEN")
    client = BoardClient()

    _review_report(store, shared, card, claim_id, client)

    assert card_status(shared, card) == "Review"
    assert shared.project.status.option("done") not in client.writes


def test_closed_settled_feature_does_not_bounce_from_done_on_next_poll(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim_id = _settled_claim(store, "feature")
    card = _card(shared, "review", state="CLOSED")
    client = BoardClient()

    _review_report(store, shared, card, claim_id, client)
    assert card_status(shared, card) == "Done"
    first_writes = list(client.writes)
    _review_report(store, shared, card, claim_id, client)

    assert card_status(shared, card) == "Done"
    assert client.writes == first_writes


def test_reopened_settled_claim_does_not_leave_done(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim_id = _settled_claim(store, "fix")
    card = _card(shared, "done", state="OPEN")
    client = BoardClient()

    _review_report(store, shared, card, claim_id, client)

    assert card_status(shared, card) == "Done"
    assert shared.project.status.option("review") not in client.writes


def test_closed_settled_claim_does_not_change_a_non_factory_card(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim_id = _settled_claim(store, "fix")
    card = _card(shared, "review", state="CLOSED")
    card.fields.pop(shared.project.owner.id)
    client = BoardClient()

    _review_report(store, shared, card, claim_id, client)

    assert card_status(shared, card) == "Review"
    assert client.writes == []


def test_closed_unsettled_claim_is_cancelled_and_presented_done(
    store: ClaimStore, shared: SharedConfig
) -> None:
    claim = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp", {}))
    card = _card(shared, "running", state="CLOSED")
    client = BoardClient()
    controller = Controller(store, cast(GitHubClient, client), {})

    assert runtime._should_cancel(claim)  # pyright: ignore[reportPrivateUsage]
    controller.cancel(claim.id)
    runtime._report(  # pyright: ignore[reportPrivateUsage]
        store,
        controller,
        cast(GitHubClient, client),
        shared,
        card,
        claim.id,
        None,
    )

    assert (store.get_claim(claim.id) or claim).lifecycle == "cancelled"
    assert card_status(shared, card) == "Done"
