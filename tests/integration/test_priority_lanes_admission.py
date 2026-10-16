"""INT-003: real runtime, controllers, PR/review/unblock handlers and store."""

# pyright: reportPrivateUsage=false
from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.config import JobCapConfig
from agent_factory.store import Claim, ClaimDraft
from agent_factory.work_kinds.base import Preparation
from tests.fixtures.priority_lanes import Site


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    value = Site(tmp_path, monkeypatch)
    try:
        yield value
    finally:
        value.store.close()


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
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
    site.card(8, "Urgent", kind)
    other_kind = "eval" if kind != "eval" else "fix"
    site.card(7, "Low", other_kind)
    (other,) = site.tick()
    assert other.kind == other_kind
    waits = site.store.get_settings_by_prefix("lane-wait", "")
    assert len(waits) == 4
    assert {wait["cause"] for wait in waits.values()} == {"lane-busy"}
    assert all(number not in site.prepared for number in (4, 5, 6, 8))
    assert {r.id for r in site.store.nonterminal_runs(kind=kind)} == {low.id, medium.id, high.id}
    site.finish(low)
    site.finish(medium)
    # The busy High lane holds new lower starts.
    assert site.tick() == []
    assert site.store.lane_decision(kind, "low").cause == "higher-lane"
    assert site.store.lane_decision(kind, "medium").cause == "higher-lane"
    site.finish(high)
    (urgent,) = site.tick()
    # Urgent shares the High lane and is ranked ahead of the waiting High card.
    assert urgent.lane == "high"
    assert site.store.get_claim(urgent.claim_id).issue_number == 8  # type: ignore[union-attr]


@pytest.fixture
def slots_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    value = Site(tmp_path, monkeypatch, slots=f"{request.param} = 2")
    try:
        yield value
    finally:
        value.store.close()


@pytest.mark.parametrize("slots_site", ["eval", "fix"], indirect=True)
def test_shared_slots_then_lanes(slots_site: Site) -> None:
    from agent_factory.operations import _slot_lines

    site = slots_site
    kind = next(iter(site.shared.slots))
    repo = "example/evals" if kind == "eval" else "example/work"
    verdict = "ready-for-human-review" if kind == "eval" else None
    site.card(1, "Low", kind)
    (first,) = site.tick()
    site.card(2, "High", kind)
    (second,) = site.tick()
    site.finish(first, verdict=verdict)
    site.finish(second, verdict=verdict)
    site.board.cards.clear()
    # Any two attempts of the kind run together, whatever their lanes.
    site.card(3, "Low", kind)
    (a,) = site.tick()
    site.card(4, "Low", kind)
    (b,) = site.tick()
    assert [a.lane, b.lane] == ["low", "low"]
    site.card(5, "Low", kind)
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "")[f"{repo}:5"]["cause"] == ("lane-busy")
    # Past two, the lane rules let higher Priority in.
    site.card(6, "Medium", kind)
    (medium,) = site.tick()
    site.card(7, "High", kind)
    (high,) = site.tick()
    assert [medium.lane, high.lane] == ["medium", "high"]
    assert len(site.store.nonterminal_runs(kind=kind)) == 4
    site.card(8, "Medium", kind)
    assert site.tick() == []
    assert 5 not in site.prepared and 8 not in site.prepared
    lines = _slot_lines(site.store)
    assert f"{kind} slot: busy (high, medium, low)" in lines
    assert sum(line.startswith(f"{kind} lane low: ") for line in lines) == 2
    # Other kinds keep one slot.
    other = "fix" if kind == "eval" else "eval"
    assert site.store.lane_slots(other) == 1


@pytest.mark.parametrize("slots_site", ["eval"], indirect=True)
def test_eval_continuation_and_reprioritization(slots_site: Site) -> None:
    site = slots_site
    low_card = site.card(1, "Low", "eval")
    (low,) = site.tick()
    site.card(2, "Low", "eval")
    (other,) = site.tick()
    site.card(3, "High", "eval")
    (high,) = site.tick()
    site.finish(low, verdict="ready-for-human-review")
    # Both shared slots are taken, so the next repetition follows the lane rules.
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "")["example/evals:1"]["cause"] == (
        "lane-busy"
    )
    low_card_index = site.board.cards.index(low_card)
    site.board.cards[low_card_index] = replace(low_card, priority="Medium")
    assert site.tick() == []
    assert (
        site.store.get_settings_by_prefix("lane-wait", "")["example/evals:1"]["cause"]
        == "higher-lane"
    )
    site.board.cards.remove(next(c for c in site.board.cards if c.source.number == 2))
    site.finish(other, verdict="ready-for-human-review")
    (second,) = site.tick()
    assert second.claim_id == low.claim_id and second.unit_key == "rep-2"
    assert second.lane == "medium"
    assert site.store.get_run(low.id).lane == "low"  # type: ignore[union-attr]
    assert site.store.get_run(high.id).status == "running"  # type: ignore[union-attr]


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
def test_review_candidate_falls_through_to_fresh_ready(site: Site, kind: str) -> None:
    old = site.seed_review(1, "Low", kind, feedback=False, ready=True)
    (run,) = site.tick()
    assert run.reason == "initial" and run.claim_id != old.id
    assert site.store.get_claim(old.id).lifecycle == "superseded"  # type: ignore[union-attr]


@pytest.mark.parametrize("kind", ["fix", "feature", "task"])
def test_review_blocked_ready_without_feedback_preserves_claim(site: Site, kind: str) -> None:
    claim = site.seed_review(1, "Low", kind, blocked=True, feedback=False, ready=True)

    assert site.tick() == []
    current = site.store.get_claim(claim.id)
    assert current is not None and current.lifecycle == "blocked"
    assert current.outcome["blocked_by"] == "review"
    assert current.outcome["review_checkpoint"] == "2026-01-01"
    assert [c.id for c in site.store.claims_for_item("P1")] == [claim.id]
    assert site.board.cards[0].fields[site.shared.project.status.id] == (
        site.shared.project.status.option("review")
    )
    assert 1 not in site.prepared


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
    # The final rewrite must retain the unblock helper's new wait and remove stale entries.
    site.store.set_setting("lane-wait", "example/work:99", expected_wait)
    assert site.tick() == []
    assert site.store.get_settings_by_prefix("lane-wait", "") == {"example/work:1": expected_wait}
    site.finish(high)
    (run,) = site.tick()
    assert run.reason == "unblock" and run.lane == "medium"
    assert site.store.get_settings_by_prefix("lane-wait", "") == {}


@pytest.mark.parametrize("blocked", [False, True])
def test_admission_failure_publishes_collected_lane_waits(
    site: Site, monkeypatch: pytest.MonkeyPatch, blocked: bool
) -> None:
    site.card(1, "High")
    (high,) = site.tick()
    card = site.card(2, "Medium")
    if blocked:
        handler = site.handlers["fix"]
        snapshot = handler.snapshot(card, site.board, site.shared)
        assert snapshot is not None
        claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
        assert claim is not None
        site.store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01"})
    site.card(3, "Low", "eval")
    site.store.set_setting("lane-wait", "example/work:99", {"lane": "low"})

    def fail_prepare(claim: Claim) -> Preparation:
        raise RuntimeError("admission failed")

    monkeypatch.setattr(site.handlers["eval"], "prepare", fail_prepare)
    with pytest.raises(RuntimeError, match="admission failed"):
        site.tick()

    assert site.store.get_settings_by_prefix("lane-wait", "") == {
        "example/work:2": {
            "lane": "medium",
            "cause": "higher-lane",
            "holder_run_id": high.id,
        }
    }


def test_unblock_failure_preserves_its_lane_wait(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    site.card(1, "High")
    (high,) = site.tick()
    card = site.card(2, "Medium")
    handler = site.handlers["fix"]
    snapshot = handler.snapshot(card, site.board, site.shared)
    assert snapshot is not None
    claim = site.controller.accept(snapshot, resolve=handler.resolve_request)
    assert claim is not None
    site.store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01"})

    def fail_reconcile(claim: Claim) -> None:
        raise RuntimeError("reconciliation failed")

    monkeypatch.setattr(handler, "reconcile", fail_reconcile)
    with pytest.raises(RuntimeError, match="reconciliation failed"):
        site.tick()
    assert site.store.get_setting("lane-wait", "example/work:2") == {
        "lane": "medium",
        "cause": "higher-lane",
        "holder_run_id": high.id,
    }


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
def test_technical_recovery_waits_for_higher_lane(site: Site, kind: str) -> None:
    from agent_factory.controller import AttemptResult

    site.card(1, "Low", kind)
    (low,) = site.tick()
    site.card(2, "High", kind)
    (high,) = site.tick()
    site.controller.record_result(low.id, AttemptResult("failed", None, {"reason": "technical"}))
    site.store.set_setting("consumed-results", low.id, {"complete": True})
    assert site.tick() == []
    assert (
        site.store.get_settings_by_prefix("lane-wait", "")["example/work:1"]["cause"]
        == "higher-lane"
    )
    site.finish(high)
    (recovery,) = site.tick()
    assert recovery.reason == "recovery" and recovery.claim_id == low.claim_id
    assert recovery.lane == "low"


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
