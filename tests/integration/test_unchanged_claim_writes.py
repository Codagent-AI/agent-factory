"""A claim's row, and its updated_at, change only when its stored state changes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, parse_request
from tests.fixtures.fly.api import FakeMachinesApi
from tests.integration.test_fly_intake import (
    Installation,
    _wire,  # pyright: ignore[reportPrivateUsage]
)

ROLES = {"lead": "codex:x:medium", "implementor": "codex:x:medium", "tester": "codex:x:medium"}


def _updated_at(store: ClaimStore, claim_id: str) -> str:
    claim = store.get_claim(claim_id)
    assert claim is not None
    return claim.updated_at


def test_same_value_writes_leave_updated_at_alone(tmp_path: Path) -> None:
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store:
        claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "fix", "fp", {}))
        store.set_preparation(claim.id, {"branch_name": "b"})
        store.set_claim_lifecycle(claim.id, "cancelled", {})
        store.set_cleanup(claim.id, {"complete": True})
        store.record_event(claim.id, "cancelled", "Issue closed.")
        store.set_claim_sync(claim.id, {"completed": True})
        current = store.get_claim(claim.id)
        assert current is not None

        store.set_claim_lifecycle(claim.id, "cancelled", {})
        store.set_preparation(claim.id, {"branch_name": "b"})
        store.set_cleanup(claim.id, dict(current.cleanup))
        store.record_event(claim.id, "cancelled", "Issue closed.")
        store.set_claim_sync(claim.id, {"completed": True})
        assert store.get_claim(claim.id) == current

        store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "changed"})
        assert _updated_at(store, claim.id) != current.updated_at


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installation]:
    with FakeMachinesApi() as api:
        installation = Installation(tmp_path, api, "```eval\nrepetitions = 1\n```")
        _wire(monkeypatch, installation)
        yield installation


def test_tick_over_a_cleaned_cancelled_claim_on_a_closed_issue_writes_nothing(
    site: Installation,
) -> None:
    status = site.shared.project.status
    site.github.state = "CLOSED"
    site.github.fields[status.id] = status.option("done")
    with site.store() as store:
        store.set_paused(True)
        request = parse_request(site.github.body, EvalDefaults("main", "main", ROLES, False, 1))
        frozen = request.freeze(
            {name: site.revisions[name] for name in ("runner", "skills", "evals")},
            suite="and-scene",
        ).payload
        claim = store.create_claim(
            ClaimDraft(
                site.shared.routing.eval_source, 1, "I1", "P1", "eval", request.fingerprint, frozen
            )
        )
        store.set_claim_lifecycle(claim.id, "cancelled", {})
        store.record_event(
            claim.id, "cancelled", "Issue closed; execution cancelled and evidence retained."
        )
        store.acknowledge_event(claim.id, "cancelled", "comment-1")
        cleaned = store.get_claim(claim.id)
        assert cleaned is not None
        store.set_cleanup(
            claim.id,
            {
                **cleaned.cleanup,
                "complete": True,
                "done_observed_at": "2026-01-01T00:00:00+00:00",
                "retention": {"errors": [], "pruned_at": "2026-01-02T00:00:00+00:00"},
                "sweep_complete": True,
            },
        )

    site.tick()  # delivers the reports this minimal claim has never had
    with site.store() as store:
        before = store.get_claim(claim.id)
        assert not store.pending_events(claim.id)
    assert before is not None

    site.tick()
    site.tick()

    with site.store() as store:
        after = store.get_claim(claim.id)
    assert after is not None
    assert after.lifecycle == "cancelled"
    assert after.updated_at == before.updated_at
    assert (after.outcome, after.cleanup, after.reporting) == (
        before.outcome,
        before.cleanup,
        before.reporting,
    )
