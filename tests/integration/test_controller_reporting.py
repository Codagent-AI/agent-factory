from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_factory.controller import AttemptResult, Controller, RequestSnapshot
from agent_factory.github import GitHubApiError, IssueComment
from agent_factory.store import ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler


def defaults() -> EvalDefaults:
    return EvalDefaults(
        agent_runner_ref="main",
        agent_skills_ref="main",
        roles={"lead": "codex:m:high", "implementor": "codex:m:high", "tester": "codex:m:high"},
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

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []


def test_controller_invalid_feedback_pause_and_lost_response_reporting(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    comments = Comments()
    controller = Controller(
        store, comments, {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    controller.pause()

    invalid = snapshot("```eval\nrepetitions = 0\n```")
    assert controller.accept(invalid, resolve=lambda _: ("a" * 40, "b" * 40)) is None
    assert len(comments.posted) == 1
    assert store.claims_for_item("P1") == []

    claim = controller.accept(
        snapshot("```eval\nrepetitions = 1\n```"), resolve=lambda _: ("a" * 40, "b" * 40)
    )
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
        ClaimStore(tmp_path / "state.sqlite3"),
        comments,
        {"eval": EvalHandler(defaults(), harness_ref="c" * 40)},
    )
    reopened.deliver_reports(claim.id)
    assert comments.posted == delivered


def test_controller_preserves_product_failure_and_stops_after_second_technical_failure(
    tmp_path: Path,
) -> None:
    controller = Controller(
        ClaimStore(tmp_path / "state.sqlite3"),
        Comments(),
        {"eval": EvalHandler(defaults(), harness_ref="c" * 40)},
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


def test_controller_does_not_trust_user_markers_and_records_delivery_failure(
    tmp_path: Path,
) -> None:
    @dataclass
    class FailingComments(Comments):
        def create_comment(self, repository: str, number: int, body: str) -> str:
            raise GitHubApiError("permission revoked")

        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return [
                IssueComment("user-comment", "<!-- agent-factory:event:x:accepted -->", "writer")
            ]

    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, FailingComments(), {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None

    controller.deliver_reports(claim.id)

    assert store.pending_events(claim.id)
    assert (
        store.delivery_failures(claim.id)["accepted"]["error"]
        == "GitHubApiError: permission revoked"
    )


def test_controller_never_hands_off_a_cancelled_repetition(tmp_path: Path) -> None:
    controller = Controller(
        ClaimStore(tmp_path / "state.sqlite3"),
        Comments(),
        {"eval": EvalHandler(defaults(), harness_ref="c" * 40)},
    )
    claim = controller.accept(
        snapshot("```eval\nrepetitions = 1\n```"), resolve=lambda _: ("a" * 40, "b" * 40)
    )
    assert claim is not None
    first = controller.reserve_next(claim.id, readiness=lambda: None)
    assert first is not None

    controller.record_result(first.id, AttemptResult("cancelled", None, {}))

    assert controller.presentation(claim.id).status != "Review"


def test_delivery_diagnostics_survive_later_event_acknowledgement(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, Comments(), {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    store.record_delivery_failure(claim.id, "accepted", GitHubApiError("temporary outage"))

    controller.deliver_reports(claim.id)

    assert (
        store.delivery_failures(claim.id)["accepted"]["error"] == "GitHubApiError: temporary outage"
    )


def test_nondefault_bot_recovers_a_lost_successful_comment_response(tmp_path: Path) -> None:
    class LostResponse(Comments):
        def create_comment(self, repository: str, number: int, body: str) -> str:
            self.posted.append(body)
            raise GitHubApiError("response lost after successful creation")

        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return [
                IssueComment(str(i), body, "example-worker[bot]")
                for i, body in enumerate(self.posted)
            ]

    store = ClaimStore(tmp_path / "state.sqlite3")
    comments = LostResponse()
    controller = Controller(
        store,
        comments,
        {"eval": EvalHandler(defaults(), harness_ref="c" * 40)},
        factory_login="example-worker[bot]",
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    controller.deliver_reports(claim.id)
    controller.deliver_reports(claim.id)
    assert len(comments.posted) == 1 and not store.pending_events(claim.id)
    store.close()


def test_codex_quota_suspends_other_claims_too(tmp_path: Path) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, Comments(), {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    first = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert first is not None
    run = controller.reserve_next(first.id, readiness=lambda: None)
    assert run is not None
    controller.record_result(
        run.id,
        AttemptResult("failed", None, {}, quota_until=datetime.now(UTC) + timedelta(hours=1)),
    )
    second = controller.accept(
        replace(snapshot(), issue_id="I2", project_item_id="P2", issue_number=2),
        resolve=lambda _: ("a" * 40, "b" * 40),
    )
    assert second is not None
    assert controller.reserve_next(second.id, readiness=lambda: None) is None
    store.close()


def test_quota_hold_scoped_to_provider_leaves_other_providers_admissible(tmp_path: Path) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime, timedelta

    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, Comments(), {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    codex_body = (
        "```eval\nrepetitions = 1\n"
        "lead = 'codex:m:high'\nimplementor = 'codex:m:high'\ntester = 'codex:m:high'\n```"
    )
    cursor_body = (
        "```eval\nrepetitions = 1\n"
        "lead = 'cursor:m:high'\nimplementor = 'cursor:m:high'\ntester = 'cursor:m:high'\n```"
    )
    codex_claim = controller.accept(snapshot(codex_body), resolve=lambda _: ("a" * 40, "b" * 40))
    assert codex_claim is not None
    run = controller.reserve_next(codex_claim.id, readiness=lambda: None)
    assert run is not None
    controller.record_result(
        run.id,
        AttemptResult(
            "failed",
            None,
            {"failure": {"reason": "Codex usage limit reached"}},
            quota_until=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    another_codex_claim = controller.accept(
        replace(snapshot(codex_body), issue_id="I2", project_item_id="P2", issue_number=2),
        resolve=lambda _: ("a" * 40, "b" * 40),
    )
    assert another_codex_claim is not None
    assert controller.reserve_next(another_codex_claim.id, readiness=lambda: None) is None

    cursor_claim = controller.accept(
        replace(snapshot(cursor_body), issue_id="I3", project_item_id="P3", issue_number=3),
        resolve=lambda _: ("a" * 40, "b" * 40),
    )
    assert cursor_claim is not None
    cursor_run = controller.reserve_next(cursor_claim.id, readiness=lambda: None)
    assert cursor_run is not None
    store.close()


def test_malformed_terminal_artifact_is_reported_as_failure_not_stale_success(
    tmp_path: Path,
) -> None:
    from agent_factory import runtime
    from agent_factory.suites.and_scene import AndSceneAdapter

    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store,
        Comments(),
        {"eval": EvalHandler(defaults(), harness_ref="c" * 40)},
        artifact_root=tmp_path / "artifacts",
    )
    claim = controller.accept(
        snapshot("```eval\nrepetitions=1\n```"), resolve=lambda _: ("a" * 40, "b" * 40)
    )
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    artifact = Path(run.evidence_path)
    artifact.mkdir(parents=True)
    (artifact / "result.json").write_text('{"incomplete":')
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"product_verdict": "ready-for-human-review", "score": 60},
    )
    runtime._consume_results(  # pyright: ignore[reportPrivateUsage]
        store, controller, AndSceneAdapter(environment_file=tmp_path / "unused")
    )
    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "failed"
    assert "invalid result.json" in str(saved.result["reason"])
    assert "invalid result.json" in str(store.pending_events(claim.id))
    assert controller.presentation(claim.id).verdict == "infra-error"
    assert (artifact / "result.json").read_text() == '{"incomplete":'
    store.close()


def test_real_suite_result_is_reported_concisely_with_delivery_and_usage(tmp_path: Path) -> None:
    from agent_factory.suites.and_scene import AndSceneAdapter

    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    (artifact / "result.json").write_bytes(
        Path("tests/fixtures/and-scene-result-v7.json").read_bytes()
    )
    comments = Comments()
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, comments, {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    result = AndSceneAdapter(environment_file=tmp_path / "env").read_result(artifact)
    controller.record_result(run.id, result)
    controller.deliver_reports(claim.id)
    body = next(body for body in comments.posted if ":complete -->" in body)
    assert "Execution: completed" in body
    assert "55.48/70" in body
    assert "11254.61 s" in body
    assert str(artifact / "report.html") in body
    assert "https://github.com/Codagent-AI/and-scene/pull/16" in body
    assert "eval/and-scene/astra-lead-20260909T142250Z" in body
    assert "50844812 input" in body and "216517 output" in body
    assert "Cost: unavailable" in body
    assert len(body) < 2500
    store.close()


def test_technical_retry_and_exhaustion_report_failure_details(tmp_path: Path) -> None:
    comments = Comments()
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, comments, {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    for _ in range(2):
        run = controller.reserve_next(claim.id, readiness=lambda: None)
        assert run is not None
        controller.record_result(
            run.id,
            AttemptResult(
                "failed",
                None,
                {
                    "failure": {
                        "owner": "evaluation-harness",
                        "reason": "browser executable missing",
                    },
                },
            ),
        )
    controller.deliver_reports(claim.id)
    for event in ("retry", "exhausted"):
        body = next(body for body in comments.posted if f":{event} -->" in body)
        assert "browser executable missing" in body
        assert "evaluation-harness" in body
        assert "Execution: failed" in body
    store.close()


def test_report_redacts_credentials_in_failure_diagnostics(tmp_path: Path) -> None:
    comments = Comments()
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(
        store, comments, {"eval": EvalHandler(defaults(), harness_ref="c" * 40)}
    )
    claim = controller.accept(snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    controller.record_result(
        run.id,
        AttemptResult(
            "failed",
            None,
            {
                "reason": (
                    "authentication failed: TOKEN=example-private-value "
                    "Authorization: Bearer example-bearer"
                ),
                "error": "https://user:example-password@github.com/repo ghp_examplecredential "
                "Authorization: Basic example-basic",
            },
        ),
    )
    controller.deliver_reports(claim.id)
    body = "\n".join(comments.posted)
    for secret in (
        "example-private-value",
        "example-bearer",
        "example-password",
        "ghp_examplecredential",
        "example-basic",
    ):
        assert secret not in body
    assert "authentication failed" in body
    assert "[redacted]" in body
    store.close()
