"""Recovery reads the prior attempt of the same repetition, not a neighbour's."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from agent_factory.controller import ExecutionPlan
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import AndSceneAdapter, PreparedWorktrees
from agent_factory.work_kinds.eval.handler import plan_attempt


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def plan(self, *args: object, **kwargs: object) -> ExecutionPlan:
        self.calls.append(kwargs)
        return ExecutionPlan(("run.sh",), "/tmp", {}, (), (), {"backend": "fly-machine"}, False)


def test_recovery_uses_the_previous_attempt_of_its_own_repetition(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(
            ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {"settings": {}})
        )
        # rep-1's first attempt proved a checkpoint before it was lost.
        first = store.reserve_run(
            claim.id, "rep-1", lane="low", reason="initial", evidence_path=str(tmp_path)
        )
        store.mark_running(first.id, {})
        store.update_progress(first.id, {"checkpoint_seen": True})
        store.finish_run(first.id, execution_status="failed", result={"reason": "machine lost"})
        # A later repetition sorts after rep-1 and never reached a checkpoint.
        other = store.reserve_run(
            claim.id, "rep-2", lane="low", reason="initial", evidence_path=str(tmp_path)
        )
        store.mark_running(other.id, {})
        store.finish_run(
            other.id, execution_status="failed", result={"reason": "suite launch failed"}
        )
        retry = store.reserve_run(
            claim.id, "rep-1", lane="low", reason="recovery", evidence_path=str(tmp_path / "retry")
        )
        adapter = _RecordingAdapter()
        frozen = store.get_claim(claim.id)
        assert frozen is not None

        plan_attempt(
            store,
            cast(AndSceneAdapter, adapter),
            frozen,
            retry,
            cast(PreparedWorktrees, None),
        )

        assert adapter.calls[0]["pre_checkpoint_proven"] is False
        assert adapter.calls[0]["expect_checkpoint"] is True
    finally:
        store.close()


def infra_settled_claim(store: ClaimStore, root: Path) -> tuple[str, dict[str, str]]:
    """Claim 850886c3's shape: rep-1 completed, rep-2 settled by infrastructure, rep-3 unstarted."""
    from agent_factory.store import ClaimDraft

    claim = store.create_claim(
        ClaimDraft(
            "example/evals", 125, "I125", "P125", "eval", "fp", {"settings": {"repetitions": 3}}
        )
    )
    ids: dict[str, str] = {}

    def attempt(unit: str, reason: str, status: str, result: dict[str, object], seen: bool) -> str:
        evidence = root / f"{claim.id}-{unit}"
        evidence.mkdir(exist_ok=True)
        run = store.reserve_run(
            claim.id, unit, lane="low", reason=reason, evidence_path=str(evidence)
        )
        store.mark_running(run.id, {})
        if seen:
            store.update_progress(run.id, {"checkpoint_seen": True})
        store.finish_run(run.id, execution_status=status, result=result)
        return run.id

    ids["rep-1"] = attempt(
        "rep-1", "initial", "completed", {"product_verdict": "unavailable"}, True
    )
    attempt("rep-2", "initial", "interrupted", {"reason": "Fly Machine stopped"}, True)
    attempt(
        "rep-2",
        "recovery",
        "failed",
        {
            "failure_stage": "pre-suite",
            "stage": "planning",
            "reason": "suite checkpoint is missing",
        },
        False,
    )
    ids["rep-2"] = attempt(
        "rep-2", "recovery", "interrupted", {"reason": "Fly launcher exited"}, True
    )
    # The checkpoint was collected after the last attempt ended.
    (root / f"{claim.id}-rep-2" / "run-state.json").write_text('{"schema_version": 1}')
    store.set_claim_lifecycle(
        claim.id, "settled", {"verdict": "infra-error", "failed_unit": "rep-2"}
    )
    return claim.id, ids


def _handler(store: ClaimStore) -> object:
    from agent_factory.work_kinds.eval import EvalDefaults
    from agent_factory.work_kinds.eval.handler import EvalHandler

    handler = EvalHandler(EvalDefaults("main", "main", {}, False, 3))
    handler.attach_store(store)
    return handler


def test_operator_resume_continues_the_checkpointed_repetition_then_starts_the_unstarted_one(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from agent_factory.work_kinds.eval.handler import EvalHandler, grant_resume, plan_resume

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim_id, ids = infra_settled_claim(store, tmp_path)
        handler = cast(EvalHandler, _handler(store))
        claim = store.get_claim(claim_id)
        assert claim is not None
        # Merely reopening the claim would skip rep-2 (its recovery is spent) and start rep-3.
        assert handler.next_unit(claim, store.runs_for_claim(claim_id)) == ("rep-3", "initial")

        dry = plan_resume(store, claim)
        assert dry.refusal is None
        assert dry.units == {
            "rep-1": ("keep", ids["rep-1"]),
            "rep-2": ("resume", ids["rep-2"]),
            "rep-3": ("start", None),
        }
        assert store.get_claim(claim_id).lifecycle == "settled"  # pyright: ignore[reportOptionalMemberAccess]

        granted = grant_resume(store, claim_id, now=datetime(2026, 10, 16, 19, tzinfo=UTC))
        assert granted.units == dry.units
        claim = store.get_claim(claim_id)
        assert claim is not None and claim.lifecycle == "waiting"
        assert handler.presentation(claim).status == "Ready"
        events = cast(dict[str, object], claim.reporting["events"])
        assert any(key.startswith("operator-resume:") for key in events)

        assert handler.next_unit(claim, store.runs_for_claim(claim_id)) == ("rep-2", "resume")
        resumed = store.reserve_run(
            claim_id,
            "rep-2",
            lane="low",
            reason="resume",
            evidence_path=str(tmp_path / f"{claim_id}-rep-2"),
        )
        adapter = _RecordingAdapter()
        plan_attempt(
            store, cast(AndSceneAdapter, adapter), claim, resumed, cast(PreparedWorktrees, None)
        )
        assert adapter.calls[0]["recovery"] is True
        assert adapter.calls[0]["pre_checkpoint_proven"] is False
        assert adapter.calls[0]["expect_checkpoint"] is True
        assert adapter.calls[0]["seed_checkpoint"] is True
        # The grant is spent once its repetition has a newer attempt.
        assert handler.next_unit(claim, store.runs_for_claim(claim_id)) == (None, "initial")
        store.mark_running(resumed.id, {})
        store.finish_run(
            resumed.id, execution_status="completed", result={"product_verdict": "unavailable"}
        )
        assert handler.next_unit(claim, store.runs_for_claim(claim_id)) == ("rep-3", "initial")
        assert handler.settle(claim, store.runs_for_claim(claim_id)) is None

        rep3 = store.reserve_run(
            claim_id, "rep-3", lane="low", reason="initial", evidence_path=str(tmp_path / "rep-3")
        )
        store.mark_running(rep3.id, {})
        store.finish_run(
            rep3.id, execution_status="completed", result={"product_verdict": "unavailable"}
        )
        outcome = handler.settle(claim, store.runs_for_claim(claim_id))
        assert outcome is not None and outcome.verdict == "pending-human-review"
        # A settled-then-resumed claim gets its own handoff event.
        assert outcome.event_key == "handoff:2026-10-16T19:00:00+00:00"
        # Completed rep-1 was never rerun.
        assert [run.id for run in store.runs_for_claim(claim_id) if run.unit_key == "rep-1"] == [
            ids["rep-1"]
        ]
    finally:
        store.close()


def test_operator_resume_refuses_claims_that_did_not_settle_on_infrastructure(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from agent_factory.work_kinds.eval.handler import grant_resume, plan_resume

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim_id, _ = infra_settled_claim(store, tmp_path)
        store.set_claim_lifecycle(claim_id, "settled", {"verdict": "failed"})
        assert "not infra-error" in str(plan_resume(store, store.get_claim(claim_id)).refusal)

        store.set_claim_lifecycle(claim_id, "settled", {"verdict": "infra-error"})
        (tmp_path / f"{claim_id}-rep-2" / "run-state.json").unlink()
        refused = grant_resume(store, claim_id, now=datetime.now(UTC))
        assert "no usable collected checkpoint" in str(refused.refusal)
        claim = store.get_claim(claim_id)
        assert claim is not None and claim.lifecycle == "settled"

        (tmp_path / f"{claim_id}-rep-2" / "run-state.json").write_text('{"schema_version": 1}')
        assert grant_resume(store, claim_id, now=datetime.now(UTC)).refusal is None
        # A claim that is no longer settled cannot be granted twice.
        assert "not settled" in str(grant_resume(store, claim_id, now=datetime.now(UTC)).refusal)
    finally:
        store.close()


def test_resumed_fly_attempt_rebuilds_an_image_cleanup_already_deleted(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from agent_factory.work_kinds.eval.handler import grant_resume

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim_id, _ = infra_settled_claim(store, tmp_path)
        digest = "sha256:" + "a" * 64
        build = {"repository": "registry.fly.io/app", "tag": "claim-x", "digest": digest}
        factory = tmp_path / f"{claim_id}-rep-2" / ".factory"
        factory.mkdir()
        (factory / "image-build.json").write_text(json.dumps(build))
        claim = store.get_claim(claim_id)
        assert claim is not None
        store.set_cleanup(
            claim_id,
            {**claim.cleanup, "registry": {digest: {**build, "deleted_at": "2026-10-16T18:24Z"}}},
        )
        assert grant_resume(store, claim_id, now=datetime.now(UTC)).refusal is None
        claim = store.get_claim(claim_id)
        assert claim is not None
        resumed = store.reserve_run(
            claim_id, "rep-2", lane="low", reason="resume", evidence_path=str(factory.parent)
        )

        plan_attempt(
            store,
            cast(AndSceneAdapter, _RecordingAdapter()),
            claim,
            resumed,
            cast(PreparedWorktrees, None),
        )

        # No deleted digest is pinned, and the launcher finds no stale build record to reuse.
        assert not (factory / "manifest.json").exists()
        assert not (factory / "image-build.json").exists()
        assert (factory / "image-build.deleted.json").exists()
    finally:
        store.close()
