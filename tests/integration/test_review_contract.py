"""Review-loop contracts for factory fix claims."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

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


def test_review_workflow_declares_artifact_dir_and_no_container_path() -> None:
    text = launch.check_packaged_workflow("factory-review/1")

    assert "- name: artifact_dir" in text
    assert "review-outcome.json" in text
    assert "factory-implement-v1.0.yaml" in text


def test_host_wrapper_runs_the_review_workflow_with_its_input_file(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    script = launch.host_script(
        runner="/opt/agent-runner",
        repo_clone=tmp_path / "repo",
        evidence=evidence,
        credential_copy=tmp_path / "fix.env",
        gitconfig=tmp_path / "gitconfig",
        askpass=tmp_path / "askpass.sh",
        branch="factory/fix-7-claim",
        contract="factory-review/1",
    )
    exec_line = next(line for line in script.splitlines() if " run " in line)

    assert exec_line.startswith("/opt/agent-runner run factory-review ")
    assert f"--param review_file={evidence / 'input' / 'review.json'}" in exec_line
    assert f"--param artifact_dir={evidence}" in exec_line
    assert "--param contract_version=factory-review/1" in exec_line


def test_container_script_runs_the_review_workflow_from_the_artifacts_mount() -> None:
    script = launch.container_script(
        {"lead": ("cursor", "model", "high")},
        branch="factory/fix-7-claim",
        contract="factory-review/1",
        bootstrap_skills=False,
    )

    assert "agent-runner run factory-review --profile factory" in script
    assert "--param review_file=/artifacts/input/review.json" in script


def _script(name: str, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    package = Path(launch.__file__).parent / "workflow" / name
    return subprocess.run(
        ["sh", str(package)], input=json.dumps(payload), capture_output=True, text=True
    )


@pytest.mark.parametrize(
    ("decision", "token"),
    [
        ({"needs_input": ["pick A or B"], "items": []}, "needs-input"),
        (
            {"needs_input": [], "items": [{"id": "t1", "decision": "change", "plan": "x"}]},
            "true",
        ),
        ({"needs_input": [], "items": [{"id": "t1", "decision": "answer"}]}, "false"),
    ],
)
def test_record_review_triage_reduces_the_decision_to_one_token(
    decision: dict[str, object], token: str
) -> None:
    done = _script("record-review-triage.sh", {"decision": "Sure!\n" + json.dumps(decision) + "\n"})

    assert done.returncode == 0, done.stderr
    assert done.stdout == token


def test_record_review_triage_rejects_a_missing_decision() -> None:
    done = _script("record-review-triage.sh", {"decision": "no json here"})

    assert done.returncode == 2


def test_record_review_outcome_maps_answers_changes_and_needs_input(tmp_path: Path) -> None:
    outcome_path = tmp_path / "review-outcome.json"
    result_path = tmp_path / "implement-result.json"
    items = [
        {"id": "t1", "decision": "answer"},
        {"id": "t2", "decision": "change", "plan": "x"},
    ]

    done = _script(
        "record-review-outcome.sh",
        {
            "decision": json.dumps({"needs_input": [], "items": items}),
            "changes_needed": "true",
            "result_path": str(result_path),
            "outcome_path": str(outcome_path),
        },
    )
    assert done.returncode == 0, done.stderr
    written = json.loads(outcome_path.read_text())
    assert written["outcome"] == "failed"
    assert written["changed"] == ["t2"] and written["answered"] == ["t1"]

    result_path.write_text(
        json.dumps(
            {"validator": {"status": "passed"}, "ci": {"status": "passed"}, "head_sha": "abc"}
        )
    )
    done = _script(
        "record-review-outcome.sh",
        {
            "decision": json.dumps({"needs_input": [], "items": items}),
            "changes_needed": "true",
            "result_path": str(result_path),
            "outcome_path": str(outcome_path),
        },
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(outcome_path.read_text())["outcome"] == "pull-request"

    done = _script(
        "record-review-outcome.sh",
        {
            "decision": json.dumps({"needs_input": ["choose"], "items": []}),
            "changes_needed": "needs-input",
            "result_path": str(result_path),
            "outcome_path": str(outcome_path),
        },
    )
    assert done.returncode == 0, done.stderr
    written = json.loads(outcome_path.read_text())
    assert written["outcome"] == "needs-input" and written["reasons"] == ["choose"]
