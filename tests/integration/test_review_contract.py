"""Review-loop contracts for factory fix claims."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_factory.github import IssueComment, ReviewActivity, ReviewThread
from agent_factory.work_kinds.pull_request import launch
from agent_factory.work_kinds.pull_request.outcome import read_outcome
from agent_factory.work_kinds.pull_request.review import eligible_review_activity


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
    assert "--param base_head=" not in exec_line


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


def _review_file(tmp_path: Path) -> Path:
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {
                "reviews": [{"id": "r1"}],
                "threads": [{"id": "t1", "comments": [{"id": "tc1"}]}],
                "comments": [{"id": "c1"}],
            }
        )
    )
    return path


def _triage(tmp_path: Path, ids: list[str], needs_input: list[str] | None = None) -> int:
    decision = {
        "needs_input": needs_input or [],
        "items": [{"id": identifier, "decision": "answer"} for identifier in ids],
    }
    payload: dict[str, object] = {
        "decision": json.dumps(decision),
        "review_file": str(_review_file(tmp_path)),
    }
    return _script("record-review-triage.sh", payload).returncode


def test_record_review_triage_requires_one_decision_per_review_item(tmp_path: Path) -> None:
    assert _triage(tmp_path, ["r1", "t1", "c1"]) == 0
    assert _triage(tmp_path, []) == 2
    assert _triage(tmp_path, ["r1", "t1"]) == 2
    assert _triage(tmp_path, ["r1", "t1", "c1", "c1"]) == 2
    assert _triage(tmp_path, ["r1", "t1", "c1", "tc1"]) == 2
    # A needs-input decline posts no replies, so it need not decide every item.
    assert _triage(tmp_path, [], needs_input=["pick A or B"]) == 0


def test_review_workflow_passes_the_review_file_to_both_triage_gates() -> None:
    text = launch.packaged_workflow_text(launch.REVIEW_CONTRACT)

    gates = [step for step in text.split("\n  - id: ") if "script: record-review-triage.sh" in step]
    assert len(gates) == 2
    assert all('review_file: "{{review_file}}"' in step for step in gates)


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


def run_review_description(
    tmp_path: Path, mode: str, kind: str, body: Path, edit_fails: bool = False, view: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run review-description.sh with a gh stub whose PR body lives in ``body``."""
    import os

    staged = launch.stage_workflow(tmp_path / "stage", "factory-review/1")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": kind, "pull_request": {"number": 4}}))
    stub = tmp_path / "bin" / "gh"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text(
        # Like gh: "-q .body" prints the body plus a newline; "--json body" prints JSON.
        '#!/bin/sh\nif [ -n "$VIEW" ] && [ "$2" = view ]; then echo "$VIEW"\n'
        'elif [ "$2" = view ]; then\n'
        '  case "$*" in *" -q "*) cat "$PR_BODY"; echo ;;\n'
        "  *) python3 -c 'import json,sys; "
        'print(json.dumps({"body": open(sys.argv[1], newline="").read()}))\''
        ' "$PR_BODY" ;; esac\n'
        # `gh pr edit` reads project items, which the factory's token cannot.
        'elif [ "$2" = edit ]; then echo "GraphQL: Resource not accessible" >&2; exit 1\n'
        'elif [ "$1" = api ]; then\n'
        '  [ -z "$EDIT_FAILS" ] || { echo "HTTP 403: denied" >&2; exit 1; }\n'
        '  for arg; do case "$arg" in body=@*) cat "${arg#body=@}" > "$PR_BODY" ;; esac; done\n'
        "fi\n"
    )
    stub.chmod(0o755)
    result = subprocess.run(
        [str(staged / "review-description.sh")],
        input=json.dumps({"mode": mode, "review_file": str(review), "artifact_dir": str(tmp_path)}),
        env={
            **os.environ,
            "PATH": f"{stub.parent}:{os.environ['PATH']}",
            "PR_BODY": str(body),
            "EDIT_FAILS": "1" if edit_fails else "",
            "VIEW": view,
        },
        text=True,
        capture_output=True,
    )
    assert edit_fails or result.returncode == 0, result.stderr
    return result


@pytest.mark.parametrize(("kind", "restored"), [("feature", True), ("fix", False)])
def test_review_round_keeps_the_factory_owned_feature_description(
    tmp_path: Path, kind: str, restored: bool
) -> None:
    """Finalization may rewrite the description; a feature PR's annotated layout is
    restored after the round, and a fix PR's description is left as finalization wrote it."""
    body = tmp_path / "body.md"
    annotated = "**Feature for #2:** Add cleanup\n\n# Review first\n"
    body.write_text(annotated)
    run_review_description(tmp_path, "save", kind, body)
    rewritten = "## Review update\n\n" + annotated + "\n----\nabc123 commit\n"
    body.write_text(rewritten)
    run_review_description(tmp_path, "restore", kind, body)
    assert (body.read_text() == annotated) is restored
    overwritten = tmp_path / "pr-description-overwritten.md"
    assert (overwritten.read_text() == rewritten) if restored else not overwritten.exists()


def test_a_failed_description_restore_is_recorded_for_the_completion_comment(
    tmp_path: Path,
) -> None:
    body = tmp_path / "body.md"
    body.write_text("**Feature for #2:** Add cleanup\n\n# Review first\n")
    run_review_description(tmp_path, "save", "feature", body)
    body.write_text("rewritten by finalization\n")
    result = run_review_description(tmp_path, "restore", "feature", body, edit_fails=True)
    assert result.returncode != 0
    recorded = (tmp_path / "description-restore-failed").read_text()
    assert "pull request #4" in recorded and "HTTP 403: denied" in recorded
    text = launch.packaged_workflow_text(launch.REVIEW_CONTRACT)
    respond = text.split("  - id: respond\n", 1)[1].split("\n  - id: ")[0]
    assert "description-restore-failed" in respond
    save = text.split("  - id: save-description\n", 1)[1].split("\n  - id: ")[0]
    assert "continue_on_failure: true" in save


def test_an_unchanged_crlf_description_is_left_alone(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_bytes(b"**Feature for #2:** Add cleanup\r\n\r\n# Review first\r\n")
    run_review_description(tmp_path, "save", "feature", body)
    run_review_description(tmp_path, "restore", "feature", body)
    assert not (tmp_path / "pr-description-overwritten.md").exists()
    assert body.read_bytes() == b"**Feature for #2:** Add cleanup\r\n\r\n# Review first\r\n"


def test_unreadable_gh_output_is_recorded_as_a_failed_restore(tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("# Review first\n")
    run_review_description(tmp_path, "save", "feature", body)
    result = run_review_description(
        tmp_path, "restore", "feature", body, edit_fails=True, view="warning: not JSON"
    )
    assert result.returncode != 0
    assert (tmp_path / "description-restore-failed").is_file()


@pytest.mark.parametrize(
    ("validator", "ci", "expected"),
    [
        ("passed", "passed", "pull-request"),
        ("failed", "", "failed"),
        ("passed", "failed", "failed"),
    ],
)
def test_merge_only_review_maps_implementation_result(
    tmp_path: Path, validator: str, ci: str, expected: str
) -> None:
    result_path = tmp_path / "implement-result.json"
    result_path.write_text(
        json.dumps({"validator": {"status": validator}, "ci": {"status": ci}, "head_sha": "abc"})
    )
    outcome_path = tmp_path / "review-outcome.json"
    done = _script(
        "record-review-outcome.sh",
        {
            "decision": json.dumps(
                {"needs_input": [], "items": [{"id": "t1", "decision": "answer"}]}
            ),
            "changes_needed": "false",
            "merge_status": "merged",
            "result_path": str(result_path),
            "outcome_path": str(outcome_path),
        },
    )
    assert done.returncode == 0, done.stderr
    outcome = json.loads(outcome_path.read_text())
    assert outcome["outcome"] == expected
    assert outcome["answered"] == ["t1"]


def test_review_merge_no_base_is_a_noop(tmp_path: Path) -> None:
    from tests.integration.test_feature_workflow_scripts import repository, run

    repo, _ = repository(tmp_path)
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": "fix"}))
    evidence = tmp_path / "evidence"
    result = run(
        str(Path(launch.__file__).parent / "workflow/review-merge-base.sh"),
        str(review),
        str(evidence),
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "none"
    assert not (evidence / "base-merge.json").exists()


def test_review_merge_stop_does_not_push_branch(tmp_path: Path) -> None:
    from tests.integration.test_feature_workflow_scripts import git, repository, run

    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "review")
    (repo / "choice.txt").write_text("review\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "review change")
    previous = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-u", "origin", "review")
    git(repo, "checkout", "main")
    (repo / "choice.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base change")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "review")
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": "feature", "base_head": base}))
    evidence = tmp_path / "evidence"
    script = str(Path(launch.__file__).parent / "workflow/review-merge-base.sh")
    merged = run(script, str(review), str(evidence), cwd=repo)
    assert merged.returncode == 0, merged.stderr
    assert merged.stdout == "conflict"
    (evidence / "merge-stop.json").write_text(
        json.dumps(
            {
                "questions": ["Choose a value"],
                "direction_summary": "Need direction",
            }
        )
    )
    stopped = run(
        str(Path(launch.__file__).parent / "workflow/record-review-merge-stop.sh"),
        str(evidence),
        cwd=repo,
    )
    assert stopped.returncode == 0, stopped.stderr
    assert git(repo, "rev-parse", "HEAD") == previous
    assert git(remote, "rev-parse", "refs/heads/review") == previous
    outcome = json.loads((evidence / "review-outcome.json").read_text())
    assert outcome["outcome"] == "needs-input"
    assert outcome["answered"] == outcome["changed"] == []
    assert "choice.txt" in outcome["reasons"][0]
