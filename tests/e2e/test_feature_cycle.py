"""E2E-001: a writer's Feature travels through host launch, PR, sync, and cleanup."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from tests.e2e import test_fix_cycle as fix_cycle


@pytest.mark.parametrize(
    "compliance,comment",
    [
        ({"result": "not-run", "reason": "Trusted"}, "Task-compliance did not run: Trusted."),
        ({"result": "failed"}, "Task-compliance violations remain."),
        ({"result": "passed"}, ""),
    ],
)
def test_feature_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compliance: dict[str, str], comment: str
) -> None:
    monkeypatch.setattr(
        fix_cycle, "RUNNER", fix_cycle.RUNNER.replace("fix-outcome.json", "feature-outcome.json")
    )
    h = fix_cycle.Harness(tmp_path, factory_owner=False, execution="host")
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(
        shared_path.read_text()
        + '\n[feature]\ncontract = "factory-feature/1"\n'
        + '[feature.defaults]\nlead = "codex:test:high"\n'
        + 'implementor = "codex:test:high"\ntester = "codex:test:high"\n'
        + 'crosscheck = "codex:test:high"\n'
    )
    config = h.config.read_text()
    h.config.write_text(
        config.replace("[credentials]", '[feature]\nexecution = "host"\n[credentials]')
    )
    board = h.state()
    board["items"][0]["content"]["issueType"]["name"] = "Feature"
    h.board.write_text(json.dumps(board))
    artifact = None
    try:
        h.tick()
        assert h.field(h.shared.project.owner.id) == h.shared.project.owner.option("factory")
        runs = h.store.nonterminal_runs(kind="feature")
        assert len(runs) == 1
        run = runs[0]
        assert fix_cycle.FIX_TOKEN not in json.dumps(run.plan)
        hints = run.plan["ownership_hints"]
        assert isinstance(hints, dict)
        assert hints["backend"] == "host"
        assert hints["runner_version"] == "stub-runner 1.0"
        assert hints["runner_executable"]
        assert hints["session_dir"]
        artifact = h.wait_started(run)
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None
        branch = f"factory/feature-1-{claim.id[:8]}"
        args = json.loads((artifact / "runner-args.json").read_text())
        assert "factory-feature" in args
        assert f"branch_name={branch}" in args
        assert f"change_name=feature-1-{claim.id[:8]}" in args
        assert "resume_from=" in args and "prior_branch=" in args
        staged = h.root / "clones" / claim.id / "0" / "repo" / ".agent-runner" / "workflows"
        assert (staged / "factory-feature-v1.0.yaml").is_file()
        assert (staged / "factory-define-v1.0.yaml").is_file()
        assert any("starts fresh" in comment for comment in h.comments())
        assert any("Feature inputs accepted and frozen." in comment for comment in h.comments())
        assert "feature slot:" in h.cli("status")
        shared_path.write_text(shared_path.read_text().split("\n[feature]\n", 1)[0])
        outcome: dict[str, object] = {
            "contract": "factory-feature/1",
            "outcome": "pull-request",
            "reasons": [],
            "review_attention_counts": {
                "red": 1 if compliance["result"] != "passed" else 0,
                "orange": 3,
                "yellow": 12,
            },
            "validator": {
                "checks": "passed",
                "status": {"not-run": "incomplete", "failed": "review-failed", "passed": "passed"}[
                    compliance["result"]
                ],
            },
            "task_compliance": compliance,
            "pr": {
                "url": "https://github.com/example/work/pull/214",
                "number": 214,
                "branch": branch,
                "head_sha": "f" * 40,
            },
        }
        h.finish(artifact, json.dumps(outcome))
        h.tick()
        assert h.status() == "review"
        assert h.field(h.shared.project.verdict.id) == h.shared.project.verdict.option(
            "pending-human-review"
        )
        red = 1 if compliance["result"] != "passed" else 0
        assert any(f"{red} red flags, 3 orange flags, 12 yellow items" in c for c in h.comments())
        if comment:
            assert any(comment in c for c in h.comments())
        else:
            assert all(
                "Task-compliance" not in c for c in h.comments() if "Pull request opened" in c
            )
        merged_sha = fix_cycle._commit(tmp_path / "work", "the feature")
        fix_cycle._git(tmp_path / "work", "push", "-q", "origin", "main")
        h.update(pr_states={"214": {"state": "MERGED", "mergedAt": "2026-01-02T00:00:00Z"}})
        h.tick()
        assert fix_cycle._git(h.working, "merge-base", "--is-ancestor", merged_sha, "HEAD") == ""
        assert h.state()["issue"]["state"] == "closed"
        h.set_status("done")
        h.tick()
        assert not (h.root / "clones" / claim.id / "0").exists()
        assert not (h.root / "private" / run.id).exists()
        assert (artifact / "feature-outcome.json").is_file()
    finally:
        if artifact is not None:
            (artifact / "finish").touch()
        h.store.close()


def test_stopped_feature_resolves_new_target_on_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fix_cycle, "RUNNER", fix_cycle.RUNNER.replace("fix-outcome.json", "feature-outcome.json")
    )
    h = fix_cycle.Harness(tmp_path, factory_owner=False, execution="host")
    shared = tmp_path / "shared.toml"
    shared.write_text(
        shared.read_text()
        + '\n[feature]\ncontract = "factory-feature/1"\n'
        + '[feature.defaults]\nlead = "codex:test:high"\n'
        + 'implementor = "codex:test:high"\ntester = "codex:test:high"\n'
        + 'crosscheck = "codex:test:high"\n'
    )
    h.config.write_text(
        h.config.read_text().replace(
            "[credentials]", '[feature]\nexecution = "host"\n[credentials]'
        )
    )
    board = h.state()
    board["items"][0]["content"]["issueType"]["name"] = "Feature"
    h.board.write_text(json.dumps(board))
    first: Path | None = None
    second: Path | None = None
    try:
        h.tick()
        run = h.store.nonterminal_runs(kind="feature")[0]
        first = h.wait_started(run)
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None
        revisions = cast(Mapping[str, object], claim.frozen_spec["revisions"])
        admission_value = revisions["target"]
        assert isinstance(admission_value, str)
        admission = admission_value
        branch = f"factory/feature-1-{claim.id[:8]}"
        work = tmp_path / "work"
        fix_cycle._git(work, "checkout", "-b", branch, str(admission))
        (work / "plan.txt").write_text("draft plan\n")
        fix_cycle._commit(work, "plan")
        fix_cycle._git(work, "push", "-q", "origin", branch)
        fix_cycle._git(work, "checkout", "main")
        h.finish(
            first,
            json.dumps(
                {
                    "contract": "factory-feature/1",
                    "outcome": "needs-input",
                    "stopped_step": "design",
                    "questions": ["Which direction?"],
                    "reasons": ["Which direction?"],
                    "direction_summary": "Drafted design.",
                    "branch": branch,
                }
            ),
        )
        h.tick()
        (work / "target-fix.txt").write_text("new main work\n")
        moved = fix_cycle._commit(work, "new main work")
        fix_cycle._git(work, "push", "-q", "origin", "main")
        board = h.state()
        board["comments"].append(
            {
                "id": 950,
                "body": "Use the proposed direction",
                "user": {"login": "writer"},
                "created_at": "2099-01-01T00:00:00Z",
            }
        )
        h.board.write_text(json.dumps(board))
        h.tick()
        retry = h.store.nonterminal_runs(kind="feature")[0]
        second = h.wait_started(retry)
        args = json.loads((second / "runner-args.json").read_text())
        assert "resume_from=design" in args
        assert f"base_head={moved}" in args
        provenance = json.loads((second / "host-provenance.json").read_text())
        assert provenance["target_at_admission"] == admission
        assert provenance["base_head"] == moved
        resumed_claim = h.store.get_claim(claim.id)
        assert resumed_claim is not None
        assert resumed_claim.frozen_spec["revisions"] == revisions
        assert any(
            f"merges main@{moved[:7]}" in body
            for body in [*h.comments(), *(event.body for event in h.store.pending_events(claim.id))]
        )
        assert fix_cycle.FIX_TOKEN not in json.dumps(retry.plan)
    finally:
        for artifact in (first, second):
            if artifact is not None:
                (artifact / "finish").touch()
        h.store.close()


@pytest.mark.parametrize("case", ["implement", "fresh", "plain", "superseded", "empty"])
def test_blocked_repair_supervision_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """INT-002/005 and E2E-001: outcome consumption, recovery policy and writer resume."""
    from tests.fixtures.repair_block.audits import PATH, REASON, blocked, event

    path = "define, sub:factory-define, specs, check" if case == "fresh" else PATH
    audit = blocked(
        path,
        response="REPAIR_BLOCKED" if case == "empty" else REASON + "\nREPAIR_BLOCKED",
        declaration=case != "plain",
    )
    if case == "superseded":
        audit = (
            blocked()
            + event(PATH, "step_start")
            + "\n"
            + event(PATH, "step_end", outcome="success")
            + "\n"
            + blocked("unrelated", declaration=False)
        )
    injected = f"""
if script.strip() == 'repair-test':
    branch = params['branch_name']
    catalog = pathlib.Path('.agent-runner/workflows')
    subprocess.run(['git', 'remote', 'set-url', 'origin', {str(tmp_path / "work-origin.git")!r}],
                   check=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run(['sh', str(catalog / 'prepare-branch.sh'),
                    branch, head, '', '', str(out)], check=True)
    if {case != "fresh"!r}:
        subprocess.run(['sh', str(catalog / 'checkpoint.sh'), 'planned', branch], check=True)
    (sess / 'audit.log').write_text({audit!r})
    sys.exit(1)
"""
    runner = fix_cycle.RUNNER.replace(
        "if script.strip() == 'crash':", injected + "\nif script.strip() == 'crash':"
    )
    monkeypatch.setattr(fix_cycle, "RUNNER", runner)
    h = fix_cycle.Harness(tmp_path, factory_owner=False, execution="host")
    shared = tmp_path / "shared.toml"
    shared.write_text(
        shared.read_text()
        + '\n[feature]\ncontract = "factory-feature/1"\n'
        + '[feature.defaults]\nlead = "codex:test:high"\n'
        + 'implementor = "codex:test:high"\ntester = "codex:test:high"\n'
        + 'crosscheck = "codex:test:high"\n'
    )
    h.config.write_text(
        h.config.read_text().replace(
            "[credentials]", '[feature]\nexecution = "host"\n[credentials]'
        )
    )
    board = h.state()
    board["items"][0]["content"]["issueType"]["name"] = "Feature"
    h.board.write_text(json.dumps(board))
    first = second = None
    try:
        h.tick()
        initial_run = h.store.nonterminal_runs(kind="feature")[0]
        first = h.wait_started(initial_run)
        branch = f"factory/feature-1-{initial_run.claim_id[:8]}"
        h.finish(first, "repair-test")
        finished = h.store.get_run(initial_run.id)
        assert finished is not None
        if case in ("plain", "superseded", "empty"):
            assert finished.status == "interrupted"
            assert finished.result["reason"] == "owned process exited without durable result"
            assert not (first / "feature-outcome.json").exists()
            h.tick()
            retry = h.store.nonterminal_runs(kind="feature")
            assert len(retry) == 1
            second = h.wait_started(retry[0])
            assert len(h.store.runs_for_claim(initial_run.claim_id)) == 2
            return
        assert finished.status == "completed"
        assert finished.result["outcome"] == "needs-input"
        assert finished.result["blocked_step"] == path
        h.tick()
        claim = h.store.get_claim(initial_run.claim_id)
        assert claim is not None and claim.lifecycle == "blocked"
        assert h.status() == "running"
        assert "needs-input" in h.state()["labels"]
        assert not h.store.nonterminal_runs(kind="feature")
        assert len(h.store.runs_for_claim(claim.id)) == 1
        stop_comments = [body for body in h.comments() if "Needs input." in body]
        assert len(stop_comments) == 1
        assert REASON in stop_comments[0] and path in stop_comments[0]
        if case == "fresh":
            assert "No branch was published; the next attempt starts fresh." in stop_comments[0]
        else:
            assert "resumes at implement" in stop_comments[0]
            assert f"/tree/{branch}" in stop_comments[0]
        board = h.state()
        board["comments"].append(
            {
                "id": 950,
                "body": "Cause fixed; resume",
                "user": {"login": "writer"},
                "created_at": "2099-01-01T00:00:00Z",
            }
        )
        h.board.write_text(json.dumps(board))
        h.tick()
        resumed = h.store.nonterminal_runs(kind="feature")[0]
        second = h.wait_started(resumed)
        args = json.loads((second / "runner-args.json").read_text())
        assert ("resume_from=" + ("implement" if case == "implement" else "")) in args
        assert f"branch_name={branch}" in args
        assert "prior_branch=" in args
        if case == "fresh":
            assert not (second / "resume.json").exists()
            assert all("unavailable" not in body for body in h.comments())
            assert sum("starts fresh" in body for body in h.comments()) >= 2
    finally:
        for artifact in (first, second):
            if artifact is not None:
                (artifact / "finish").touch()
        h.store.close()
