from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore, NonterminalRunError


def test_uncertain_execution_identity_retains_the_global_execution_slot(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "x", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/tmp/evidence")

    assert hasattr(store, "report_uncertainty"), "uncertain ownership must be durable"
    store.report_uncertainty(run.id, "PID identity cannot be verified")

    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "observing"
    assert saved.result["reason"] == "PID identity cannot be verified"
    with pytest.raises(NonterminalRunError):
        store.reserve_run(claim.id, "rep-1", reason="recovery", evidence_path="/tmp/evidence")
