"""INT-002: real Runner closing steps, with hermetic classifying and repair sessions."""

from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent_factory.work_kinds.pull_request.kinds import FEATURE
from agent_factory.work_kinds.pull_request.launch import stage_workflow_into, staged_config_text
from tests.integration.test_feature_workflow_catalog import PACKAGE, RUNNER, suitable_runner

CLAUDE_STUB = """#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
artifact = Path(os.environ["CLASSIFICATION_ARTIFACT"])
log = artifact / "calls.jsonl"
calls = log.read_text().splitlines() if log.exists() else []
prompt = sys.argv[-1]
repair = bool(calls)
assert (artifact / "finalized").exists(), "classify ran before finalize"
with log.open("a") as f:
    f.write(json.dumps({"repair": repair, "prompt": prompt}) + "\\n")
mode = os.environ["REPAIR_MODE" if repair else "CLASSIFY_MODE"]
if mode == "fail":
    sys.exit(1)
record = artifact / "review-attention.json"
if mode == "truncated":
    record.write_text('{"red":')
else:
    accepted = (artifact / "accepted-head").read_text().strip()
    later = subprocess.check_output(
        ["git", "rev-list", "--reverse", accepted + "..HEAD"], text=True
    ).splitlines()
    orange = []
    if mode != "missing-item":
        size = subprocess.check_output(
            ["git", "diff", "--shortstat", accepted, "HEAD"], text=True
        ).strip()
        orange = [{"title": "Commits after acceptance",
                   "detail": ", ".join(later) + ": " + size
                             + ". Tests: fixture test covers the CI fix.",
                   "link": "fix.txt:1"}]
    record.write_text(json.dumps({"red": [], "orange": orange, "yellow": [], "white": [],
                                  "accepted_head": accepted, "later_commits": later}))
print(json.dumps({"type": "result", "result": "classification written"}))
"""


def closing_fragment(finalize_exit: int) -> dict[str, Any]:
    """Keep real step semantics, sessions and repair; replace only external effects."""
    workflow = cast(
        dict[str, Any], yaml.safe_load((PACKAGE / "factory-feature-v1.0.yaml").read_text())
    )
    steps = cast(list[dict[str, Any]], workflow["steps"])
    ids = [step["id"] for step in steps]
    steps = steps[ids.index("seed-ci-status") : ids.index("verify-outcome") + 1]
    replacements = {
        "finalize": (
            'printf "CI fix\\n" > fix.txt; git add fix.txt; git commit -m "CI fix" '
            '> /dev/null && touch "{{artifact_dir}}/finalized"; '
            f"exit {finalize_exit}"
        ),
        "annotate-pr": 'touch "{{artifact_dir}}/annotated"',
        "pr-details": "printf '%s' "
        + shlex.quote(
            json.dumps(
                {
                    "url": "https://example.test/pull/9",
                    "number": 9,
                    "headRefOid": "final-head",
                }
            )
        ),
    }
    for step in steps:
        if step["id"] in replacements:
            for key in ("workflow", "params", "script", "script_inputs"):
                step.pop(key, None)
            step["command"] = replacements[step["id"]]
    return {
        "name": "classification-order",
        "description": "hermetic closing steps",
        "hidden": True,
        "params": [
            {"name": name, "required": True}
            for name in (
                "artifact_dir",
                "validator_status",
                "effective_resume",
                "issue_file",
                "archived_dir",
                "branch_name",
                "change_name",
            )
        ],
        "sessions": workflow["sessions"],
        "steps": steps,
    }


@pytest.mark.darwin
@pytest.mark.parametrize("case", list("abcdefgh"))
def test_classification_after_finalization(tmp_path: Path, case: str) -> None:
    if platform.system() != "Darwin":
        pytest.skip("Runner session behavior requires macOS")
    if not suitable_runner():
        pytest.skip("installed Runner is missing or incompatible")
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.com"),
        ("commit", "--allow-empty", "-m", "Accepted"),
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    accepted = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "accepted-head").write_text(accepted)
    (artifacts / "task-compliance.json").write_text(
        json.dumps(
            {
                "result": "not-declared",
                "base": accepted,
                "reviewed_head": accepted,
                "tasks_sha256": "a" * 64,
            }
        )
    )
    preseed = {
        "contract": "factory-feature/1",
        "outcome": "needs-input",
        "reasons": ["definition stopped"],
        "questions": ["Which direction?"],
        "direction_summary": "definition",
        "stopped_step": "design",
        "resume": {"from": "design"},
    }
    outcome_path = artifacts / "feature-outcome.json"
    if case == "g":
        outcome_path.write_text(json.dumps(preseed))
    catalog = stage_workflow_into(repo / ".agent-runner/workflows", "factory-feature/1", FEATURE)
    fragment = catalog / "classification-order-v1.0.yaml"
    fragment.write_text(yaml.safe_dump(closing_fragment(1 if case in "ef" else 0)))
    (repo / ".agent-runner/config.yaml").write_text(
        staged_config_text(None, {"lead": ("claude", "stub", "low")})
    )
    home = tmp_path / "home"
    (home / ".agent-runner").mkdir(parents=True)
    (home / ".agent-runner/settings.yaml").write_text(
        "theme: light\nautonomous_backend: headless\nsetup:\n  completed_at: 2026-05-24T13:56:56Z\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(CLAUDE_STUB)
    stub.chmod(0o755)
    session = tmp_path / "session"
    params = {
        "artifact_dir": str(artifacts),
        "validator_status": "failed" if case == "h" else "passed",
        "effective_resume": "",
        "issue_file": str(tmp_path / "issue.json"),
        "archived_dir": str(tmp_path / "archive"),
        "branch_name": "claim",
        "change_name": "change",
    }
    result = subprocess.run(
        [
            str(RUNNER),
            "--headless",
            "-C",
            str(repo),
            "--profile",
            "factory",
            "run",
            "classification-order",
            *[f"{name}={value}" for name, value in params.items()],
            "--session-dir",
            str(session),
        ],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CLASSIFICATION_ARTIFACT": str(artifacts),
            "CLASSIFY_MODE": {
                "b": "fail",
                "c": "truncated",
                "d": "missing-item",
                "f": "truncated",
            }.get(case, "valid"),
            "REPAIR_MODE": "truncated" if case in "cf" else "valid",
        },
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(outcome_path.read_text())
    calls = artifacts / "calls.jsonl"
    audit = (session / "audit.log").read_text()
    if case in "gh":
        assert not calls.exists()
        assert not (artifacts / "annotated").exists()
        if case == "g":
            assert outcome == preseed
        else:
            assert outcome["outcome"] == "failed"
            assert outcome["validator"] == {"checks": "failed", "status": "failed"}
            assert outcome["reasons"] == ["validator did not pass within its repair cycles"]
        return
    invocations = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(invocations) == (2 if case in "cdf" else 1)
    assert not invocations[0]["repair"]
    if case in "cdf":
        assert invocations[1]["repair"]
        assert "Correct " in invocations[1]["prompt"]
    assert outcome["pr"]["url"] == "https://example.test/pull/9"
    assert outcome["ci"]["status"] == ("failed" if case in "ef" else "passed")
    if case in "bcf":
        assert not (artifacts / "annotated").exists()
        assert outcome["outcome"] == "failed"
        assert "classification failed after finalization" in outcome["reasons"][0]
        assert "review_attention_counts" not in outcome
        if case == "b":
            ends = [
                json.loads(line.split(" step_end ", 1)[1])
                for line in audit.splitlines()
                if "[verify-classification] step_end " in line
            ]
            assert len(ends) == 1
            assert ends[0]["outcome"] == "skipped"
            assert "classifying session failed" in outcome["reasons"][0]
        else:
            assert (artifacts / "review-attention.rejected.json").read_text() == '{"red":'
        assert ("CI did not pass within its fix cycle" in outcome["reasons"]) == (case == "f")
    else:
        assert (artifacts / "annotated").exists()
        assert outcome["outcome"] == ("failed" if case == "e" else "pull-request")
        assert outcome["review_attention_counts"]["orange"] == 1
        classification = json.loads((artifacts / "review-attention.json").read_text())
        final_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip()
        assert classification["later_commits"] == [final_head]
        assert final_head in classification["orange"][0]["detail"]
        assert "1 file changed" in classification["orange"][0]["detail"]
        assert "Tests:" in classification["orange"][0]["detail"]
        if case == "e":
            assert outcome["reasons"] == ["CI did not pass within its fix cycle"]
    assert "[verify-outcome] step_end" in audit
