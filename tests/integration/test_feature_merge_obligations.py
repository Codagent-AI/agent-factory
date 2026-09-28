"""Real Git coverage for the feature target merge and its resolution guard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace
from tests.integration.test_feature_workflow_scripts import PACKAGE, git, repository, run


def commit_file(repo: Path, name: str, value: str, message: str) -> str:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value)
    git(repo, "add", name)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def prepare(
    repo: Path, evidence: Path, branch: str, admission: str, resume: str, prior: str, base: str = ""
) -> str:
    evidence.mkdir()
    result = run(
        str(PACKAGE / "prepare-branch.sh"),
        branch,
        admission,
        resume,
        prior,
        str(evidence),
        base,
        cwd=repo,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_int001_archive_stop_uses_target_fix_and_ignores_merged_checkpoint(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    admission = commit_file(repo, "openspec/specs/live/spec.md", "blocked\n", "living spec")
    git(repo, "push", "origin", "main")
    git(repo, "checkout", "-b", "claim")
    planned = commit_file(repo, "plan.md", "planned\n", "plan\n\nFactory-Checkpoint: planned")
    before = commit_file(
        repo, "implementation.py", "done\n", "implement\n\nFactory-Checkpoint: implemented"
    )
    git(repo, "push", "origin", "claim")
    git(repo, "checkout", "main")
    commit_file(repo, "openspec/specs/live/spec.md", "fixed\n", "fix living spec")
    git(repo, "checkout", "-b", "unrelated")
    commit_file(repo, "other.md", "archived\n", "other\n\nFactory-Checkpoint: archived")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "--no-edit", "unrelated")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "main")
    evidence = tmp_path / "evidence"
    assert prepare(repo, evidence, "claim", admission, "archive", "", base) == "archive"
    assert git(repo, "show", "-s", "--format=%P", "HEAD") == f"{before} {base}"
    assert (repo / "openspec/specs/live/spec.md").read_text() == "fixed\n"
    assert (repo / "implementation.py").read_text() == "done\n"
    assert planned != before
    record = json.loads((evidence / "base-merge.json").read_text())
    assert (record["target_at_admission"], record["base_head"], record["status"]) == (
        admission,
        base,
        "merged",
    )
    assert not (evidence / "resume.json").exists()
    git(repo, "push", "origin", "claim")
    mirror = tmp_path / "storage/mirrors/example__work.git"
    mirror.parent.mkdir(parents=True)
    git(tmp_path, "clone", "--mirror", str(remote), str(mirror))
    workspace = PullRequestWorkspace(tmp_path / "storage", repo, repo)
    assert (
        workspace.feature_checkpoint("example/work", "claim", base_sha=admission) == "implemented"
    )


@pytest.mark.parametrize(
    "case",
    [
        "current",
        "ahead",
        "ancestor",
        "missing_prior",
        "missing_own",
        "legacy_resume",
        "legacy_continuation",
        "finalize_current",
        "finalize_merged",
    ],
)
def test_int002_merge_topologies_and_fallback(tmp_path: Path, case: str) -> None:
    repo, _ = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    before = commit_file(repo, "work.txt", "work\n", "branch work")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    if case not in {"current", "finalize_current", "missing_prior", "missing_own"}:
        if case == "ancestor":
            git(repo, "merge", "--ff-only", "prior")
        commit_file(repo, "base.txt", "new target\n", "advance target")
        git(repo, "push", "origin", "main")
    base = git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    branch = "claim" if case in {"missing_prior", "legacy_continuation"} else "prior"
    prior = (
        "missing" if case == "missing_prior" else "prior" if case == "legacy_continuation" else ""
    )
    resume = "finalize" if case.startswith("finalize") else "implement"
    if case == "missing_own":
        branch = "missing"
    source = "" if case.startswith("legacy") else base
    target = base if case == "legacy_continuation" else admission
    effective = prepare(repo, evidence, branch, target, resume, prior, source)
    if case in {"missing_prior", "missing_own"}:
        assert effective == ""
        assert json.loads((evidence / "resume.json").read_text())["fallback"]
        assert git(repo, "rev-parse", "HEAD") == target
        return
    assert not (evidence / "resume.json").exists()
    if case == "legacy_resume":
        assert not (evidence / "base-merge.json").exists()
        assert git(repo, "rev-parse", "HEAD") == before
    else:
        record = json.loads((evidence / "base-merge.json").read_text())
        expected = "current" if case in {"current", "finalize_current"} else "merged"
        assert record["status"] == expected
        if expected == "current":
            assert git(repo, "rev-parse", "HEAD") == before
        else:
            assert git(repo, "show", "-s", "--format=%P", "HEAD") == f"{before} {base}"
            assert (repo / "work.txt").read_text() == "work\n"
            assert (repo / "base.txt").read_text() == "new target\n"
    assert effective == ("verify" if case == "finalize_merged" else resume)


def test_continuation_resumes_own_branch_when_prior_was_deleted(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    continuation_head = commit_file(repo, "plan.txt", "planned\n", "prior plan")
    git(repo, "push", "origin", "prior:refs/heads/claim")
    git(repo, "checkout", "main")
    base = commit_file(repo, "base.txt", "current\n", "advance target")
    evidence = tmp_path / "evidence"

    assert prepare(repo, evidence, "claim", admission, "implement", "prior", base) == "implement"
    assert git(repo, "show", "-s", "--format=%P", "HEAD") == f"{continuation_head} {base}"
    assert not (evidence / "resume.json").exists()
    assert git(remote, "rev-parse", "refs/heads/claim") == continuation_head


@pytest.mark.parametrize(
    "state",
    [
        "unfinished",
        "unmerged",
        "markers",
        "dirty",
        "missing_base",
        "reset_base",
        "wrong_parents",
        "missing_first_parent",
        "take_base_file",
        "delete_branch_file",
        "resolved",
        "follow_up",
    ],
)
def test_int005_merge_guard_states(tmp_path: Path, state: str) -> None:
    repo, _ = repository(tmp_path)
    commit_file(repo, "work.txt", "original\n", "common work")
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    commit_file(repo, "choice.txt", "prior\n", "prior choice")
    commit_file(repo, "work.txt", "branch\n", "branch work")
    before = commit_file(repo, "branch-only.txt", "keep\n", "branch file")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target choice")
    evidence = tmp_path / "evidence"
    assert prepare(repo, evidence, "claim", admission, "implement", "prior", base) == "implement"
    assert git(repo, "rev-parse", "MERGE_HEAD") == base
    original = (evidence / "base-merge.json").read_text()
    if state == "unmerged":
        git(repo, "merge", "--abort")
        prior_blob = git(repo, "rev-parse", f"{before}:choice.txt")
        base_blob = git(repo, "rev-parse", f"{base}:choice.txt")
        index = (
            f"0 {'0' * 40}\tchoice.txt\n"
            f"100644 {prior_blob} 2\tchoice.txt\n"
            f"100644 {base_blob} 3\tchoice.txt\n"
        )
        indexed = run("git", "update-index", "--index-info", input=index, cwd=repo)
        assert indexed.returncode == 0, indexed.stderr
        assert git(repo, "ls-files", "-u")
        assert not run("git", "rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=repo).stdout.strip()
    elif state != "unfinished":
        if state in {"missing_base", "reset_base", "wrong_parents", "missing_first_parent"}:
            git(repo, "merge", "--abort")
            if state == "reset_base":
                git(repo, "reset", "--hard", base)
            elif state == "wrong_parents":
                tree = git(repo, "rev-parse", "HEAD^{tree}")
                extra = git(repo, "commit-tree", tree, "-p", base, "-m", "extra base commit")
                fabricated = git(
                    repo,
                    "commit-tree",
                    tree,
                    "-p",
                    before,
                    "-p",
                    extra,
                    "-m",
                    "wrong second parent",
                )
                git(repo, "reset", "--hard", fabricated)
            elif state == "missing_first_parent":
                tree = git(repo, "rev-parse", "HEAD^{tree}")
                fabricated = git(
                    repo, "commit-tree", tree, "-p", base, "-p", before, "-m", "reversed merge"
                )
                git(repo, "reset", "--hard", fabricated)
        else:
            (repo / "choice.txt").write_text(
                "<<<<<<< prior\nprior\n=======\ntarget\n>>>>>>> target\n"
                if state == "markers"
                else "both\n"
            )
            git(repo, "add", "choice.txt")
            if state == "take_base_file":
                (repo / "work.txt").write_text("original\n")
                git(repo, "add", "work.txt")
            if state == "delete_branch_file":
                git(repo, "rm", "branch-only.txt")
            git(repo, "commit", "--no-edit")
            if state == "dirty":
                (repo / "work.txt").write_text("dirty\n")
            if state == "follow_up":
                commit_file(repo, "follow-up.txt", "fix\n", "follow-up")
    result = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    record = json.loads((evidence / "base-merge.json").read_text())
    if state in {"resolved", "follow_up"}:
        assert result.returncode == 0, result.stderr
        assert record["status"] == "resolved"
        resolution = git(repo, "rev-parse", "HEAD^" if state == "follow_up" else "HEAD")
        assert record["merge_commit"] == resolution
        assert record["follow_up_commits"] == (
            [git(repo, "rev-parse", "HEAD")] if state == "follow_up" else []
        )
    else:
        assert result.returncode != 0, state
        assert (evidence / "base-merge.json").read_text() == original


def test_merge_guard_accepts_follow_up_changes_outside_conflicts(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    commit_file(repo, "choice.txt", "prior\n", "prior choice")
    commit_file(repo, "unconflicted.txt", "before\n", "unconflicted work")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target choice")
    evidence = tmp_path / "evidence"
    prepare(repo, evidence, "claim", admission, "implement", "prior", base)
    (repo / "choice.txt").write_text("both\n")
    git(repo, "add", "choice.txt")
    git(repo, "commit", "--no-edit")
    resolution = git(repo, "rev-parse", "HEAD")
    (repo / "unconflicted.txt").write_text("after\n")
    (repo / "new.txt").write_text("new\n")
    git(repo, "add", "unconflicted.txt", "new.txt")
    git(repo, "commit", "-m", "validator fix")
    follow_up = git(repo, "rev-parse", "HEAD")

    checked = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert checked.returncode == 0, checked.stderr
    record = json.loads((evidence / "base-merge.json").read_text())
    assert record["status"] == "resolved"
    assert record["merge_commit"] == resolution
    assert record["follow_up_commits"] == [follow_up]


def test_merge_guard_allows_markdown_setext_underline(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    commit_file(repo, "choice.md", "Original\n", "common")
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    commit_file(repo, "choice.md", "Prior\n", "prior choice")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.md", "Target\n", "target choice")
    evidence = tmp_path / "evidence"
    prepare(repo, evidence, "claim", admission, "implement", "prior", base)
    (repo / "choice.md").write_text("Merged\n=======\n")
    git(repo, "add", "choice.md")
    git(repo, "commit", "--no-edit")

    checked = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert checked.returncode == 0, checked.stderr


def test_merge_guard_rejects_markers_added_in_follow_up(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    commit_file(repo, "choice.txt", "prior\n", "prior choice")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target choice")
    evidence = tmp_path / "evidence"
    prepare(repo, evidence, "claim", admission, "implement", "prior", base)
    (repo / "choice.txt").write_text("both\n")
    git(repo, "add", "choice.txt")
    git(repo, "commit", "--no-edit")
    commit_file(
        repo, "new.txt", "<<<<<<< branch\nprior\n=======\ntarget\n>>>>>>> base\n", "bad fix"
    )

    checked = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert checked.returncode != 0
    assert "conflict markers remain" in checked.stderr


def test_fresh_definition_merge_stop_restarts_definition(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    before = commit_file(repo, "choice.txt", "prior\n", "prior")
    git(repo, "push", "origin", "prior")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target")
    evidence = tmp_path / "evidence"
    assert prepare(repo, evidence, "claim", admission, "", "prior", base) == ""
    conflict = json.loads((evidence / "merge-conflict.json").read_text())
    assert conflict["resume_from"] == ""
    (evidence / "merge-stop.json").write_text(
        json.dumps(
            {
                "questions": ["Choose one"],
                "direction_summary": "Need a choice",
            }
        )
    )
    stopped = run(str(PACKAGE / "record-merge-stop.sh"), str(evidence), "claim", cwd=repo)
    assert stopped.returncode == 0, stopped.stderr
    # A merge with no resume point belongs to a fresh definition (a continuation retried after
    # a preflight stop), so the next attempt restarts definition rather than skipping it.
    outcome = json.loads((evidence / "feature-outcome.json").read_text())
    assert outcome["stopped_step"] == "proposal"
    assert git(remote, "rev-parse", "refs/heads/claim") == before


def test_fresh_definition_on_pushed_continuation_branch_pushes_fast_forward(
    tmp_path: Path,
) -> None:
    """After a preflight stop, a continuation whose branch was pushed at the prior head
    starts definition afresh on that branch, so its first checkpoint push fast-forwards."""
    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "prior")
    prior_head = commit_file(repo, "plan.txt", "prior plan\n", "prior plan")
    git(repo, "push", "origin", "prior")
    # An earlier merge stop pushed the prior head as the claim's own branch.
    git(repo, "push", "origin", "prior:refs/heads/claim")
    git(repo, "checkout", "main")
    base = commit_file(repo, "target.txt", "target\n", "target")
    evidence = tmp_path / "evidence"
    assert prepare(repo, evidence, "claim", admission, "", "prior", base) == ""
    assert git(repo, "branch", "--show-current") == "claim"
    commit_file(repo, "proposal.md", "fresh proposal\n", "fresh definition")
    pushed = run(str(PACKAGE / "checkpoint.sh"), "planned", "claim", cwd=repo)
    assert pushed.returncode == 0, pushed.stderr
    head = git(remote, "rev-parse", "refs/heads/claim")
    assert run("git", "merge-base", "--is-ancestor", prior_head, head, cwd=remote).returncode == 0
    assert run("git", "merge-base", "--is-ancestor", base, head, cwd=remote).returncode == 0


@pytest.mark.parametrize("archived", [False, True])
def test_int003_resolved_continuation_renames_change_after_merge(
    tmp_path: Path, archived: bool
) -> None:
    repo, _ = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    prior = "factory/feature-12-prior"
    branch = "factory/feature-12-new"
    git(repo, "checkout", "-b", prior)
    commit_file(repo, "choice.txt", "prior\n", "prior choice")
    old_change = (
        "openspec/changes/archive/2026-09-28-feature-12-prior"
        if archived
        else "openspec/changes/feature-12-prior"
    )
    commit_file(repo, f"{old_change}/tasks.md", "implementation survives\n", "prior plan")
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", prior)
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target choice")
    evidence = tmp_path / "evidence"
    assert prepare(
        repo, evidence, branch, admission, "verify" if archived else "implement", prior, base
    ) == ("verify" if archived else "implement")
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "rev-parse", "MERGE_HEAD") == base
    assert not (evidence / "resume.json").exists()
    (repo / "choice.txt").write_text("prior and target\n")
    git(repo, "add", "choice.txt")
    git(repo, "commit", "--no-edit")
    checked = run(str(PACKAGE / "check-merge.sh"), str(evidence), cwd=repo)
    assert checked.returncode == 0, checked.stderr
    renamed = run(str(PACKAGE / "continue-change.sh"), prior, "feature-12-new", cwd=repo)
    assert renamed.returncode == 0, renamed.stderr
    new_change = old_change.replace("feature-12-prior", "feature-12-new")
    assert (repo / new_change / "tasks.md").read_text() == "implementation survives\n"
    assert not (repo / old_change).exists()
    assert run("git", "merge-base", "--is-ancestor", base, "HEAD", cwd=repo).returncode == 0


def test_int004_same_claim_merge_stop_preserves_remote(tmp_path: Path) -> None:
    repo, remote = repository(tmp_path)
    admission = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "claim")
    before = commit_file(repo, "choice.txt", "claim\n", "claim choice")
    git(repo, "push", "origin", "claim")
    git(repo, "checkout", "main")
    base = commit_file(repo, "choice.txt", "target\n", "target choice")
    evidence = tmp_path / "evidence"
    assert prepare(repo, evidence, "claim", admission, "implement", "", base) == "implement"
    (evidence / "merge-stop.json").write_text(
        json.dumps(
            {
                "questions": ["Which choice?"],
                "direction_summary": "Need direction",
            }
        )
    )
    stopped = run(str(PACKAGE / "record-merge-stop.sh"), str(evidence), "claim", cwd=repo)
    assert stopped.returncode == 0, stopped.stderr
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(remote, "rev-parse", "refs/heads/claim") == before
    assert not run("git", "rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=repo).stdout.strip()
    assert git(repo, "status", "--porcelain") == ""
    outcome = json.loads((evidence / "feature-outcome.json").read_text())
    assert outcome["stopped_step"] == "implement"
    assert "choice.txt" in outcome["questions"][0]
