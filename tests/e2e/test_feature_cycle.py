"""E2E-001: a writer's Feature travels through host launch, PR, sync, and cleanup."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e import test_fix_cycle as fix_cycle


def test_feature_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            "review_attention_counts": {"red": 2, "orange": 3, "yellow": 12},
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
        assert any("2 red flags, 3 orange flags, 12 yellow items" in c for c in h.comments())
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
