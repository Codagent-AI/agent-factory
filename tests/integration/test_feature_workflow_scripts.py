"""Real git boundaries for the packaged feature workflow helpers."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from agent_factory.work_kinds.pull_request.kinds import FEATURE_STAGED_FILES
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome
from tests.integration.test_fix_workflow import shell_templates, single_quoted_placeholders

PACKAGE = files("agent_factory.work_kinds.pull_request") / "workflow"


def run(*args: str, cwd: Path, input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, input=input, text=True, capture_output=True)


def git(repo: Path, *args: str) -> str:
    result = run("git", *args, cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "openspec").mkdir()
    (repo / "openspec" / ".keep").touch()
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    return repo, remote


# Like gh with a token that cannot read org Projects: `gh pr edit` fails because it
# reads the pull request's project items, while the REST update writes the body.
GH_STUB = (
    '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = list ]; then\n'
    '  echo \'[{"number":9,"url":"https://github.com/o/r/pull/9"}]\'\n'
    'elif [ "$1" = pr ] && [ "$2" = edit ]; then\n'
    "  echo 'GraphQL: Resource not accessible by personal access token"
    " (repository.pullRequest.projectItems)' >&2; exit 1\n"
    'elif [ "$1" = api ]; then\n'
    '  case "$*" in\n'
    # By default CodeRabbit reviewed the final head and left no comments.
    '    */reviews*) [ -n "${GH_REVIEWS:-}" ] && { printf %s "$GH_REVIEWS"; exit 0; }\n'
    '      printf \'[[{"user":{"login":"coderabbitai[bot]"},"commit_id":"%s"}]]\' '
    '"$(git rev-parse HEAD)"; exit 0 ;;\n'
    '    */statuses*) printf %s "${GH_STATUSES:-[]}"; exit 0 ;;\n'
    "  esac\n"
    '  [ -z "${GH_API_ERROR:-}" ] || { echo "$GH_API_ERROR" >&2; exit 1; }\n'
    '  for arg; do case "$arg" in body=@*) cat "${arg#body=@}" > "$GH_BODY" ;; esac; done\n'
    "fi\n"
)


def test_feature_files_are_listed_and_exist() -> None:
    for name in (
        "factory-feature-v1.0.yaml",
        "factory-define-v1.0.yaml",
        "factory-define-rules.md",
        "prepare-branch.sh",
        "factory-resume-skip.sh",
        "record-stop.sh",
        "record-archive-block.sh",
        "annotate-pr.sh",
    ):
        assert name in FEATURE_STAGED_FILES
        assert (PACKAGE / name).is_file()


def test_resume_skip_order() -> None:
    script = str(PACKAGE / "factory-resume-skip.sh")
    repo = Path.cwd()
    assert run(script, "", "proposal", cwd=repo).returncode != 0
    assert run(script, "implement", "write-tasks", cwd=repo).returncode == 0
    assert run(script, "implement", "implement", cwd=repo).returncode != 0
    assert run(script, "implement", "verify", cwd=repo).returncode != 0


def test_reconcile_runs_for_definition_resumes_and_continuations_only() -> None:
    script = str(PACKAGE / "reconcile-skip.sh")
    repo = Path.cwd()
    # Exit 1 runs reconcile-artifacts; exit 0 skips it.
    for step in ("proposal", "specs", "write-tasks"):
        assert run(script, step, "", cwd=repo).returncode == 1
    for step in ("", "implement", "archive", "verify", "finalize"):
        assert run(script, step, "", cwd=repo).returncode == 0
    # A continuation revises the prior plan before implementing it, but not when the
    # prior change was already archived and the continuation resumes at verification.
    assert run(script, "implement", "factory/feature-1-prior", cwd=repo).returncode == 1
    assert run(script, "", "factory/feature-1-prior", cwd=repo).returncode == 1
    assert run(script, "verify", "factory/feature-1-prior", cwd=repo).returncode == 0


def test_prepare_branch_fresh_resume_continue_and_missing_fallback(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    script = str(PACKAGE / "prepare-branch.sh")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    fresh = run(script, "claim-one", head, "", "", str(evidence), cwd=repo)
    assert fresh.returncode == 0
    assert fresh.stdout == ""
    assert git(repo, "branch", "--show-current") == "claim-one"
    (repo / "plan").write_text("plan")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "plan")
    git(repo, "push", "-u", "origin", "claim-one")
    git(repo, "checkout", "main")
    resumed = run(script, "claim-one", head, "implement", "", str(evidence), cwd=repo)
    assert resumed.returncode == 0
    assert resumed.stdout == "implement"
    assert (repo / "plan").exists()
    git(repo, "checkout", "main")
    continued = run(script, "claim-two", head, "implement", "claim-one", str(evidence), cwd=repo)
    assert continued.returncode == 0
    assert continued.stdout == "implement"
    assert (repo / "plan").exists()
    git(repo, "checkout", "main")
    missing = run(script, "claim-three", head, "implement", "missing", str(evidence), cwd=repo)
    assert missing.returncode == 0
    assert missing.stdout == ""
    assert git(repo, "branch", "--show-current") == "claim-three"
    assert json.loads((evidence / "resume.json").read_text())["fallback"]


def test_record_stop_pushes_draft_and_writes_outcome(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    (repo / "openspec" / "draft.md").write_text("draft")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "define-stop.json").write_text(
        json.dumps(
            {"step": "design", "questions": ["Which API?"], "direction_summary": "Drafted proposal"}
        )
    )
    result = run(str(PACKAGE / "record-stop.sh"), str(evidence), "claim", cwd=repo)
    assert result.returncode == 0, result.stderr
    assert "[define-stop]" in git(repo, "log", "-1", "--format=%s")
    assert git(remote, "rev-parse", "refs/heads/claim") == git(repo, "rev-parse", "HEAD")
    outcome = json.loads((evidence / "feature-outcome.json").read_text())
    assert outcome["outcome"] == "needs-input"
    assert outcome["stopped_step"] == "design"
    assert outcome["questions"] == ["Which API?"]
    assert read_interpreted_outcome(evidence, "factory-feature/1").outcome is not None


def test_record_archive_block_preserves_explanation_and_branch(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    explanation = "Main spec contains a stray delta header outside the change directory."
    (session / "audit.log").write_text(
        "2026-09-28T07:49:55.844297Z [archive, sub:archive-change, archive-transition] "
        "repair_blocked "
        + json.dumps({"attempt": 0, "response": explanation + "\n\nREPAIR_BLOCKED"})
        + "\n"
        + '[archive, sub:archive-change, archive-transition] step_end {"exit_code":1}\n'
        + '[archive, sub:archive-change] sub_workflow_end {"outcome":"failed"}\n'
        + '[archive] step_end {"outcome":"failed"}\n'
    )
    payload = json.dumps(
        {
            "artifact_dir": str(evidence),
            "branch_name": "factory/feature-12",
            "session_dir": str(session),
        }
    )
    result = run(str(PACKAGE / "record-archive-block.sh"), cwd=repo, input=payload)
    assert result.returncode == 0, result.stderr
    outcome = json.loads((evidence / "feature-outcome.json").read_text())
    assert outcome["outcome"] == "needs-input"
    assert outcome["stopped_step"] == "archive"
    assert outcome["reasons"] == outcome["questions"] == [explanation]
    assert outcome["branch"] == "factory/feature-12"
    assert "REPAIR_BLOCKED" not in outcome["direction_summary"]
    assert "commit the fix to this branch" in outcome["direction_summary"]
    assert "Fix it on the target branch" in outcome["direction_summary"]
    assert "a fix merged to main does not reach it" not in outcome["direction_summary"]
    assert read_interpreted_outcome(evidence, "factory-feature/1").outcome is not None
    assert (
        run(
            "python3",
            str(PACKAGE / "verify-feature-outcome.py"),
            str(evidence / "feature-outcome.json"),
            cwd=repo,
        ).returncode
        == 0
    )


def test_record_archive_block_requires_archive_declaration(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    (session / "audit.log").write_text(
        "2026-09-28T07:49:55Z [implement, sub:implement-task, verify-task-commit] "
        'repair_blocked {"response":"wrong step\\nREPAIR_BLOCKED"}\n'
    )
    result = run(
        str(PACKAGE / "record-archive-block.sh"), str(evidence), "branch", str(session), cwd=repo
    )
    assert result.returncode != 0
    assert "archive step failed without a REPAIR_BLOCKED declaration" in result.stderr
    assert not (evidence / "feature-outcome.json").exists()
    (session / "audit.log").unlink()
    missing_log = run(
        str(PACKAGE / "record-archive-block.sh"), str(evidence), "branch", str(session), cwd=repo
    )
    assert missing_log.returncode != 0
    assert "archive step failed without a REPAIR_BLOCKED declaration" in missing_log.stderr
    assert "Traceback" not in missing_log.stderr


def test_record_archive_block_uses_last_archive_declaration_from_commit_check(
    tmp_path: Path,
) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    (session / "audit.log").write_text(
        "2026-09-28T07:49:55Z [archive, sub:archive-change, archive-transition] "
        'repair_blocked {"response":"old reason\\nREPAIR_BLOCKED"}\n'
        "2026-09-28T07:50:00Z [archive, sub:archive-change, verify-archive-commit] "
        'repair_blocked {"response":"Commit needs a human decision.\\nREPAIR_BLOCKED"}\n'
    )
    result = run(
        str(PACKAGE / "record-archive-block.sh"), str(evidence), "branch", str(session), cwd=repo
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((evidence / "feature-outcome.json").read_text())["reasons"] == [
        "Commit needs a human decision."
    ]


def test_record_archive_block_rejects_stale_declaration_after_later_failure(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    (session / "audit.log").write_text(
        "[archive, sub:archive-change, archive-transition] "
        'repair_blocked {"response":"old reason\\nREPAIR_BLOCKED"}\n'
        '[archive, sub:archive-change, archive-transition] step_end {"exit_code":1}\n'
        "[archive, sub:archive-change, verify-archive-commit] step_start {}\n"
        '[archive, sub:archive-change, verify-archive-commit] step_end {"exit_code":1}\n'
        '[archive, sub:archive-change] sub_workflow_end {"outcome":"failed"}\n'
    )
    result = run(
        str(PACKAGE / "record-archive-block.sh"), str(evidence), "branch", str(session), cwd=repo
    )
    assert result.returncode != 0
    assert "archive step failed without a REPAIR_BLOCKED declaration" in result.stderr
    assert not (evidence / "feature-outcome.json").exists()


def test_record_archive_block_reports_invalid_declaration_payload(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    session = tmp_path / "session"
    session.mkdir()
    for payload in ('{"response":', '{"response":}', "[]"):
        (session / "audit.log").write_text(
            f"[archive, sub:archive-change, archive-transition] repair_blocked {payload}\n"
        )
        result = run(
            str(PACKAGE / "record-archive-block.sh"),
            str(evidence),
            "branch",
            str(session),
            cwd=repo,
        )
        assert result.returncode != 0
        assert "archive step failed without a REPAIR_BLOCKED declaration" in result.stderr
        assert "Traceback" not in result.stderr
        assert not (evidence / "feature-outcome.json").exists()


def test_archive_block_steps_precede_push_and_keep_status_defined() -> None:
    feature = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    steps = feature.split("\n  - id: ")
    ids = [step.split("\n", 1)[0] for step in steps[1:]]
    archive = steps[ids.index("archive") + 1]
    record = steps[ids.index("record-archive-block") + 1]
    assert "continue_on_failure: true" in archive
    assert "skip_if: 'sh: test \"{{archive_status}}\" = skipped'" in archive
    assert ids.index("seed-archive-status") < ids.index("archive")
    assert ids.index("archive") < ids.index("mark-archive-failed")
    assert "restore-skipped-archive-status" not in ids
    assert ids.index("mark-archive-failed") < ids.index("record-archive-block")
    assert ids.index("record-archive-block") < ids.index("push-archive")
    assert "script: record-archive-block.sh" in record


def test_feature_workflow_shell_placeholders_are_interpolatable() -> None:
    """Runner rejects placeholders within shell single quotes at step execution time."""
    feature = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    refused = {
        template.strip().splitlines()[0]: names
        for template in shell_templates(feature)
        if (names := single_quoted_placeholders(template))
    }
    assert refused == {}


def test_archive_status_survives_change_directory_moving_after_failure(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    change = repo / "openspec" / "changes" / "example"
    change.mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    skip_script = repo / ".agent-runner" / "workflows" / "factory-resume-skip.sh"
    skip_script.parent.mkdir(parents=True)
    skip_script.write_text("#!/bin/sh\nexit 1\n")
    skip_script.chmod(0o755)
    feature = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    seed = feature.split("  - id: seed-archive-status\n", 1)[1].split("\n  - id: ")[0]
    mark = feature.split("  - id: mark-archive-failed\n", 1)[1].split("\n  - id: ")[0]
    seed_command = seed.split("    command: ", 1)[1].split("\n", 1)[0]
    seed_command = (
        seed_command.replace("{{artifact_dir}}", str(evidence))
        .replace("{{change_name}}", "example")
        .replace("{{effective_resume}}", "archive")
    )
    seeded = run("sh", "-c", seed_command, cwd=repo)
    assert seeded.returncode == 0 and seeded.stdout == "passed"
    change.rmdir()  # archive-transition can move the change before commit verification fails.
    mark_command = mark.split("    command: ", 1)[1].split("\n", 1)[0]
    marked = run("sh", "-c", mark_command.replace("{{archive_status}}", seeded.stdout), cwd=repo)
    assert marked.returncode == 0 and marked.stdout == "failed"
    skipped = run("sh", "-c", seed_command, cwd=repo)
    assert skipped.returncode == 0 and skipped.stdout == "skipped"
    preserved = run(
        "sh", "-c", mark_command.replace("{{archive_status}}", skipped.stdout), cwd=repo
    )
    assert preserved.returncode == 0 and preserved.stdout == "skipped"


def test_record_stop_records_the_workflow_step_not_the_agents_name(tmp_path: Path) -> None:
    """The resume point comes from the workflow: an agent may name its step loosely."""
    repo, _ = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "define-stop.json").write_text(
        json.dumps({"step": "propose", "questions": ["A or B?"], "direction_summary": "Draft"})
    )
    payload = json.dumps(
        {"artifact_dir": str(evidence), "branch_name": "claim", "step": "proposal"}
    )
    result = run(str(PACKAGE / "record-stop.sh"), cwd=repo, input=payload)
    assert result.returncode == 0, result.stderr
    assert json.loads((evidence / "feature-outcome.json").read_text())["stopped_step"] == "proposal"


def test_record_stop_falls_back_to_the_agents_step_when_the_workflow_names_none(
    tmp_path: Path,
) -> None:
    repo, _ = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "define-stop.json").write_text(
        json.dumps({"step": "design", "questions": ["A or B?"], "direction_summary": "Draft"})
    )
    payload = json.dumps({"artifact_dir": str(evidence), "branch_name": "claim", "step": None})
    result = run(str(PACKAGE / "record-stop.sh"), cwd=repo, input=payload)
    assert result.returncode == 0, result.stderr
    assert json.loads((evidence / "feature-outcome.json").read_text())["stopped_step"] == "design"


def test_every_definition_stop_records_the_resume_key_of_its_step() -> None:
    """Each record-stop step names the key that factory-resume-skip.sh resumes at, the one
    the lead step before it skips on; reconciliation stops resume where the run resumed."""
    import re

    define = (PACKAGE / "factory-define-v1.0.yaml").read_text()
    steps = re.split(r"\n  - id: ", "\n" + define.split("\nsteps:\n", 1)[1])[1:]
    resume_key = ""
    recorded = 0
    for step in steps:
        key = re.search(r'factory-resume-skip.sh "{{resume_from}}" ([\w-]+)', step)
        if key:
            resume_key = key.group(1)
        if step.startswith("record-stop"):
            assert f'step: "{resume_key}"' in step or f"step: {resume_key}\n" in step, step
            recorded += 1
    assert recorded == 9
    feature = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    reconcile = feature.split("  - id: record-stop-reconcile\n", 1)[1].split("\n  - id: ")[0]
    assert 'step: "{{effective_resume}}"' in reconcile


def test_record_stop_refuses_incomplete_decision_without_push(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    stops: tuple[dict[str, object], ...] = (
        {"step": "design", "questions": [], "direction_summary": "draft"},
        {"step": "design", "questions": [""], "direction_summary": "draft"},
        {"step": "design", "questions": ["Which API?"], "direction_summary": ""},
        {"step": "", "questions": ["Which API?"], "direction_summary": "draft"},
    )
    for stop in stops:
        (evidence / "define-stop.json").write_text(json.dumps(stop))
        result = run(str(PACKAGE / "record-stop.sh"), str(evidence), "claim", cwd=repo)
        assert result.returncode != 0
        assert not (evidence / "feature-outcome.json").exists()
        assert run("git", "rev-parse", "refs/heads/claim", cwd=remote).returncode != 0


def test_annotate_pr_orders_tiers_and_adds_later_commits(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "later").write_text("fix")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix CI")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [
                    {
                        "title": "Unverified",
                        "detail": "criterion",
                        "link": "https://example.test/red",
                    }
                ],
                "orange": [],
                "yellow": [
                    {
                        "title": "Assumption",
                        "detail": "choice",
                        "link": "https://example.test/yellow",
                    }
                ],
                "white": [],
                "accepted_head": accepted,
                "later_commits": [],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body = tmp_path / "body.md"
    import os

    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_BODY": str(body)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = body.read_text()
    assert rendered.index("🔴") < rendered.index("🟠") < rendered.index("🟡")
    assert "Closes #7" in rendered and "agent-factory:claim:claim-7" in rendered
    assert "<details>" in rendered and accepted in rendered
    assert "fix CI" in rendered
    flags = json.loads((evidence / "review-attention.json").read_text())
    assert len(flags["orange"]) == 1
    assert len(flags["later_commits"]) == 1


def test_annotate_pr_flags_listed_later_commit_no_orange_item_names(tmp_path: Path) -> None:
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "later").write_text("fix")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix CI")
    later = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    attention: dict[str, object] = {
        "red": [],
        "orange": [],
        "yellow": [],
        "white": [],
        "accepted_head": accepted,
        "later_commits": [later],
    }
    (evidence / "review-attention.json").write_text(json.dumps(attention))
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GH_BODY": str(tmp_path / "body.md"),
    }
    command = [
        str(PACKAGE / "annotate-pr.sh"),
        str(evidence),
        str(issue),
        "change",
        "openspec/changes/archive/2026-09-25-change",
    ]
    for _ in range(2):
        result = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    flags = json.loads((evidence / "review-attention.json").read_text())
    assert len(flags["orange"]) == 1
    assert later[:12] in flags["orange"][0]["detail"]
    assert flags["orange"][0]["detail"].count(later[:12]) == 1


def test_annotate_pr_flags_red_acceptance_validator_once(tmp_path: Path) -> None:
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    attention: dict[str, object] = {
        "red": [],
        "orange": [],
        "yellow": [],
        "white": [],
        "accepted_head": accepted,
        "later_commits": [],
    }
    (evidence / "review-attention.json").write_text(json.dumps(attention))
    (evidence / "task-compliance.json").write_text(
        json.dumps(
            {
                "result": "passed",
                "reviewed_head": accepted,
                "base": accepted,
                "tasks_sha256": "a" * 64,
            }
        )
    )
    (evidence / "acceptance-validator-result.txt").write_text("FAIL\ntest: 2 failing\n")
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GH_BODY": str(tmp_path / "body.md"),
    }
    command = [
        str(PACKAGE / "annotate-pr.sh"),
        str(evidence),
        str(issue),
        "change",
        "openspec/changes/archive/2026-09-25-change",
    ]
    for _ in range(2):
        result = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    flags = json.loads((evidence / "review-attention.json").read_text())
    assert len(flags["red"]) == 1
    assert "test: 2 failing" in flags["red"][0]["detail"]
    (evidence / "acceptance-validator-result.txt").write_text("PASS\n")
    (evidence / "review-attention.json").write_text(json.dumps(attention))
    (evidence / "task-compliance.json").write_text(
        json.dumps(
            {
                "result": "passed",
                "reviewed_head": accepted,
                "base": accepted,
                "tasks_sha256": "a" * 64,
            }
        )
    )
    result = subprocess.run(command, cwd=repo, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((evidence / "review-attention.json").read_text())["red"] == []


def test_annotate_pr_links_items_to_github_or_the_evidence_section(tmp_path: Path) -> None:
    """The classifier writes whatever path it read; a reviewer on GitHub can open only
    committed files, so every other local path points at the acceptance evidence."""
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "gate.md").write_text("gate")
    (repo / "docs" / "my gate (v2).md").write_text("gate")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "docs")
    (repo / "scratch.log").write_text("untracked")
    accepted = git(repo, "rev-parse", "HEAD")
    branch = git(repo, "branch", "--show-current")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "acceptance-flow-evidence.md").write_text("flows")

    def item(title: str, link: str) -> dict[str, str]:
        return {"title": title, "detail": "d", "link": link}

    attention: dict[str, object] = {
        "red": [
            item("absolute tracked", str(repo.resolve() / "docs" / "gate.md")),
            item("relative tracked", "docs/gate.md"),
            item("tracked directory", "docs/"),
            item("line", "docs/gate.md:3"),
            item("line range", "docs/gate.md:3-5"),
            item("fragment", "docs/gate.md#requirement-gate"),
            item("needs encoding", "docs/my gate (v2).md"),
        ],
        "orange": [
            item("evidence file", str(evidence / "acceptance-flow-evidence.md")),
            item("untracked in clone", str(repo / "scratch.log")),
            item("missing", "no/such/file.md"),
        ],
        "yellow": [item("url", "https://example.test/x"), item("anchor", "#acceptance-evidence")],
        "white": [],
        "accepted_head": accepted,
        "later_commits": [],
    }
    (evidence / "review-attention.json").write_text(json.dumps(attention))
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7", "repository": "o/r"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body = tmp_path / "body.md"
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_BODY": str(body)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    rendered = body.read_text()
    blob = f"https://github.com/o/r/blob/{branch}"
    assert f"[absolute tracked]({blob}/docs/gate.md)" in rendered
    assert f"[relative tracked]({blob}/docs/gate.md)" in rendered
    assert f"[tracked directory]({blob}/docs)" in rendered
    assert f"[line]({blob}/docs/gate.md#L3)" in rendered
    assert f"[line range]({blob}/docs/gate.md#L3-L5)" in rendered
    assert f"[fragment]({blob}/docs/gate.md#requirement-gate)" in rendered
    assert f"[needs encoding]({blob}/docs/my%20gate%20%28v2%29.md)" in rendered
    for title in ("evidence file", "untracked in clone", "missing"):
        assert f"[{title}](#acceptance-evidence)" in rendered
    assert "[url](https://example.test/x)" in rendered
    assert "[anchor](#acceptance-evidence)" in rendered
    assert str(tmp_path) not in rendered.split("## Change summary")[0]


def review_item(title: str, detail: str = "d") -> dict[str, str]:
    return {"title": title, "detail": detail, "link": "#acceptance-evidence"}


def render_annotated_body(tmp_path: Path, build: Callable[[str, str], dict[str, object]]) -> str:
    """Annotate a pull request whose branch has one commit ("fix CI") after acceptance.

    `build` receives the accepted and the later commit SHA and returns review-attention.json.
    """
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "later").write_text("fix")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix CI")
    later = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(json.dumps(build(accepted, later)))
    (evidence / "task-compliance.json").write_text(
        json.dumps(
            {"result": "passed", "reviewed_head": later, "base": accepted, "tasks_sha256": "a" * 64}
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7", "title": "Add a flag"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body = tmp_path / "body.md"
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_BODY": str(body)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return body.read_text()


def test_annotate_pr_reports_why_the_description_update_failed(tmp_path: Path) -> None:
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [],
                "orange": [],
                "yellow": [],
                "white": [],
                "accepted_head": accepted,
                "later_commits": [],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GH_BODY": str(tmp_path / "body.md"),
            "GH_API_ERROR": "HTTP 422: body is too long (maximum is 65536 characters)",
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "body is too long" in result.stderr
    assert "pull request #9" in result.stderr


def annotate_with_bot_state(tmp_path: Path, reviews: str, statuses: str) -> dict[str, Any]:
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [],
                "orange": [{"title": "Scope", "detail": "choice", "link": ""}],
                "yellow": [],
                "white": [],
                "accepted_head": accepted,
                "later_commits": [],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body = tmp_path / "body.md"
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GH_BODY": str(body),
            "GH_REVIEWS": reviews,
            "GH_STATUSES": statuses,
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    flags = json.loads((evidence / "review-attention.json").read_text())
    return {"flags": flags, "body": body.read_text(), "head": accepted}


def test_annotate_pr_flags_a_rate_limited_bot_review_first(tmp_path: Path) -> None:
    """CodeRabbit's check is green when it was rate-limited, so its review of HEAD is required."""
    statuses = json.dumps(
        [
            {"context": "CodeRabbit", "description": "Review rate limited"},
            {"context": "CodeRabbit", "description": "Review in progress"},
        ]
    )
    stale = json.dumps([[{"user": {"login": "coderabbitai[bot]"}, "commit_id": "0" * 40}]])
    result = annotate_with_bot_state(tmp_path, stale, statuses)
    first = result["flags"]["orange"][0]
    assert first["title"] == "No CodeRabbit review of the final head"
    assert "Review rate limited" in first["detail"] and result["head"][:12] in first["detail"]
    assert result["body"].index("No CodeRabbit review") < result["body"].index("Scope")


def test_annotate_pr_adds_nothing_when_the_bot_completed_a_review_without_comments(
    tmp_path: Path,
) -> None:
    statuses = json.dumps(
        [
            {"context": "CodeRabbit", "description": "Review completed"},
            {"context": "CodeRabbit", "description": "Review in progress"},
        ]
    )
    result = annotate_with_bot_state(tmp_path, "[[]]", statuses)
    assert [item["title"] for item in result["flags"]["orange"]] == ["Scope"]


def test_annotate_pr_flags_the_latest_rate_limit_after_an_older_completed_review(
    tmp_path: Path,
) -> None:
    statuses = json.dumps(
        [
            {"context": "CodeRabbit", "description": "Review rate limited"},
            {"context": "CodeRabbit", "description": "Review completed"},
        ]
    )
    result = annotate_with_bot_state(tmp_path, "[[]]", statuses)
    assert [item["title"] for item in result["flags"]["orange"]] == [
        "No CodeRabbit review of the final head",
        "Scope",
    ]


def test_annotate_pr_flags_a_skipped_bot_review(tmp_path: Path) -> None:
    statuses = json.dumps(
        [
            {
                "context": "CodeRabbit",
                "description": "Review skipped: manual review required for this OSS repository",
            }
        ]
    )
    result = annotate_with_bot_state(tmp_path, "[[]]", statuses)
    assert "Review skipped: manual review" in result["flags"]["orange"][0]["detail"]


def test_annotate_pr_adds_nothing_when_the_bot_reviewed_the_head(tmp_path: Path) -> None:
    result = annotate_with_bot_state(tmp_path, "", "[]")
    assert [item["title"] for item in result["flags"]["orange"]] == ["Scope"]


def test_annotate_pr_ignores_a_review_by_a_deleted_account(tmp_path: Path) -> None:
    """GitHub sends a null user for a deleted account; that is not CodeRabbit, not an error."""
    reviews = json.dumps([[{"user": None, "commit_id": "0" * 40}]])
    result = annotate_with_bot_state(tmp_path, reviews, "[]")
    detail = result["flags"]["orange"][0]["detail"]
    assert "could not be read" not in detail and "posted no review" in detail


def shown_review_items(rendered: str) -> list[str]:
    review_first = rendered[rendered.index("# Review first") : rendered.index("Closes #7")]
    return [
        line.split("]")[0].removeprefix("- [")
        for line in review_first.splitlines()
        if line.startswith("- [")
    ]


def test_annotate_pr_opens_with_issue_and_shows_only_red_and_top_orange(
    tmp_path: Path,
) -> None:
    """A reviewer reads the issue line, every red item and at most five orange items;
    later commits stay visible, and the rest of orange and all yellow are collapsed."""
    rendered = render_annotated_body(
        tmp_path,
        lambda accepted, _later: {
            "red": [review_item("red one")],
            "orange": [review_item(f"orange {n}") for n in range(1, 7)],
            "yellow": [review_item("yellow one"), review_item("yellow two")],
            "white": [],
            "accepted_head": accepted,
            "later_commits": [],
        },
    )
    assert rendered.startswith("**Feature for #7:** Add a flag\n")
    review_first = rendered[rendered.index("# Review first") : rendered.index("Closes #7")]
    assert "### 🟠 Orange (7)" in review_first
    assert shown_review_items(rendered) == [
        "red one",
        "Commits after acceptance",
        "orange 1",
        "orange 2",
        "orange 3",
        "orange 4",
    ]
    assert "2 more orange items and 2 yellow items" in review_first
    assert "yellow one" not in review_first
    collapsed = rendered[rendered.index("<details><summary>2 more orange") :]
    for title in ("orange 5", "orange 6", "yellow one", "yellow two"):
        assert f"[{title}]" in collapsed


def test_annotate_pr_closes_the_issue_with_exactly_one_keyword(tmp_path: Path) -> None:
    """GitHub links a pull request to its issue (the board's "linked pull request", and
    closing the issue on merge) only through a closing keyword. The description is
    regenerated on every annotation, so it must carry the keyword itself, exactly once."""
    import re

    rendered = render_annotated_body(
        tmp_path,
        lambda accepted, _later: {
            "red": [],
            "orange": [],
            "yellow": [],
            "white": [],
            "accepted_head": accepted,
            "later_commits": [],
        },
    )
    keywords = re.findall(r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#7\b", rendered)
    assert keywords == ["Closes #7"]
    # The factory's own lookup of a claim's open pull requests must still find it.
    assert re.search(r"(?i)\b(?:refs|closes)\s+#7\b", rendered)


def test_annotate_pr_keeps_a_classifier_item_naming_later_commits_visible(
    tmp_path: Path,
) -> None:
    """Commits after acceptance stay visible whatever title the classifier gives them."""
    rendered = render_annotated_body(
        tmp_path,
        lambda accepted, later: {
            "red": [],
            "orange": [review_item(f"orange {n}") for n in range(1, 7)]
            + [review_item("CI repair", f"{later[:7]} fixed CI")],
            "yellow": [review_item("yellow one")],
            "white": [],
            "accepted_head": accepted,
            "later_commits": [later],
        },
    )
    assert shown_review_items(rendered) == [
        "CI repair",
        "orange 1",
        "orange 2",
        "orange 3",
        "orange 4",
    ]
    assert "2 more orange items and 1 yellow item " in rendered


def test_annotate_pr_never_collapses_items_naming_later_commits(tmp_path: Path) -> None:
    """Every item naming a commit after acceptance stays visible, even beyond the cap."""
    rendered = render_annotated_body(
        tmp_path,
        lambda accepted, later: {
            "red": [],
            "orange": [review_item(f"repair {n}", f"{later[:7]} part {n}") for n in range(1, 7)]
            + [review_item("orange 1")],
            "yellow": [],
            "white": [],
            "accepted_head": accepted,
            "later_commits": [later],
        },
    )
    assert shown_review_items(rendered) == [f"repair {n}" for n in range(1, 7)]
    assert "1 more orange item and 0 yellow items " in rendered


def test_annotate_pr_keeps_each_review_item_on_one_line(tmp_path: Path) -> None:
    """A multi-line detail must not add bullets or paragraphs to the one-line list."""
    rendered = render_annotated_body(
        tmp_path,
        lambda accepted, _later: {
            "red": [],
            "orange": [review_item("Split\ntitle", "First line.\n- second\n\n- third")],
            "yellow": [review_item("Yellow", "a\nb")],
            "white": [],
            "accepted_head": accepted,
            "later_commits": [],
        },
    )
    assert "- [Split title](#acceptance-evidence): First line. - second - third\n" in rendered
    assert "- [Yellow](#acceptance-evidence): a b\n" in rendered


def test_feature_record_outcome_adds_counts_resume_and_branch(tmp_path: Path) -> None:
    counts = {"red": 1, "orange": 2, "yellow": 3, "white": 4}
    payload = {
        "contract": "factory-feature/1",
        "outcome_path": str(tmp_path / "feature-outcome.json"),
        "validator_status": "passed",
        "ci_status": "passed",
        "branch_name": "claim",
        "pr_details": json.dumps({"number": 9, "url": "https://example.test/pull/9"}),
        "review_attention_counts": json.dumps(counts),
        "resume": json.dumps({"fallback": "prior branch unavailable"}),
    }
    result = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    value = read_interpreted_outcome(tmp_path, "factory-feature/1").outcome
    assert value is not None
    assert value.result["review_attention_counts"] == counts
    assert value.result["resume"] == {"fallback": "prior branch unavailable"}
    assert isinstance(value.result["pr"], dict)
    assert value.result["pr"]["branch"] == "claim"


def test_feature_outcome_rejects_invalid_counts(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "pull-request",
                "review_attention_counts": {"red": True},
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_feature_record_outcome_reads_annotation_file_and_uses_final_counts(tmp_path: Path) -> None:
    attention = tmp_path / "review-attention.json"
    attention.write_text(
        json.dumps(
            {
                "red": [],
                "orange": [{"title": "later", "detail": "commit", "link": "#evidence"}],
                "yellow": [],
                "white": [],
                "accepted_head": "a" * 40,
                "later_commits": [],
            }
        )
    )
    payload = {
        "contract": "factory-feature/1",
        "outcome_path": str(tmp_path / "feature-outcome.json"),
        "validator_status": "passed",
        "ci_status": "passed",
        "branch_name": "claim",
        "pr_details": json.dumps({"number": 9, "url": "https://example.test/pull/9"}),
        "review_attention_counts": str(attention),
        "resume": str(tmp_path / "missing-resume.json"),
    }
    result = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    value = json.loads((tmp_path / "feature-outcome.json").read_text())
    assert value["review_attention_counts"] == {"red": 0, "orange": 1, "yellow": 0, "white": 0}
    assert "resume" not in value


def test_feature_outcome_rejects_missing_stop_fields(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "needs-input",
                "reasons": ["choose API"],
                "stopped_step": "design",
                "branch": "claim",
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_failed_openspec_validation_records_errors(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    (repo / "openspec" / "draft.md").write_text("draft")
    errors = tmp_path / "validation.log"
    errors.write_text("invalid MODIFIED requirement")
    result = run(str(PACKAGE / "record-validation-failure.sh"), str(tmp_path), "claim", cwd=repo)
    assert result.returncode == 0, result.stderr
    outcome = json.loads((tmp_path / "feature-outcome.json").read_text())
    assert outcome["outcome"] == "failed"
    assert outcome["branch"] == "claim"
    assert "invalid MODIFIED requirement" in outcome["reasons"][0]
    assert git(remote, "rev-parse", "refs/heads/claim") == git(repo, "rev-parse", "HEAD")


def test_define_skips_every_step_when_resuming_past_write_tasks(tmp_path: Path) -> None:
    """A resume past definition skips validation, so no step may run that reads its
    validation.log or repairs its errors (#15 claim d5b85ace failed define this way)."""
    import re
    import shutil

    workflows = tmp_path / ".agent-runner" / "workflows"
    workflows.mkdir(parents=True)
    shutil.copy(str(PACKAGE / "factory-resume-skip.sh"), workflows)
    (workflows / "factory-resume-skip.sh").chmod(0o755)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    text = (PACKAGE / "factory-define-v1.0.yaml").read_text()
    steps = re.split(r"\n  - id: ", "\n" + text.split("\nsteps:\n", 1)[1])[1:]
    values = {"artifact_dir": str(artifact_dir), "resume_from": "implement"}
    ran: list[str] = []
    for step in steps:
        step_id = step.split("\n", 1)[0]
        skip = re.search(r"^    skip_if: '(.*)'$", step, re.MULTILINE)
        assert skip, f"{step_id} has no skip_if"
        condition = skip.group(1).removeprefix("sh: ")
        condition = re.sub(r"{{(\w+)}}", lambda m: values.get(m.group(1), m.group(0)), condition)
        if run("sh", "-c", condition, cwd=tmp_path).returncode != 0:
            ran.append(step_id)
    assert ran == []


def test_checkpoint_is_visible_only_after_push(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    (repo / "openspec" / "changes" / "sample").mkdir(parents=True)
    task = repo / "openspec" / "changes" / "sample" / "tasks.md"
    task.write_text("- [ ] Implement the feature\n")
    script = str(PACKAGE / "checkpoint.sh")
    assert run(script, "planned", "claim", "sample", cwd=repo).returncode == 0
    planned = git(remote, "rev-parse", "refs/heads/claim")
    assert "Factory-Checkpoint: planned" in git(repo, "log", "-1", "--format=%B")
    (repo / "code.py").write_text("answer = 42\n")
    git(repo, "add", "code.py")
    git(repo, "commit", "-m", "implement")
    assert git(remote, "rev-parse", "refs/heads/claim") == planned
    assert run(script, "implemented", "claim", "sample", cwd=repo).returncode == 0
    assert "Factory-Checkpoint: implemented" in git(
        remote, "log", "-1", "--format=%B", "refs/heads/claim"
    )
    assert task.read_text() == "- [x] Implement the feature\n"
    implemented = git(remote, "rev-parse", "refs/heads/claim")
    (repo / "openspec" / "archive-note.md").write_text("archived")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "archive change")
    assert git(remote, "rev-parse", "refs/heads/claim") == implemented
    assert run(script, "archived", "claim", "sample", cwd=repo).returncode == 0
    assert "Factory-Checkpoint: archived" in git(
        remote, "log", "-1", "--format=%B", "refs/heads/claim"
    )


def test_implemented_checkpoint_only_completes_its_change(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    changes = repo / "openspec" / "changes"
    for name in ("target", "unrelated"):
        path = changes / name
        path.mkdir(parents=True)
        (path / "tasks.md").write_text(f"- [ ] {name} task\n")
    result = run(str(PACKAGE / "checkpoint.sh"), "implemented", "claim", "target", cwd=repo)
    assert result.returncode == 0, result.stderr
    assert (changes / "target" / "tasks.md").read_text() == "- [x] target task\n"
    assert (changes / "unrelated" / "tasks.md").read_text() == "- [ ] unrelated task\n"
    assert "openspec/changes/unrelated/tasks.md" not in git(
        repo, "ls-tree", "-r", "--name-only", "HEAD"
    )


def test_implemented_checkpoint_rejects_multiple_open_tasks(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    git(repo, "checkout", "-b", "claim")
    task = repo / "openspec" / "changes" / "target" / "tasks.md"
    task.parent.mkdir(parents=True)
    task.write_text("- [ ] first\n- [ ] second\n")
    result = run(str(PACKAGE / "checkpoint.sh"), "implemented", "claim", "target", cwd=repo)
    assert result.returncode != 0
    assert run("git", "rev-parse", "refs/heads/claim", cwd=remote).returncode != 0
    assert task.read_text() == "- [ ] first\n- [ ] second\n"


def test_prepare_branch_conflict_keeps_prior_branch_for_resolution(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    (repo / "choice.txt").write_text("old direction\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "plan")
    git(repo, "push", "-u", "origin", "prior")
    git(repo, "checkout", "main")
    (repo / "choice.txt").write_text("new direction\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change target")
    target = git(repo, "rev-parse", "HEAD")
    assert target != base
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = run(
        str(PACKAGE / "prepare-branch.sh"),
        "claim",
        target,
        "implement",
        "prior",
        str(evidence),
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "implement"
    assert git(repo, "rev-parse", "HEAD") != target
    assert git(repo, "rev-parse", "MERGE_HEAD") == target
    assert json.loads((evidence / "merge-conflict.json").read_text())["conflicted"] == [
        "choice.txt"
    ]
    assert not (evidence / "resume.json").exists()


def test_prepare_branch_continuation_carries_prior_change_to_new_name(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "factory/feature-12-aaaaaaaa")
    prior_change = repo / "openspec" / "changes" / "feature-12-aaaaaaaa"
    prior_change.mkdir(parents=True)
    (prior_change / "tasks.md").write_text("- [ ] only task\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "plan")
    git(repo, "push", "-u", "origin", "factory/feature-12-aaaaaaaa")
    git(repo, "checkout", "main")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    payload = {
        "branch_name": "factory/feature-12-bbbbbbbb",
        "target_head": target,
        "resume_from": "implement",
        "prior_branch": "factory/feature-12-aaaaaaaa",
        "artifact_dir": str(evidence),
    }
    result = run(str(PACKAGE / "prepare-branch.sh"), cwd=repo, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "implement"
    assert git(repo, "branch", "--show-current") == "factory/feature-12-bbbbbbbb"
    renamed = run(
        str(PACKAGE / "continue-change.sh"),
        "factory/feature-12-aaaaaaaa",
        "feature-12-bbbbbbbb",
        cwd=repo,
    )
    assert renamed.returncode == 0, renamed.stderr
    changes = repo / "openspec" / "changes"
    assert (changes / "feature-12-bbbbbbbb" / "tasks.md").read_text() == "- [ ] only task\n"
    assert not (changes / "feature-12-aaaaaaaa").exists()
    assert git(repo, "status", "--porcelain") == ""
    renamed_head = git(repo, "rev-parse", "HEAD")
    again = run(
        str(PACKAGE / "continue-change.sh"),
        "factory/feature-12-aaaaaaaa",
        "feature-12-bbbbbbbb",
        cwd=repo,
    )
    assert again.returncode == 0, again.stderr
    assert git(repo, "rev-parse", "HEAD") == renamed_head


def test_prepare_branch_continuation_carries_prior_archive_to_new_name(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    target = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "factory/feature-12-aaaaaaaa")
    archive = repo / "openspec" / "changes" / "archive"
    (archive / "2026-09-24-feature-12-aaaaaaaa").mkdir(parents=True)
    (archive / "2026-09-24-feature-12-aaaaaaaa" / "tasks.md").write_text("- [x] only task\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "archived")
    git(repo, "push", "-u", "origin", "factory/feature-12-aaaaaaaa")
    git(repo, "checkout", "main")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    payload = {
        "branch_name": "factory/feature-12-bbbbbbbb",
        "target_head": target,
        "resume_from": "verify",
        "prior_branch": "factory/feature-12-aaaaaaaa",
        "artifact_dir": str(evidence),
    }
    result = run(str(PACKAGE / "prepare-branch.sh"), cwd=repo, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "verify"
    renamed = run(
        str(PACKAGE / "continue-change.sh"),
        "factory/feature-12-aaaaaaaa",
        "feature-12-bbbbbbbb",
        cwd=repo,
    )
    assert renamed.returncode == 0, renamed.stderr
    assert (archive / "2026-09-24-feature-12-bbbbbbbb" / "tasks.md").exists()
    assert not (archive / "2026-09-24-feature-12-aaaaaaaa").exists()
    assert git(repo, "status", "--porcelain") == ""
    located = run(str(PACKAGE / "locate-archive.py"), "feature-12-bbbbbbbb", cwd=repo)
    assert located.returncode == 0, located.stderr


def test_check_openspec_requires_openspec_and_validator_config(tmp_path: Path) -> None:
    script = str(PACKAGE / "check-openspec.sh")
    repo = tmp_path / "repo"
    repo.mkdir()
    evidence = tmp_path / "evidence"

    def stop() -> dict[str, Any] | None:
        outcome = evidence / "feature-outcome.json"
        if outcome.exists():
            value = json.loads(outcome.read_text())
            outcome.unlink()
            return value
        return None

    assert run(script, str(evidence), cwd=repo).returncode == 0
    missing_both = stop()
    assert missing_both is not None and missing_both["stopped_step"] == "preflight"
    assert "OpenSpec" in " ".join(missing_both["reasons"])
    (repo / "openspec").mkdir()
    assert run(script, str(evidence), cwd=repo).returncode == 0
    missing_validator = stop()
    assert missing_validator is not None and missing_validator["stopped_step"] == "preflight"
    assert "Agent Validator" in " ".join(missing_validator["reasons"])
    assert "branch" not in missing_validator
    (repo / ".validator").mkdir()
    (repo / ".validator" / "config.yml").write_text("entry_points: []\n")
    assert run(script, str(evidence), cwd=repo).returncode == 0
    assert stop() is None


def test_prepare_branch_missing_own_branch_clears_resume(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    target = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = run(
        str(PACKAGE / "prepare-branch.sh"),
        "missing-claim",
        target,
        "design",
        "",
        str(evidence),
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert json.loads((evidence / "resume.json").read_text())["fallback"]


def test_locate_archive_finds_dated_change(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    archived = repo / "openspec" / "changes" / "archive" / "2026-09-25-sample"
    archived.mkdir(parents=True)
    result = run(str(PACKAGE / "locate-archive.py"), "sample", cwd=repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(archived.relative_to(repo))


def test_feature_outcome_rejects_pull_request_without_reference(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "pull-request",
                "review_attention_counts": {"red": 0, "orange": 0, "yellow": 0, "white": 0},
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_feature_outcome_rejects_failure_without_reasons(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps({"contract": "factory-feature/1", "outcome": "failed", "branch": "claim"})
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None


def test_annotation_flags_only_commits_not_already_classified(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    archive = repo / "openspec" / "changes" / "archive" / "2026-09-25-change"
    archive.mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "first").write_text("first")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first repair")
    first = git(repo, "rev-parse", "HEAD")
    (repo / "second").write_text("second")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second repair")
    second = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [],
                "orange": [{"title": "Prior repair", "detail": first, "link": "#prior"}],
                "yellow": [],
                "white": [],
                "accepted_head": accepted,
                "later_commits": [first],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(
        '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = list ]; then\n'
        '  echo \'[{"number":9,"url":"https://github.com/o/r/pull/9"}]\'\n'
        "else exit 0; fi\n"
    )
    stub.chmod(0o755)
    import os

    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    flags = json.loads((evidence / "review-attention.json").read_text())
    later_item = next(
        item for item in flags["orange"] if item["title"] == "Commits after acceptance"
    )
    assert second[:12] in later_item["detail"]
    assert first[:12] not in later_item["detail"]
    assert set(flags["later_commits"]) == {first, second}


def test_annotation_failure_records_failed_outcome(tmp_path: Path) -> None:
    payload = {
        "contract": "factory-feature/1",
        "outcome_path": str(tmp_path / "feature-outcome.json"),
        "validator_status": "passed",
        "ci_status": "passed",
        "annotation_status": "failed",
        "branch_name": "claim",
        "pr_details": json.dumps({"number": 9, "url": "https://example.test/pull/9"}),
    }
    result = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    value = json.loads((tmp_path / "feature-outcome.json").read_text())
    assert value["outcome"] == "failed"
    assert value["pr"]["number"] == 9
    assert "annotation" in value["reasons"][0]


def test_annotation_retries_transient_pr_edit_failure(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [],
                "orange": [],
                "yellow": [],
                "white": [],
                "accepted_head": git(repo, "rev-parse", "HEAD"),
                "later_commits": [],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = list ]; then echo \'[{"number":9}]\'; exit 0; fi\n'
        'case "$*" in */reviews*) echo "[[]]"; exit 0 ;; */statuses*) echo "[]"; exit 0 ;; esac\n'
        'count=0; [ ! -f "$GH_COUNT" ] || count=$(cat "$GH_COUNT")\n'
        'count=$((count + 1)); echo "$count" > "$GH_COUNT"\n'
        '[ "$count" -gt 1 ]\n'
    )
    stub.chmod(0o755)
    import os

    count = tmp_path / "count"
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_COUNT": str(count)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert count.read_text().strip() == "2"


def test_feature_workflow_skip_conditions_read_only_variables_defined_on_a_stop() -> None:
    """The Runner evaluates every skip_if, even after a definition stop has written the
    outcome, and fails the run on an undefined variable. So each variable a skip_if reads
    must be a parameter or be captured before any stop can happen or by an unguarded step."""
    import re

    text = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    params = set(re.findall(r"^  - name: (\w+)", text.split("\nsessions:")[0], re.MULTILINE))
    steps = re.split(r"\n  - id: ", "\n" + text.split("\nsteps:\n", 1)[1])[1:]
    defined = set(params)
    stop_reached = False
    for step in steps:
        step_id = step.split("\n", 1)[0]
        stop_reached = stop_reached or step_id.startswith("record-stop")
        skip = re.search(r"^    skip_if: (.*)$", step, re.MULTILINE)
        if skip:
            undefined = set(re.findall(r"{{(\w+)}}", skip.group(1))) - defined
            assert not undefined, f"{step_id} skip_if reads {undefined} undefined on a stop"
        capture = re.search(r"^    capture: (\w+)", step, re.MULTILINE)
        if capture and (not stop_reached or not skip):
            defined.add(capture.group(1))


def test_resume_merges_current_base_and_reverifies_finalize(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "claim")
    (repo / "work.txt").write_text("claim work\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "claim work")
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-u", "origin", "claim")
    git(repo, "checkout", "main")
    (repo / "fix.txt").write_text("target fix\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "target fix")
    base = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = run(
        str(PACKAGE / "prepare-branch.sh"),
        "claim",
        admission,
        "finalize",
        "",
        str(evidence),
        base,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "verify"
    assert git(repo, "show", "-s", "--format=%P", "HEAD") == f"{before} {base}"
    assert (repo / "work.txt").read_text() == "claim work\n"
    assert (repo / "fix.txt").read_text() == "target fix\n"
    assert json.loads((evidence / "base-merge.json").read_text()) == {
        "target_at_admission": admission,
        "base_head": base,
        "pre_merge_head": before,
        "status": "merged",
        "merge_commit": git(repo, "rev-parse", "HEAD"),
    }
    assert not (evidence / "resume.json").exists()


def test_conflict_resolution_and_stop_use_real_git(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    (repo / "choice.txt").write_text("prior\n")
    (repo / "work.txt").write_text("keep\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "prior work")
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "-u", "origin", "prior")
    git(repo, "checkout", "main")
    (repo / "choice.txt").write_text("target\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "target change")
    base = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    args = ("claim", admission, "implement", "prior", str(evidence), base)
    result = run(str(PACKAGE / "prepare-branch.sh"), *args, cwd=repo)
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "MERGE_HEAD") == base
    assert json.loads((evidence / "merge-conflict.json").read_text())["pre_merge_head"] == before
    (repo / "choice.txt").write_text("prior and target\n")
    git(repo, "add", "choice.txt")
    git(repo, "commit", "--no-edit")
    checked = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert checked.returncode == 0, checked.stderr
    assert json.loads((evidence / "base-merge.json").read_text())["status"] == "resolved"
    assert (repo / "work.txt").read_text() == "keep\n"
    recorded = (evidence / "base-merge.json").read_text()
    git(repo, "reset", "--hard", base)
    rejected = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert rejected.returncode != 0
    assert (evidence / "base-merge.json").read_text() == recorded

    git(repo, "checkout", "main")
    evidence2 = tmp_path / "evidence2"
    evidence2.mkdir()
    result = run(str(PACKAGE / "prepare-branch.sh"), *args[:4], str(evidence2), base, cwd=repo)
    assert result.returncode == 0, result.stderr
    (evidence2 / "merge-stop.json").write_text(
        json.dumps({"questions": ["Which choice?"], "direction_summary": "Need a decision."})
    )
    stopped = run(str(PACKAGE / "record-merge-stop.sh"), str(evidence2), "claim", cwd=repo)
    assert stopped.returncode == 0, stopped.stderr
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(remote, "rev-parse", "refs/heads/claim") == before
    outcome = json.loads((evidence2 / "feature-outcome.json").read_text())
    assert outcome["outcome"] == "needs-input"
    assert outcome["stopped_step"] == "implement"
    assert "choice.txt" in outcome["questions"][0]
    assert read_interpreted_outcome(evidence2, "factory-feature/1").outcome is not None


def verify_classification(
    tmp_path: Path, attention: dict[str, Any]
) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "review-attention.json"
    path.write_text(json.dumps(attention))
    return run("python3", str(PACKAGE / "verify-classification.py"), str(path), cwd=tmp_path)


def classified(**tiers: Any) -> dict[str, Any]:
    return {
        "red": [],
        "orange": [],
        "yellow": [],
        "white": [],
        "accepted_head": "a" * 40,
        "later_commits": [],
        **tiers,
    }


def item(title: str, detail: str = "detail") -> dict[str, str]:
    return {"title": title, "detail": detail, "link": "#evidence"}


def test_verify_classification_accepts_a_later_commits_item_with_size_and_tests(
    tmp_path: Path,
) -> None:
    later = "b" * 40
    attention = classified(
        later_commits=[later],
        orange=[
            item(
                "Commits after acceptance",
                f"{later[:7]} fix lint. 2 files changed, 10 insertions(+), 3 deletions(-). "
                "Tests: covered by TestSweepIntegration.",
            )
        ],
        yellow=[item("Criterion covered by TestReplayRetentionRace")],
    )
    result = verify_classification(tmp_path, attention)
    assert result.returncode == 0, result.stderr


def test_verify_classification_requires_later_commits_diff_size_and_test_coverage(
    tmp_path: Path,
) -> None:
    later = "b" * 40
    attention = classified(
        later_commits=[later], orange=[item("Commits after acceptance", f"{later[:7]} fix lint")]
    )
    result = verify_classification(tmp_path, attention)
    assert result.returncode != 0
    assert "diff size" in result.stderr
    assert "Tests:" in result.stderr


def test_verify_classification_requires_every_later_commit_named_in_orange(
    tmp_path: Path,
) -> None:
    later = "b" * 40
    attention = classified(later_commits=[later], yellow=[item("Commits", later[:7])])
    result = verify_classification(tmp_path, attention)
    assert result.returncode != 0
    assert later[:7] in result.stderr


def test_verify_classification_rejects_one_item_in_two_tiers(tmp_path: Path) -> None:
    attention = classified(red=[item("Smoke test fails")], orange=[item("Smoke test fails")])
    result = verify_classification(tmp_path, attention)
    assert result.returncode != 0
    assert "Smoke test fails" in result.stderr


def test_verify_classification_allows_generic_titles_repeated_within_a_tier(
    tmp_path: Path,
) -> None:
    attention = classified(white=[item("Criterion passed", "a"), item("Criterion passed", "b")])
    result = verify_classification(tmp_path, attention)
    assert result.returncode == 0, result.stderr


def test_verify_classification_rejects_malformed_items(tmp_path: Path) -> None:
    attention = classified(red=[{"title": "", "detail": "x", "link": "#e"}], white=["passed"])
    result = verify_classification(tmp_path, attention)
    assert result.returncode != 0
    assert "red[0]" in result.stderr
    assert "white[0]" in result.stderr


def test_annotate_pr_marks_items_whose_linked_file_changed_after_acceptance(
    tmp_path: Path,
) -> None:
    """Classification runs before finalization's last fixes, so an item may already be
    fixed by a later commit; the item then says which commit changed its linked file."""
    import os

    repo, _ = repository(tmp_path)
    (repo / "openspec" / "changes" / "archive" / "2026-09-25-change").mkdir(parents=True)
    (repo / "settings.go").write_text('enabled == "true"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "settings.go").write_text("enabled is a bool\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix: read enabled as a YAML bool")
    later = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [
                    {
                        "title": "Known deviation",
                        "detail": "True reads as off",
                        "link": "settings.go:1",
                    },
                    {"title": "Unverified", "detail": "no run", "link": "openspec/.keep"},
                ],
                "orange": [],
                "yellow": [],
                "white": [],
                "accepted_head": accepted,
                "later_commits": [],
            }
        )
    )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7", "repository": "o/r"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body = tmp_path / "body.md"
    result = subprocess.run(
        [
            str(PACKAGE / "annotate-pr.sh"),
            str(evidence),
            str(issue),
            "change",
            "openspec/changes/archive/2026-09-25-change",
        ],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_BODY": str(body)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    lines = body.read_text().splitlines()
    deviation = next(line for line in lines if line.startswith("- [Known deviation]"))
    unverified = next(line for line in lines if line.startswith("- [Unverified]"))
    assert f"may be fixed by `{later[:7]}`" in deviation
    assert "may be fixed by" not in unverified


REVIEW_GH_STUB = (
    '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = view ]; then\n'
    "  python3 -c 'import json,sys; "
    'print(json.dumps({"body": open(sys.argv[1], newline="").read()}))\' "$GH_BODY"\n'
    'elif [ "$1" = api ]; then\n'
    '  for arg; do case "$arg" in body=@*) cat "${arg#body=@}" > "$GH_BODY" ;; esac; done\n'
    "fi\n"
)


def test_review_round_description_keeps_the_layout_and_reflects_the_round(
    tmp_path: Path,
) -> None:
    """A review round restores the factory's description over the agent's rewrite, but the
    restored description still shows what the round did: the round's commits and the
    feedback it addressed, every commit after acceptance, and which items those commits
    may have fixed. Lines added outside the factory's items, such as a closing keyword,
    are kept."""
    import os

    from agent_factory.work_kinds.pull_request import launch

    repo, _ = repository(tmp_path)
    git(repo, "checkout", "-b", "factory/feature-7")
    (repo / "spec.md").write_text("smoke passes\n")
    (repo / "plan.md").write_text("AT-001\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")
    accepted = git(repo, "rev-parse", "HEAD")
    (repo / "run.sh").write_text("cleanup\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix: defer early signals")
    round_start = git(repo, "rev-parse", "HEAD")
    (repo / "spec.md").write_text("smoke passes with a lead auditor\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "fix: align smoke fixture with lead auditor")
    fixture = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    (repo / "unrelated").write_text("main moved\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "main work")
    git(repo, "checkout", "-q", "factory/feature-7")
    git(repo, "merge", "--no-ff", "-m", "Merge main", "main")
    merge = git(repo, "rev-parse", "HEAD")

    blob = "https://github.com/o/r/blob/factory/feature-7"
    saved = "\n".join(
        [
            "**Feature for #7:** Clean up smoke leftovers",
            "",
            "# Review first",
            "",
            "### 🔴 Red (2)",
            f"- [Spec deviation]({blob}/spec.md#L1): the smoke fails on this host",
            f"- [AT-001 unverified]({blob}/plan.md#L1): no success run",
            "",
            "### 🟠 Orange (1)",
            f"- [Commits after acceptance](#acceptance-evidence): {round_start[:12]} "
            "(fix: defer early signals)",
            "",
            "Closes #7",
            "<!-- agent-factory:claim:claim-7 -->",
            "",
            "## Change summary",
            "",
            "Feature: Clean up smoke leftovers.",
            "",
            '<details id="acceptance-evidence"><summary>Acceptance evidence</summary>',
            "",
            f"Acceptance ran against `{accepted}`.",
            "",
            f"Later commits: `{round_start}`",
            "",
            "</details>",
            "",
        ]
    )
    body = tmp_path / "body.md"
    body.write_text(saved)
    staged = launch.stage_workflow(tmp_path / "stage", launch.REVIEW_CONTRACT)
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps(
            {
                "kind": "feature",
                "head_sha": round_start,
                "pull_request": {"number": 9, "head_sha": round_start},
            }
        )
    )
    decision = {
        "needs_input": [],
        "items": [
            {
                "id": "c1",
                "source": "comment",
                "decision": "change",
                "reply": "Fixed the audit-warning blocker\n(A1/L1).",
                "plan": "Configure the lead agent in the fixture.",
            },
            {"id": "c2", "source": "comment", "decision": "answer", "reply": "No change."},
            {"id": "c3", "source": "comment", "decision": "change", "reply": "Kept </details>"},
        ],
    }
    stub = tmp_path / "bin" / "gh"
    stub.parent.mkdir()
    stub.write_text(REVIEW_GH_STUB)
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}", "GH_BODY": str(body)}

    def describe(mode: str) -> None:
        payload = {
            "mode": mode,
            "review_file": str(review),
            "artifact_dir": str(tmp_path),
            "decision": json.dumps(decision),
        }
        result = subprocess.run(
            [str(staged / "review-description.sh")],
            cwd=repo,
            env=env,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr

    describe("save")
    rewritten = "# Review follow-up\n\nA1/L1 fixed.\n\n" + saved
    body.write_text(rewritten)
    describe("restore")

    restored = body.read_text()
    assert restored.startswith("**Feature for #7:** Clean up smoke leftovers\n\n# Review first")
    assert "Closes #7" in restored
    assert (tmp_path / "pr-description-overwritten.md").read_text() == rewritten
    lines = restored.splitlines()
    commits_item = next(line for line in lines if line.startswith("- [Commits after acceptance]"))
    for sha in (round_start, fixture, merge):
        assert sha[:12] in commits_item
    assert "main work" not in commits_item
    later_line = next(line for line in lines if line.startswith("Later commits:"))
    for sha in (round_start, fixture, merge):
        assert sha in later_line
    deviation = next(line for line in lines if line.startswith("- [Spec deviation]"))
    unverified = next(line for line in lines if line.startswith("- [AT-001 unverified]"))
    assert f"may be fixed by `{fixture[:7]}`" in deviation
    assert "may be fixed by" not in unverified
    follow_up = restored.split("### 🔁 Review round", 1)[1].split("## Change summary")[0]
    assert fixture[:7] in follow_up and "align smoke fixture with lead auditor" in follow_up
    assert f"- `{merge[:7]}` Merge main" in follow_up
    assert f"- `{round_start[:7]}`" not in follow_up and "main work" not in follow_up
    assert "Fixed the audit-warning blocker (A1/L1)." in follow_up
    assert "No change." not in follow_up
    assert "Kept &lt;/details&gt;" in follow_up and "</details>" not in follow_up
    assert restored.index("### 🔁 Review round") < restored.index("## Change summary")


def test_task_compliance_workflow_structure() -> None:
    workflow = (PACKAGE / "factory-feature-v1.0.yaml").read_text()
    assert "  - id: task-compliance-implemented\n" not in workflow
    assert "  - id: task-compliance-implemented-final\n" not in workflow
    assert workflow.index("  - id: implement\n") < workflow.index("  - id: complete-task\n")
    assert (
        workflow.index("  - id: restore-skipped-verify-status\n")
        < workflow.index("  - id: task-compliance-verified\n")
        < workflow.index("  - id: classify\n")
    )
    assert "  - name: implementor-agent\n    agent: implementor" in workflow
    for phase, tasks in (("verified", '"{{archived_dir}}/tasks.md"'),):
        block = workflow.split(f"  - id: task-compliance-{phase}\n", 1)[1].split(
            f"  - id: task-compliance-{phase}-final\n", 1
        )[0]
        assert "loop:\n      max: 3" in block
        assert "break_if: success" in block
        assert "session: implementor-agent" in block
        assert "skip_if: previous_success" in block
        assert "continue_on_failure: true" in block
        assert f"tasks_file: {tasks}" in block
        assert "capture: validator_status" not in block
        final = workflow.split(f"  - id: task-compliance-{phase}-final\n", 1)[1].split(
            "  - id:", 1
        )[0]
        assert "skip_if: previous_success" in final
    assert 'task_compliance: "{{artifact_dir}}/task-compliance.json"' in workflow
    assert "task-compliance-gate.py" in FEATURE_STAGED_FILES


@pytest.mark.parametrize(
    "compliance,expected",
    [
        ({"result": "passed"}, "passed"),
        ({"result": "not-declared"}, "passed"),
        ({"result": "not-run", "reason": "Trusted"}, "incomplete"),
        ({"result": "failed"}, "review-failed"),
        (None, "incomplete"),
    ],
)
def test_feature_outcome_qualifies_validator_status(
    tmp_path: Path, compliance: dict[str, str] | None, expected: str
) -> None:
    record_path = tmp_path / "task-compliance.json"
    if compliance is not None:
        record_path.write_text(
            json.dumps(
                {
                    **compliance,
                    "base": "a" * 40,
                    "reviewed_head": "b" * 40,
                    "tasks_sha256": "c" * 64,
                }
            )
        )
    payload = {
        "contract": "factory-feature/1",
        "outcome_path": str(tmp_path / "feature-outcome.json"),
        "validator_status": "passed",
        "ci_status": "passed",
        "annotation_status": "passed",
        "branch_name": "feature",
        "review_attention_counts": json.dumps({"red": [], "orange": [], "yellow": [], "white": []}),
        "pr_details": json.dumps({"url": "https://example.test/pr", "number": 1}),
        "task_compliance": str(record_path),
    }
    result = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    outcome = json.loads((tmp_path / "feature-outcome.json").read_text())
    assert outcome["outcome"] == "pull-request"
    assert outcome["validator"] == {"status": expected, "checks": "passed"}
    assert outcome["task_compliance"]["result"] == (compliance or {}).get("result", "not-run")
    parsed = read_interpreted_outcome(tmp_path, "factory-feature/1")
    assert parsed.outcome is not None and parsed.outcome.product_verdict == "pull-request"
    if compliance is None:
        record_path.write_text('{"result": "passed"}')  # Missing binding is unreadable evidence.
        rerun = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
        assert rerun.returncode == 0, rerun.stderr
        assert (
            json.loads((tmp_path / "feature-outcome.json").read_text())["validator"]["status"]
            == "incomplete"
        )
        record_path.write_text(
            json.dumps({"result": "not-run", "reason": "binding failed: bad base", "base": ""})
        )
        rerun = run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
        assert rerun.returncode == 0, rerun.stderr
        assert json.loads((tmp_path / "feature-outcome.json").read_text())["task_compliance"] == {
            "result": "not-run",
            "reason": "binding failed: bad base",
            "base": "",
        }
    payload["validator_status"] = "failed"
    run(str(PACKAGE / "record-outcome.sh"), cwd=tmp_path, input=json.dumps(payload))
    assert (
        json.loads((tmp_path / "feature-outcome.json").read_text())["validator"]["status"]
        == "failed"
    )


def test_task_compliance_record_rejects_non_object_json(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("annotate_pr", str(PACKAGE / "annotate-pr.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "task-compliance.json").write_text("[]")

    assert module.task_compliance_record(tmp_path) == {
        "result": "not-run",
        "reason": "no task-compliance record",
    }
    (tmp_path / "task-compliance.json").write_text(
        json.dumps({"result": "not-run", "reason": "binding failed: bad base", "base": ""})
    )
    assert module.task_compliance_record(tmp_path)["reason"] == "binding failed: bad base"


@pytest.mark.parametrize(
    "compliance,tier,title,status",
    [
        (
            {"result": "not-run", "reason": "Trusted"},
            "red",
            "Task-compliance did not run",
            "incomplete",
        ),
        (
            {
                "result": "failed",
                "violations": [{"file": "code.py", "line": 4, "issue": "missing"}],
            },
            "red",
            "Task-compliance violations remain",
            "review-failed",
        ),
        ({"result": "not-declared"}, "yellow", "Task-compliance not declared", "passed"),
        ({"result": "passed"}, "white", "Task-compliance passed", "passed"),
        (None, "red", "Task-compliance did not run", "incomplete"),
    ],
)
def test_task_compliance_record_flows_through_pr_and_outcome(
    tmp_path: Path, compliance: dict[str, Any] | None, tier: str, title: str, status: str
) -> None:
    import os

    repo, _ = repository(tmp_path)
    archive = repo / "openspec" / "changes" / "archive" / "2026-09-25-change"
    archive.mkdir(parents=True)
    accepted = git(repo, "rev-parse", "HEAD")
    for name in ("first", "second"):
        (repo / name).write_text(name)
        git(repo, "add", ".")
        git(repo, "commit", "-m", name)
    later = git(repo, "log", "--format=%H", f"{accepted}..HEAD").splitlines()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "review-attention.json").write_text(
        json.dumps(
            {
                "red": [
                    {
                        "title": "Task-compliance did not run",
                        "detail": "classifier guess",
                        "link": "#acceptance-evidence",
                    }
                ],
                "orange": [
                    {
                        "title": "Task-compliance violations remain",
                        "detail": "guess",
                        "link": "#acceptance-evidence",
                    }
                ],
                "yellow": [
                    {
                        "title": "Task-compliance not declared",
                        "detail": "guess",
                        "link": "#acceptance-evidence",
                    }
                ],
                "white": [
                    {
                        "title": "Task-compliance passed",
                        "detail": "guess",
                        "link": "#acceptance-evidence",
                    }
                ],
                "accepted_head": accepted,
                "later_commits": [],
            }
        )
    )
    if compliance is not None:
        (artifacts / "task-compliance.json").write_text(
            json.dumps(
                {
                    **compliance,
                    "base": accepted,
                    "reviewed_head": accepted,
                    "tasks_sha256": "a" * 64,
                    "runs": [{"evidence": "review/logs"}],
                }
            )
        )
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    stub = tmp_path / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    body_path = tmp_path / "body.md"
    result = subprocess.run(
        [str(PACKAGE / "annotate-pr.sh"), str(artifacts), str(issue), "change", str(archive)],
        cwd=repo,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GH_BODY": str(body_path)},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    flags = json.loads((artifacts / "review-attention.json").read_text())
    items = [
        item
        for value in ("red", "orange", "yellow", "white")
        for item in flags[value]
        if item["title"].startswith("Task-compliance")
    ]
    assert len(items) == 1
    assert items[0]["title"] == title
    assert items[0] in flags[tier]
    if tier in ("white", "yellow"):
        assert items[0]["link"] == ""
    body = body_path.read_text()
    assert title in body
    if tier in ("white", "yellow"):
        assert f"- {title}:" in body
    if compliance is not None:
        assert f"base {accepted}" in body
        assert "Not covered by task-compliance: " in body
    assert all(sha[:12] in body for sha in later)
    # One "Commits after acceptance" item carries both the commits and the task-compliance note.
    later_items = [item for item in flags["orange"] if item["title"] == "Commits after acceptance"]
    assert len(later_items) == 1
    assert all(sha[:12] in later_items[0]["detail"] for sha in later)
    if compliance is not None:
        assert "Not covered by task-compliance: " in later_items[0]["detail"]
    assert body.count("[Commits after acceptance]") == 1
    assert f"### 🟠 Orange ({len(flags['orange'])})" in body
    payload = {
        "contract": "factory-feature/1",
        "outcome_path": str(artifacts / "feature-outcome.json"),
        "validator_status": "passed",
        "ci_status": "passed",
        "branch_name": "feature",
        "pr_details": json.dumps({"url": "https://example.test/pr", "number": 9}),
        "review_attention_counts": str(artifacts / "review-attention.json"),
        "task_compliance": str(artifacts / "task-compliance.json"),
    }
    result = run(str(PACKAGE / "record-outcome.sh"), cwd=repo, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    outcome = json.loads((artifacts / "feature-outcome.json").read_text())
    assert outcome["validator"] == {"checks": "passed", "status": status}
    assert outcome["review_attention_counts"]["red"] == len(flags["red"])
    assert f"### 🔴 Red ({len(flags['red'])})" in body
    assert read_interpreted_outcome(artifacts, "factory-feature/1").outcome is not None
    if compliance is None:
        assert outcome["task_compliance"] == {
            "result": "not-run",
            "reason": "no task-compliance record",
        }
