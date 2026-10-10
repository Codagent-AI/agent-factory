"""INT-003: real runtime, controllers, PR/review/unblock handlers and store."""

# pyright: reportPrivateUsage=false
from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.config import JobCapConfig
from agent_factory.store import ClaimDraft
from tests.fixtures.priority_lanes import Site


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    value = Site(tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.store.close()


@pytest.mark.parametrize("kind", ["fix", "feature", "task", "eval"])
def test_escalation_holds_and_independent_kinds(site: Site, kind: str) -> None:
    site.card(1, "Low", kind)
    (low,) = site.tick()
    site.card(2, "Medium", kind)
    (medium,) = site.tick()
    site.card(3, "High", kind)
    (high,) = site.tick()
    assert [low.lane, medium.lane, high.lane] == ["low", "medium", "high"]
    site.card(4, "High", kind)
    site.card(5, "Low", kind)
    site.card(6, "Medium", kind)
    other_kind = "eval" if kind != "eval" else "fix"
    site.card(7, "Low", other_kind)
    (other,) = site.tick()
    assert other.kind == other_kind
    waits = site.store.get_settings_by_prefix("lane-wait", "")
    assert len(waits) == 3
    assert {wait["cause"] for wait in waits.values()} == {"lane-busy"}
    assert all(number not in site.prepared for number in (4, 5, 6))
    site.card(8, "Urgent", kind)
    (urgent,) = site.tick()
    assert urgent.lane == "urgent"
    assert {r.id for r in site.store.nonterminal_runs(kind=kind)} == {
        low.id,
        medium.id,
        high.id,
        urgent.id,
    }
    site.finish(low, verdict="ready-for-human-review" if kind == "eval" else None)
    site.finish(medium, verdict="ready-for-human-review" if kind == "eval" else None)
    if kind != "eval":
        assert site.tick() == []
    else:
        (continuation,) = site.tick()
        assert continuation.claim_id == medium.claim_id and continuation.lane == "medium"
    assert site.store.lane_decision(kind, "low", None, "initial").cause == "higher-lane"


def test_eval_continuation_and_reprioritization(site: Site) -> None:
    low_card = site.card(1, "Low", "eval")
    (low,) = site.tick()
    site.card(2, "High", "eval")
    (high,) = site.tick()
    site.finish(low, verdict="ready-for-human-review")
    (second,) = site.tick()
    assert second.claim_id == low.claim_id and second.unit_key == "rep-2" and second.lane == "low"
    assert site.store.get_run(high.id).status == "running"  # type: ignore[union-attr]
    low_card_index = site.board.cards.index(low_card)
    site.board.cards[low_card_index] = replace(low_card, priority="Urgent")
    assert site.store.get_run(second.id).lane == "low"  # type: ignore[union-attr]
    site.finish(second, verdict="ready-for-human-review")
    (third,) = site.tick()
    assert third.unit_key == "rep-3" and third.lane == "urgent"


@pytest.mark.parametrize("planning", [False, True])
def test_never_launched_claim_is_a_start(site: Site, planning: bool) -> None:
    card = site.card(1, "Low")
    handler = site.handlers["fix"]
    snapshot = handler.snapshot(card, site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    if planning:
        run = site.store.reserve_run(
            claim.id, "fix", lane="low", reason="initial", evidence_path="/e"
        )
        site.finish(run, planning=True)
    site.card(2, "High")
    (high,) = site.tick()
    assert site.store.get_claim(high.claim_id).issue_number == 2  # type: ignore[union-attr]
    assert site.tick() == []
    assert 1 not in site.prepared
    assert (
        site.store.get_settings_by_prefix("lane-wait", "")["example/work:1"]["cause"]
        == "higher-lane"
    )


@pytest.mark.parametrize(
    ("review_priority", "ready_priority", "review_first"),
    [("Low", "High", False), (None, "Low", False), ("Medium", "Medium", True)],
)
def test_review_order_and_wait_bookkeeping(
    site: Site, review_priority: str | None, ready_priority: str, review_first: bool
) -> None:
    claim = site.seed_review(1, review_priority)
    site.card(2, ready_priority)
    (run,) = site.tick()
    assert (run.reason == "review") is review_first
    assert (run.claim_id == claim.id) is review_first
    if not review_first:
        current = site.store.get_claim(claim.id)
        assert current is not None and current.outcome.get("waiting_review")
        assert 1 not in site.prepared
        assert site.store.get_settings_by_prefix("lane-wait", "")["example/work:1"]["cause"] in {
            "higher-lane",
            "lane-busy",
        }


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
def test_review_blocked_and_prior_pr_fallback(site: Site, kind: str) -> None:
    claim = site.seed_review(1, "High", kind, blocked=True)
    (run,) = site.tick()
    assert run.reason == "review" and run.claim_id == claim.id and run.lane == "high"
    site.finish(run)
    site.board.cards.clear()
    fallback = site.seed_review(2, "Medium", kind, fallback=True)
    (run,) = site.tick()
    assert run.reason == "review" and run.claim_id == fallback.id


@pytest.mark.parametrize("kind", ["fix", "task"])
@pytest.mark.parametrize("blocked", [False, True])
def test_review_candidate_falls_through_to_fresh_ready(
    site: Site, kind: str, blocked: bool
) -> None:
    old = site.seed_review(1, "Low", kind, feedback=False, ready=True, blocked=blocked)
    (run,) = site.tick()
    assert run.reason == "initial" and run.claim_id != old.id
    assert site.store.get_claim(old.id).lifecycle == "superseded"  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
def test_unblock_reconciles_but_waits_for_higher_lane(site: Site, kind: str) -> None:
    from agent_factory.operations import _lane_wait_lines

    card = site.card(1, "Medium", kind)
    handler = site.handlers[kind]
    snapshot = handler.snapshot(card, site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    site.store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01"})
    from agent_factory.github import IssueComment

    site.board.comments[1] = [IssueComment("writer", "retry", "writer", "2099-01-01")]
    site.card(2, "High", kind)
    (high,) = site.tick()
    assert high.lane == "high"
    assert 1 in site.reconciled and 1 not in site.prepared
    expected_wait = {"lane": "medium", "cause": "higher-lane", "holder_run_id": high.id}
    assert site.store.get_setting("lane-wait", "example/work:1") == expected_wait
    assert any(
        f"example/work#1 waits for {kind} lane medium" in line
        and "example/work#2; higher-lane" in line
        for line in _lane_wait_lines(site.store)
    )
    # The next cycle clears the namespace before the unblock helper writes its wait.
    # The final rewrite must retain that new wait and remove unrelated stale entries.
    site.store.set_setting("lane-wait", "example/work:99", expected_wait)
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "") == {"example/work:1": expected_wait}
    site.finish(high)
    (run,) = site.tick()
    assert run.reason == "unblock" and run.lane == "medium"
    assert site.store.get_settings_by_prefix("lane-wait", "") == {}


@pytest.mark.parametrize("provider_hold", [False, True])
def test_quota_held_claim_is_not_otherwise_admissible_for_lane_wait(
    site: Site, monkeypatch: pytest.MonkeyPatch, provider_hold: bool
) -> None:
    site.card(1, "High")
    (high,) = site.tick()
    card = site.card(2, "Low")
    handler = site.handlers["fix"]
    snapshot = handler.snapshot(card, site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    hold = {"until": "2099-01-01T00:00:00+00:00"}
    provider = "codex"

    def providers(_: object) -> set[str]:
        return {provider}

    if provider_hold:
        monkeypatch.setattr(handler, "providers", providers)
        site.store.set_setting("admission", f"quota:{provider}", hold)
    else:
        site.store.set_hold(claim.id, "quota", hold)
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "") == {}
    assert 2 not in site.prepared
    if provider_hold:
        site.store.clear_setting("admission", f"quota:{provider}")
    else:
        site.store.clear_setting("claim-hold", f"{claim.id}:quota")
    assert site.tick() == []
    assert site.store.get_setting("lane-wait", "example/work:2") == {
        "lane": "low",
        "cause": "higher-lane",
        "holder_run_id": high.id,
    }


def test_kind_mode_and_one_reservation_per_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = Site(tmp_path, monkeypatch, lanes=False)
    try:
        site.card(1, "Low")
        site.card(2, "High")
        site.card(3, "Low", "task")
        runs = site.tick()
        assert {r.kind for r in runs} == {"fix", "task"}
        assert len(runs) == 2
        assert site.store.lane_mode() == "kind"
        assert site.tick() == []
        assert 1 not in site.prepared
    finally:
        site.store.close()


def test_lane_wait_does_not_get_a_job_cap_comment(site: Site) -> None:
    site.card(1, "High")
    (high,) = site.tick()
    site.card(2, "Low")
    site.shared_path.write_text(
        site.shared_path.read_text() + "\n[job_cap]\nattempts = 1\nwindow_hours = 24\n"
    )
    site.store.job_cap = JobCapConfig(1, 24)
    assert site.tick() == []
    assert not site.store.get_setting("job-cap-card", "example/work:2")
    assert not site.board.comments.get(2)
    assert (
        site.store.get_settings_by_prefix("lane-wait", "")["example/work:2"]["holder_run_id"]
        == high.id
    )


def test_legacy_holder_blocks_every_lane(site: Site) -> None:
    site.card(1, "Low")
    (run,) = site.tick()
    site.store._connection.execute("UPDATE run SET lane=NULL WHERE id=?", (run.id,))
    site.card(2, "Urgent")
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "")["example/work:2"]["cause"] == "legacy"


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
def test_technical_recovery_continues_beside_higher_lane(site: Site, kind: str) -> None:
    from agent_factory.controller import AttemptResult

    site.card(1, "Low", kind)
    (low,) = site.tick()
    site.card(2, "High", kind)
    (high,) = site.tick()
    site.controller.record_result(low.id, AttemptResult("failed", None, {"reason": "technical"}))
    site.store.set_setting("consumed-results", low.id, {"complete": True})
    (recovery,) = site.tick()
    assert recovery.reason == "recovery" and recovery.claim_id == low.claim_id
    assert recovery.lane == "low"
    assert site.store.get_run(high.id).status == "running"  # type: ignore[union-attr]


def test_memory_is_resampled_before_each_kind_reservation(site: Site) -> None:
    site.config.write_text(
        site.config.read_text().replace('execution = "host"', 'execution = "docker"')
    )
    site.card(1, "High", "fix")
    site.card(2, "High", "eval")
    assert len(site.tick()) == 2
    assert site.memory_probes == 2


def test_lane_busy_controller_return_has_no_hold(site: Site) -> None:
    site.card(1, "High")
    (high,) = site.tick()
    site.card(2, "Low")
    handler = site.handlers["fix"]
    snapshot = handler.snapshot(site.board.cards[-1], site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    assert site.controller.reserve_next(claim.id, lane="low", readiness=lambda: None) is None
    assert site.store.get_settings_by_prefix("claim-hold", claim.id) == {}
    assert site.store.get_run(high.id).status == "running"  # type: ignore[union-attr]


def test_drag_to_ready_unblocks_without_comments(site: Site) -> None:
    card = site.card(1, "Low")
    handler = site.handlers["fix"]
    snapshot = handler.snapshot(card, site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    site.store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01"})
    (run,) = site.tick()
    assert run.reason == "unblock" and run.claim_id == claim.id


def test_blocked_claim_without_input_is_not_a_lane_wait(site: Site) -> None:
    card = site.card(1, "Low", status="Running")
    claim = site.store.create_claim(ClaimDraft("example/work", 1, "I1", card.id, "fix", "fp", {}))
    site.store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01"})
    site.card(2, "High")
    assert len(site.tick()) == 1
    assert site.store.get_settings_by_prefix("lane-wait", "") == {}
    assert 1 not in site.reconciled


@pytest.mark.parametrize("reentry", ["review", "unblock"])
def test_cycle_limit_defers_reentry_even_when_memory_and_lanes_allow(
    site: Site, monkeypatch: pytest.MonkeyPatch, reentry: str
) -> None:
    from agent_factory import runtime
    from agent_factory.github import IssueComment

    if reentry == "review":
        second = site.seed_review(2, "Low")
    else:
        card = site.card(2, "Low")
        handler = site.handlers["fix"]
        snapshot = handler.snapshot(card, site.board, site.shared)
        assert snapshot is not None
        second = site.controller.accept(snapshot, resolve=handler.resolve_request)
        assert second is not None
        site.store.set_claim_lifecycle(second.id, "blocked", {"declined_at": "2026-01-01"})
        site.board.comments[2] = [IssueComment("writer", "retry", "writer", "2099-01-01")]
    first = site.seed_review(1, "High")

    def finish_on_launch(state: Path, run_id: str, *args: object, **kwargs: object) -> None:
        site.launch(state, run_id, *args, **kwargs)
        # Remove lane contention: only the per-cycle limit should defer the second claim.
        site.store.finish_run(run_id, execution_status="completed", result={})

    monkeypatch.setattr(runtime, "launch_supervisor", finish_on_launch)
    handler = site.handlers["fix"]
    method_name = "review_round" if reentry == "review" else "unblock"
    original = getattr(handler, method_name)
    admissions: list[bool] = []

    def with_memory(*args: object, **kwargs: object):
        admissions.append(bool(kwargs["admission_available"]))
        kwargs["memory_available"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(handler, method_name, with_memory)
    (run,) = site.tick()
    assert run.claim_id == first.id
    assert admissions[-1] is False
    assert not site.store.runs_for_claim(second.id)
    assert 2 not in site.prepared
    if reentry == "review":
        assert site.store.get_claim(second.id).outcome.get("waiting_review")  # type: ignore[union-attr]
    else:
        assert 2 in site.reconciled


def test_job_cap_reuses_the_claim_selection_from_lane_admission(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.controller import Controller, RequestSnapshot
    from agent_factory.store import Claim

    site.card(1, "Low")
    (run,) = site.tick()
    site.finish(run)
    site.board.cards.clear()
    site.card(2, "Low")
    site.shared_path.write_text(
        site.shared_path.read_text() + "\n[job_cap]\nattempts = 1\nwindow_hours = 24\n"
    )
    selections: list[int] = []
    original = Controller.select_existing

    def select_once(
        controller: Controller, snapshot: RequestSnapshot, *, fresh: bool = False
    ) -> Claim | None:
        selections.append(snapshot.issue_number)
        return original(controller, snapshot, fresh=fresh)

    monkeypatch.setattr(Controller, "select_existing", select_once)
    assert site.tick() == []
    assert selections == [2]
    assert site.store.get_setting("job-cap-card", "example/work:2")
