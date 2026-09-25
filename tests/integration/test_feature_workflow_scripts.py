"""Real git boundaries for the packaged feature workflow helpers."""

from __future__ import annotations

import json
import subprocess
from importlib.resources import files
from pathlib import Path

from agent_factory.work_kinds.pull_request.kinds import FEATURE_STAGED_FILES
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome

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


def test_feature_files_are_listed_and_exist() -> None:
    for name in (
        "factory-feature-v1.0.yaml",
        "factory-define-v1.0.yaml",
        "factory-define-rules.md",
        "prepare-branch.sh",
        "factory-resume-skip.sh",
        "record-stop.sh",
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
        assert run(script, step, "factory/feature-1-prior", cwd=repo).returncode == 1


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
    stub.write_text(
        '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = list ]; then\n'
        '  echo \'[{"number":9,"url":"https://github.com/o/r/pull/9"}]\'\n'
        'else cat "$5" > "$GH_BODY"; fi\n'
    )
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
    assert "Refs #7" in rendered and "agent-factory:claim:claim-7" in rendered
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
    attention = {
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
    stub.write_text(
        '#!/bin/sh\nif [ "$1" = pr ] && [ "$2" = list ]; then\n'
        '  echo \'[{"number":9,"url":"https://github.com/o/r/pull/9"}]\'\n'
        "fi\n"
    )
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
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


def test_prepare_branch_conflict_falls_back_to_fresh(tmp_path: Path) -> None:
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
    assert result.stdout == ""
    assert git(repo, "rev-parse", "HEAD") == target
    assert json.loads((evidence / "resume.json").read_text())["fallback"]


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
        "change_name": "feature-12-bbbbbbbb",
    }
    result = run(str(PACKAGE / "prepare-branch.sh"), cwd=repo, input=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "implement"
    assert git(repo, "branch", "--show-current") == "factory/feature-12-bbbbbbbb"
    changes = repo / "openspec" / "changes"
    assert (changes / "feature-12-bbbbbbbb" / "tasks.md").read_text() == "- [ ] only task\n"
    assert not (changes / "feature-12-aaaaaaaa").exists()
    assert git(repo, "status", "--porcelain") == ""


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
