from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_factory.controller import AttemptResult, Controller, RequestSnapshot
from agent_factory.store import ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults


def defaults() -> EvalDefaults:
    return EvalDefaults(
        agent_runner_ref="main",
        agent_skills_ref="main",
        roles={"lead": "codex:m:high", "implementor": "codex:m:high", "reviewer": "codex:m:high"},
        skip_validator=False,
        repetitions=2,
    )


def snapshot(body: str = "```eval\nrepetitions = 2\n```") -> RequestSnapshot:
    return RequestSnapshot(
        repository="example/evals",
        issue_number=1,
        issue_id="I1",
        project_item_id="P1",
        author="writer",
        author_permission="write",
        issue_type="Eval",
        labels=frozenset({"run-eval"}),
        status="Ready",
        owner="factory",
        verdict=None,
        body=body,
        closed=False,
    )


@dataclass
class Comments:
    posted: list[str] = field(default_factory=lambda: [])

    def list_comments(self, repository: str, number: int) -> list[str]:
        return self.posted.copy()

    def create_comment(self, repository: str, number: int, body: str) -> str:
        self.posted.append(body)
        return str(len(self.posted))


def test_controller_invalid_feedback_pause_and_lost_response_reporting(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    comments = Comments()
    controller = Controller(store, comments, defaults(), harness_sha="c" * 40)
    controller.pause()

    invalid = snapshot("```eval\nrepetitions = 0\n```")
    assert controller.accept(invalid, resolve=lambda _: ("a" * 40, "b" * 40)) is None
    assert len(comments.posted) == 1
    assert store.claims_for_item("P1") == []

    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    assert controller.reserve_next(claim.id, readiness=lambda: "Docker unavailable") is None
    assert controller.reserve_next(claim.id, readiness=lambda: None) is None
    controller.resume()
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None

    controller.record_result(
        run.id,
        AttemptResult(
            execution_status="completed",
            product_verdict="ready-for-human-review",
            result={"score": 60, "cost": None},
        ),
    )
    controller.deliver_reports(claim.id)
    delivered = comments.posted.copy()
    reopened = Controller(
        ClaimStore(tmp_path / "state.sqlite3"), comments, defaults(), harness_sha="c" * 40
    )
    reopened.deliver_reports(claim.id)
    assert comments.posted == delivered


def test_controller_preserves_product_failure_and_stops_after_second_technical_failure(
    tmp_path: Path,
) -> None:
    controller = Controller(
        ClaimStore(tmp_path / "state.sqlite3"), Comments(), defaults(), harness_sha="c" * 40
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    first = controller.reserve_next(claim.id, readiness=lambda: None)
    assert first is not None
    controller.record_result(
        first.id,
        AttemptResult("completed", "failed", {"score": 20}),
    )
    second = controller.reserve_next(claim.id, readiness=lambda: None)
    assert second is not None
    controller.record_result(
        second.id, AttemptResult("failed", None, {"failure": {"owner": "harness"}})
    )
    retry = controller.reserve_next(claim.id, readiness=lambda: None)
    assert retry is not None and retry.reason == "recovery"
    controller.record_result(
        retry.id, AttemptResult("failed", None, {"failure": {"owner": "harness"}})
    )

    assert controller.presentation(claim.id).verdict == "infra-error"
    assert controller.reserve_next(claim.id, readiness=lambda: None) is None
