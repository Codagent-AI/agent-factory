"""The ordered stop classification table."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.config import SharedConfig
from agent_factory.github import IssuePresence, ProjectQueueItem
from agent_factory.notify.detect import classify
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore


@pytest.mark.parametrize(
    ("lifecycle", "status", "outcome", "board", "kind", "waiting", "expected"),
    [
        ("settled", "completed", "pull-request", "ready", "fix", False, None),
        ("settled", "completed", "pull-request", "review", "fix", True, None),
        ("cancelled", "completed", "", "review", "fix", False, "cancelled"),
        ("settled", "failed", "", "review", "fix", False, "failed"),
        ("blocked", "completed", "needs-input", "review", "feature", False, "needs-input"),
        ("settled", "completed", "pull-request", "review", "fix", False, "pull-request"),
        ("settled", "completed", "", "review", "eval", False, "settled"),
        ("running", "completed", "", "absent", "fix", False, "not-queued"),
        ("running", "completed", "", "review", "fix", False, None),
    ],
)
def test_classification_table(
    tmp_path: Path,
    lifecycle: str,
    status: str,
    outcome: str,
    board: str,
    kind: str,
    waiting: bool,
    expected: str | None,
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", kind, "fp", {}))
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/evidence"
        )
        claim = replace(
            claim, lifecycle=lifecycle, outcome={"waiting_review": {"pr": 1}} if waiting else {}
        )
        run = replace(run, status=status, result={"outcome": outcome})
        card: ProjectQueueItem | IssuePresence
        if board == "absent":
            card = IssuePresence("OPEN", frozenset(), "", False)
        else:
            card = ProjectQueueItem(
                "P",
                "I",
                {
                    shared.project.owner.id: shared.project.owner.option("factory"),
                    shared.project.status.id: shared.project.status.option(board),
                },
                SourceItem("I", "o/r", 1, "author", frozenset(), "Bug", "open"),
            )
        assert classify(claim, run, card, shared) == expected
    finally:
        store.close()
