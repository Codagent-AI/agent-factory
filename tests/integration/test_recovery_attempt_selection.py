"""Recovery reads the prior attempt of the same repetition, not a neighbour's."""

from __future__ import annotations

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
        first = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
        store.mark_running(first.id, {})
        store.update_progress(first.id, {"checkpoint_seen": True})
        store.finish_run(first.id, execution_status="failed", result={"reason": "machine lost"})
        # A later repetition sorts after rep-1 and never reached a checkpoint.
        other = store.reserve_run(claim.id, "rep-2", reason="initial", evidence_path=str(tmp_path))
        store.mark_running(other.id, {})
        store.finish_run(
            other.id, execution_status="failed", result={"reason": "suite launch failed"}
        )
        retry = store.reserve_run(
            claim.id, "rep-1", reason="recovery", evidence_path=str(tmp_path / "retry")
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
