"""Review-loop contracts for factory fix claims."""

from __future__ import annotations

import json
from pathlib import Path

from agent_factory.github import IssueComment, ReviewActivity, ReviewThread
from agent_factory.work_kinds.fix import launch
from agent_factory.work_kinds.fix.outcome import read_outcome
from agent_factory.work_kinds.fix.review import eligible_review_activity


def test_eligible_review_activity_keeps_writer_pr_comments_after_checkpoint() -> None:
    activity = ReviewActivity(
        reviews=(IssueComment("review-1", "please change this", "writer", "2026-02-02T00:00:00Z"),),
        threads=(
            ReviewThread(
                "thread-1",
                False,
                "src/example.py",
                8,
                (IssueComment("inline-1", "edge case", "writer", "2026-02-02T00:01:00Z"),),
            ),
            ReviewThread(
                "thread-2",
                True,
                "src/old.py",
                1,
                (IssueComment("old", "already handled", "writer", "2026-02-02T00:02:00Z"),),
            ),
        ),
        comments=(
            IssueComment("comment-1", "why this approach?", "writer", "2026-02-02T00:03:00Z"),
        ),
    )

    eligible = eligible_review_activity(
        activity,
        since="2026-02-01T00:00:00+00:00",
        bot_login="factory[bot]",
        permission=lambda login: "write" if login == "writer" else None,
    )

    assert eligible == {
        "reviews": [
            {
                "id": "review-1",
                "author": "writer",
                "body": "please change this",
                "created_at": "2026-02-02T00:00:00Z",
            }
        ],
        "threads": [
            {
                "id": "thread-1",
                "path": "src/example.py",
                "line": 8,
                "comments": [
                    {
                        "id": "inline-1",
                        "author": "writer",
                        "body": "edge case",
                        "created_at": "2026-02-02T00:01:00Z",
                    }
                ],
            }
        ],
        "comments": [
            {
                "id": "comment-1",
                "author": "writer",
                "body": "why this approach?",
                "created_at": "2026-02-02T00:03:00Z",
            }
        ],
    }


def test_review_workflow_and_shared_implementation_are_staged(tmp_path: Path) -> None:
    staged = launch.stage_workflow(tmp_path, "factory-review/1")

    assert (staged / "factory-review-v1.0.yaml").read_text().splitlines()[
        0
    ] == "# factory-contract: factory-review/1"
    assert (staged / "factory-implement-v1.0.yaml").is_file()
    assert (staged / "record-review-triage.sh").is_file()


def test_review_outcome_uses_the_review_contract_and_filename(tmp_path: Path) -> None:
    (tmp_path / "review-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-review/1",
                "outcome": "pull-request",
                "answered": ["thread-1"],
                "changed": [],
            }
        )
    )

    assert read_outcome(tmp_path, "factory-review/1") == {
        "contract": "factory-review/1",
        "outcome": "pull-request",
        "answered": ["thread-1"],
        "changed": [],
    }
