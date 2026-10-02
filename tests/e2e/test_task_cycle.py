"""Task journeys through the CLI, store, host launcher and fake GitHub board."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

from agent_factory.work_kinds.pull_request.launch import HOST_NOTE
from tests.e2e.test_fix_cycle import REPOSITORY, Harness, _commit, _git


def task_pr(branch: str) -> str:
    return json.dumps(
        {
            "contract": "factory-task/1",
            "outcome": "pull-request",
            "pr": {
                "url": f"https://github.com/{REPOSITORY}/pull/214",
                "number": 214,
                "branch": branch,
                "head_sha": "f" * 40,
            },
            "validator": {"status": "passed"},
            "ci": {"status": "passed"},
        }
    )


def test_task_happy_path(tmp_path: Path) -> None:
    h = Harness(tmp_path, factory_owner=False, execution="host", kind="task")
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    try:
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None and run.kind == "task"
        assert "task slot:" in h.cli("status")
        assert h.field(h.shared.project.owner.id) == h.shared.project.owner.option("factory")
        args = json.loads((artifact / "runner-args.json").read_text())
        assert args[:2] == ["run", "factory-task"]
        assert f"branch_name={h.branch_for(claim.id)}" in args
        assert "contract_version=factory-task/1" in args
        clone = h.root / "clones" / claim.id / "0" / "repo"
        assert (clone / ".agent-runner/workflows/factory-task-v1.0.yaml").is_file()
        assert "fix-token-value" not in json.dumps(run.plan)
        assert "host" in json.dumps(run.plan)
        h.finish(artifact, task_pr(h.branch_for(claim.id)))
        h.tick()
        claim = h.store.get_claim(claim.id)
        assert claim is not None and claim.lifecycle == "settled"
        assert h.status() == "review"
        assert h.field(h.shared.project.verdict.id) == h.shared.project.verdict.option(
            "pending-human-review"
        )
        assert any("pull/214" in body and HOST_NOTE in body for body in h.comments())
        merged = _commit(tmp_path / "work", "chore: maintenance")
        _git(tmp_path / "work", "push", "-q", "origin", "main")
        h.update(pr_states={"214": {"state": "MERGED", "mergedAt": "2026-01-02T00:00:00Z"}})
        h.tick()
        assert _git(h.working, "merge-base", "--is-ancestor", merged, "HEAD") == ""
        assert h.state()["issue"]["state"] == "closed"
        h.set_status("done")
        h.tick()
        assert not (h.root / "clones" / claim.id).exists()
        assert (artifact / "task-outcome.json").exists()
    finally:
        (artifact / "finish").touch()
        h.store.close()


def test_declined_task_blocks_then_relaunches(tmp_path: Path) -> None:
    h = Harness(tmp_path, execution="host", kind="task")
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    second: Path | None = None
    try:
        h.finish(
            artifact,
            json.dumps(
                {
                    "contract": "factory-task/1",
                    "outcome": "needs-input",
                    "reasons": ["threshold is unbounded; belongs in a Feature"],
                }
            ),
        )
        h.tick()
        claim = h.store.get_claim(run.claim_id)
        assert claim is not None and claim.lifecycle == "blocked"
        assert h.status() == "running"
        assert "task slot: free" in h.cli("status")
        assert "needs-input" in h.state()["labels"]
        assert any("No branch was pushed." in body for body in h.comments())
        assert not h.store.nonterminal_runs(kind="task")
        assert not _git(h.origin, "for-each-ref", "--format=%(refname)", "refs/heads/factory/task")
        data = h.state()
        data["comments"].append(
            {
                "id": 950,
                "body": "Use a 2% baseline",
                "user": {"login": "writer"},
                "created_at": "2099-01-01T00:00:00Z",
            }
        )
        h.board.write_text(json.dumps(data))
        h.tick()
        retry = h.active_run()
        assert retry.reason == "unblock" and retry.attempt_number == 1
        second = h.wait_started(retry)
        issue = json.loads((second / "input/issue.json").read_text())
        assert [comment["body"] for comment in issue["comments"]] == ["Use a 2% baseline"]
        assert "needs-input" not in h.state()["labels"]
        h.finish(second, task_pr(h.branch_for(claim.id)))
        h.tick()
        assert h.status() == "review"
    finally:
        (artifact / "finish").touch()
        if second is not None:
            (second / "finish").touch()
        h.store.close()


def test_three_kinds_admitted_independently(tmp_path: Path) -> None:
    h = Harness(tmp_path, execution="host", kind="task")
    shared = h.config.parent / "shared.toml"
    shared.write_text(
        shared.read_text()
        + (
            '\n[feature]\ncontract = "factory-feature/1"\n'
            '[feature.defaults]\nlead = "codex:test:high"\nimplementor = "codex:test:high"\n'
            'tester = "codex:test:high"\ncrosscheck = "codex:test:high"\n'
        )
    )
    board = h.state()
    template = board["items"][0]
    for number, kind in ((2, "Bug"), (3, "Feature")):
        item = json.loads(json.dumps(template))
        item["id"] = f"P{number}"
        item["content"]["id"] = f"I{number}"
        item["content"]["number"] = number
        item["content"]["title"] = f"{kind} fixture"
        item["content"]["issueType"]["name"] = kind
        board["items"].append(item)
    h.board.write_text(json.dumps(board))
    try:
        for _ in range(3):
            h.tick()
        runs = h.store.nonterminal_runs()
        assert {run.kind for run in runs} == {"fix", "feature", "task"}
        for run in runs:
            artifact = h.wait_started(run)
            args = json.loads((artifact / "runner-args.json").read_text())
            assert args[:2] == ["run", f"factory-{run.kind}"]
            assert any(arg.startswith(f"branch_name=factory/{run.kind}-") for arg in args)
            assert (
                h.root
                / "clones"
                / run.claim_id
                / "0"
                / "repo"
                / ".agent-runner/workflows"
                / f"factory-{run.kind}-v1.0.yaml"
            ).is_file()
    finally:
        for run in h.store.nonterminal_runs():
            evidence = Path(run.evidence_path) / f"attempt-{run.attempt_number + 1}"
            (evidence / "finish").write_text("crash")
        h.store.close()
