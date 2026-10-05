import json
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.github import GitHubApiError, PullRequestInfo
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler, feature_resume_point
from agent_factory.work_kinds.pull_request.kinds import FEATURE
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome


def test_feature_outcome_accepts_well_formed_extra_fields(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "needs-input",
                "reasons": ["choose"],
                "stopped_step": "design",
                "questions": ["choose"],
                "direction_summary": "Drafted a plan",
                "branch": "claim",
                "review_attention_counts": {"red": 1, "orange": 0, "yellow": 2},
                "resume": {"from": "design"},
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is not None


def test_feature_outcome_rejects_wrong_extra_field_type(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "needs-input",
                "stopped_step": 42,
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_blocked_archive_resumes_at_archive() -> None:
    assert feature_resume_point("needs-input", "archive", "implemented", False, False) == "archive"


@pytest.mark.parametrize("close_error", [False, True])
def test_continuation_closes_prior_draft_pr_once_and_preserves_handoff(
    tmp_path: Path, close_error: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    prior = store.create_claim(ClaimDraft("example/work", 103, "I103", "P103", "feature", "fp", {}))
    store.set_claim_lifecycle(prior.id, "settled", {"verdict": "infra-error"})
    claim = store.create_claim(ClaimDraft("example/work", 103, "I103", "P103", "feature", "fp", {}))
    handler = PullRequestHandler(
        FEATURE,
        SharedConfig.from_file(Path("config/codagent.toml")),
        LocalConfig.from_file(Path("config/local.example.toml")),
    )
    handler.attach_store(store)
    prior_branch = handler.branch_name(prior)
    current_branch = handler.branch_name(claim)
    store.set_preparation(claim.id, {"continuation_head": "a" * 40, "prior_branch": prior_branch})
    comments: list[tuple[int, str]] = []
    closed: list[int] = []

    class GitHub:
        def list_open_pull_requests_for_head(
            self, repository: str, branch: str
        ) -> list[PullRequestInfo]:
            assert repository == "example/work"
            assert branch == prior_branch
            return [PullRequestInfo("https://example.test/pr/105", 105, "a" * 40, True)]

        def create_comment(self, repository: str, number: int, body: str) -> str:
            comments.append((number, body))
            return "1"

        def close_pull_request(self, repository: str, number: int) -> None:
            closed.append(number)
            if close_error:
                raise GitHubApiError("close failed")

    handler.attach_github(GitHub())  # type: ignore[arg-type]
    run = store.reserve_run(claim.id, "feature", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "pr": {"number": 114, "url": "https://example.test/pr/114"},
        },
    )
    runs = store.runs_for_claim(claim.id)
    result = handler.settle(claim, runs)
    assert result is not None and result.verdict == "pending-human-review"
    assert "https://example.test/pr/114" in result.event_body
    assert closed == [105]
    assert len(comments) == 1
    assert comments[0][0] == 105
    assert "https://example.test/pr/114" in comments[0][1]
    assert current_branch in comments[0][1]
    if close_error:
        assert any(
            event.key == "superseded-pr-error:105"
            and "https://example.test/pr/105" in event.body
            and "close failed" in event.body
            for event in store.pending_events(claim.id)
        )
    handler.settle(claim, runs)
    assert len(comments) == 1


@pytest.mark.parametrize(
    ("reason", "has_prior"), [("initial", False), ("initial", True), ("review", True)]
)
def test_new_feature_or_review_round_does_not_close_any_pr(
    tmp_path: Path, reason: str, has_prior: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    if has_prior:
        store.create_claim(ClaimDraft("example/work", 103, "I103", "P103", "feature", "fp", {}))
    claim = store.create_claim(ClaimDraft("example/work", 103, "I103", "P103", "feature", "fp", {}))
    handler = PullRequestHandler(
        FEATURE,
        SharedConfig.from_file(Path("config/codagent.toml")),
        LocalConfig.from_file(Path("config/local.example.toml")),
    )
    handler.attach_store(store)

    class GitHub:
        def list_open_pull_requests_for_head(
            self, repository: str, branch: str
        ) -> list[PullRequestInfo]:
            raise AssertionError("prior branches should not be queried")

    handler.attach_github(GitHub())  # type: ignore[arg-type]
    run = store.reserve_run(claim.id, "feature", reason=reason, evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "pr": {"number": 114, "url": "https://example.test/pr/114"},
        },
    )
    result = handler.settle(claim, store.runs_for_claim(claim.id))
    assert result is not None and result.verdict == "pending-human-review"
