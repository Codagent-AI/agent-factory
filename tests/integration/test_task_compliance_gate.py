"""The feature gate's git, validator output, and evidence boundaries."""

# ruff: noqa: E501

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_feature_workflow_scripts import PACKAGE, git, repository

GATE = PACKAGE / "task-compliance-gate.py"

STUB = """#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
log = pathlib.Path(os.environ['CALL_LOG'])
with log.open('a') as stream:
    stream.write(json.dumps({'args': args, 'cwd': os.getcwd()}) + '\\n')
if args == ['list']:
    if os.environ.get('LIST_FAIL'):
        sys.exit(1)
    entry = os.environ.get('ENTRY', '.')
    gate = 'other-review' if os.environ.get('NODECLARE') else 'task-compliance'
    print('Review Gates:\\n - ' + gate + ' (Tools: stub)\\n\\nEntry Points:\\n - ' + entry + '\\n   Reviews: ' + gate)
    sys.exit(0)
mode = os.environ.get('MODES', 'pass').split(',')
number = sum('review' in call['args'] for call in map(json.loads, log.read_text().splitlines()))
state = mode[min(number - 1, len(mode) - 1)]
entry = os.environ.get('ENTRY', '.')
if state != 'none' and state != 'deleted' and '/' not in entry:
    root = pathlib.Path('validator_logs')
    if state == 'previous':
        root /= 'previous'
    root.mkdir(parents=True, exist_ok=True)
    status = {'previous': 'pass', 'preserved': 'preserved_one_shot'}.get(state, state)
    (root / f'review_{entry}_task-compliance_stub@1.1.json').write_text(json.dumps({
        'status': status, 'attempt_id': 'attempt-1',
        'violations': [{'file': 'code.py', 'line': 1, 'issue': 'missing feature', 'fix': 'add it', 'priority': 'high'}] if status == 'fail' else [],
    }))
if state in ('pass', 'fail', 'error', 'previous', 'deleted'):
    label = {'pass': 'PASS', 'fail': 'FAIL', 'error': 'ERROR', 'previous': 'PASS', 'deleted': 'PASS'}[state]
    print(f'[{label}] review:{entry}:task-compliance (stub@1) (1s) - reviewed')
elif state == 'preserved':
    print(f'[PASS] review:{entry}:task-compliance (stub@1) (1s) - preserved one-shot state')
else:
    print('Status: Trusted')
"""


def setup(tmp_path: Path) -> tuple[Path, Path, Path, str, dict[str, str]]:
    repo, _ = repository(tmp_path)
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "feature")
    (repo / "code.py").write_text("feature\n")
    tasks = repo / "openspec" / "changes" / "feature" / "tasks.md"
    tasks.parent.mkdir(parents=True)
    tasks.write_text("- [ ] Add feature\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    stub = tmp_path / "agent-validator"
    stub.write_text(STUB)
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CALL_LOG": str(tmp_path / "calls.jsonl"),
    }
    return repo, tasks, artifacts, base, env


def gate(
    repo: Path,
    tasks: Path,
    artifacts: Path,
    base: str,
    env: dict[str, str],
    phase: str = "verified",
) -> subprocess.CompletedProcess[str]:
    payload = {
        "phase": phase,
        "artifact_dir": str(artifacts),
        "tasks_file": str(tasks),
        "target_head": base,
    }
    return subprocess.run(
        [str(GATE), "--json", json.dumps(payload)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )


def record(artifacts: Path) -> dict[str, Any]:
    return json.loads((artifacts / "task-compliance.json").read_text())


def review_calls(env: dict[str, str]) -> list[dict[str, Any]]:
    return [
        call
        for call in map(json.loads, Path(env["CALL_LOG"]).read_text().splitlines())
        if "review" in call["args"]
    ]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("fail", "failed"),
        ("pass", "passed"),
        ("previous", "passed"),
        ("deleted", "passed"),
        ("none", "not-run"),
        ("preserved", "not-run"),
    ],
)
def test_gate_maps_dispatch_evidence(tmp_path: Path, mode: str, expected: str) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["MODES"] = mode
    result = gate(repo, tasks, artifacts, base, env)
    assert result.returncode == (1 if expected == "failed" else 0), result.stderr
    saved = record(artifacts)
    assert saved["result"] == expected
    assert saved["base"] == base
    assert saved["reviewed_head"] == git(repo, "rev-parse", "HEAD")
    assert len(review_calls(env)) == 1
    assert review_calls(env)[0]["cwd"] != str(repo)
    assert not list(tmp_path.glob("task-compliance-*/review"))
    if mode == "fail":
        assert saved["violations"][0]["issue"] == "missing feature"
    if mode == "deleted":
        assert (
            "[PASS]"
            in (artifacts / "task-compliance" / "verified-1" / "console.txt").read_text()
        )


def test_gate_reuses_only_openspec_changes_and_rechecks_code(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    assert gate(repo, tasks, artifacts, base, env).returncode == 0
    tasks.write_text("- [x] Add feature\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "tick task")
    assert gate(repo, tasks, artifacts, base, env, "verified").returncode == 0
    assert len(review_calls(env)) == 1
    (repo / "code.py").write_text("repaired\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "repair")
    assert gate(repo, tasks, artifacts, base, env, "verified").returncode == 0
    assert len(review_calls(env)) == 2
    tasks.write_text("- [x] Add another feature\n")
    assert gate(repo, tasks, artifacts, base, env, "verified").returncode == 0
    assert len(review_calls(env)) == 3


def test_gate_uses_resume_target_and_names_uncovered_paths(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["ENTRY"] = "packages/app"
    (repo / "packages" / "app").mkdir(parents=True)
    (repo / "packages" / "app" / "a.py").write_text("app\n")
    (repo / "packages" / "lib").mkdir(parents=True)
    (repo / "packages" / "lib" / "b.py").write_text("lib\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "packages")
    result = gate(repo, tasks, artifacts, base, env)
    assert result.returncode == 0, result.stderr
    assert record(artifacts)["result"] == "not-run"
    assert "packages/lib/b.py" in record(artifacts)["uncovered_paths"], record(artifacts)
    git(repo, "checkout", "main")
    (repo / "target-only").write_text("target\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "target update")
    new_base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "feature")
    git(repo, "merge", "main", "--no-edit")
    (artifacts / "base-merge.json").write_text(json.dumps({"base_head": new_base}))
    env["ENTRY"] = "."
    assert gate(repo, tasks, artifacts, base, env, "verified").returncode == 0
    assert record(artifacts)["target_ref"] == new_base
    assert record(artifacts)["base"] == new_base
    assert review_calls(env)[-1]["args"][-1] == new_base
    assert "target-only" not in git(repo, "diff", "--name-only", f"{new_base}...HEAD").splitlines()


def test_gate_retries_error_once(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["MODES"] = "error,pass"
    assert gate(repo, tasks, artifacts, base, env).returncode == 0
    assert record(artifacts)["result"] == "passed"
    assert len(review_calls(env)) == 2
    env["MODES"] = "error"
    (repo / "code.py").write_text("changed\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "change")
    assert gate(repo, tasks, artifacts, base, env).returncode == 0
    assert record(artifacts)["result"] == "not-run"
    assert "review error" in record(artifacts)["reason"]


def test_gate_unreadable_declaration(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["LIST_FAIL"] = "1"
    assert gate(repo, tasks, artifacts, base, env).returncode == 0
    assert record(artifacts)["result"] == "not-run"
    assert review_calls(env) == []


@pytest.mark.parametrize(
    "mode,retention,expected",
    [("review-fail", 3, "failed"), ("pass", 3, "passed"), ("pass", 0, "passed")],
)
def test_real_validator_escapes_trusted_claim_clone(
    tmp_path: Path, mode: str, retention: int, expected: str
) -> None:
    """The real validator sees the isolated clone while the claim's tree is trusted."""
    import shutil

    validator = shutil.which("agent-validator")
    if not validator:
        pytest.skip("agent-validator is not installed")
    repo, tasks, artifacts, _base, env = setup(tmp_path)
    config = repo / ".validator" / "config.yml"
    config.parent.mkdir()
    config.write_text(f"""base_branch: main
log_dir: validator_logs
max_previous_logs: {retention}
allow_parallel: false
cli:
  default_preference: [codex]
  adapters:
    codex:
      allow_tool_use: false
entry_points:
  - path: "."
    reviews:
      - task-compliance:
          builtin: task-compliance
          enabled: false
          parallel: false
""")
    (repo / ".gitignore").write_text("validator_logs/\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "configure review")
    base = git(repo, "rev-parse", "main")
    stub = tmp_path / "codex"
    stub.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
input = sys.stdin.read()
pathlib.Path(os.environ['REVIEW_PROMPT']).write_text(input)
if os.environ['REVIEW_MODE'] == 'review-fail':
    answer = {'status': 'fail', 'violations': [{'file': 'code.py', 'line': 1, 'issue': 'missing feature', 'fix': 'add it', 'priority': 'high', 'status': 'new'}]}
else:
    answer = {'status': 'pass', 'message': 'Stub review passed'}
print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(answer)}}))
""")
    stub.chmod(0o755)
    env.update(REVIEW_MODE=mode, REVIEW_PROMPT=str(tmp_path / "prompt.txt"))
    env.pop("MODES", None)
    env["PATH"] = f"{tmp_path}:{os.environ['PATH']}"
    (tmp_path / "agent-validator").unlink()  # Use the real validator; only codex is stubbed.
    # First prove the direct invocation is stopped by the trusted ledger.
    skip = subprocess.run([validator, "skip"], cwd=repo, env=env, text=True, capture_output=True)
    if skip.returncode:
        pytest.skip(f"installed validator cannot seed trust ledger: {skip.stderr or skip.stdout}")
    direct = subprocess.run(
        [validator, "run", "--report", "--enable-review", "task-compliance"],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    if "Trusted" not in direct.stdout + direct.stderr:
        pytest.skip(
            f"installed validator did not report a trusted claim head: {direct.returncode} {direct.stdout!r} {direct.stderr!r}"
        )
    before = (repo / "validator_logs").exists()
    ledger = repo / ".git" / "agent-validator" / "trusted-snapshots.jsonl"
    ledger_before = ledger.read_bytes() if ledger.exists() else None
    payload = {
        "phase": "verified",
        "artifact_dir": str(artifacts),
        "tasks_file": str(tasks),
        "target_head": base,
    }
    result = subprocess.run(
        [str(GATE), "--json", json.dumps(payload)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == (1 if expected == "failed" else 0), result.stderr
    saved = record(artifacts)
    assert saved["result"] == expected, saved
    assert saved["base"] == base
    assert saved["reviewed_head"] == git(repo, "rev-parse", "HEAD")
    prompt = (tmp_path / "prompt.txt").read_text()
    assert "Add feature" in prompt
    assert "code.py" in prompt
    assert (repo / "validator_logs").exists() == before
    assert (ledger.read_bytes() if ledger.exists() else None) == ledger_before
    assert not list(tmp_path.glob("task-compliance-*/review"))
    evidence = artifacts / "task-compliance" / "verified-1"
    if mode == "review-fail":
        assert list(evidence.rglob("review_*task-compliance*.json"))
    elif retention == 3:
        assert list(
            (evidence / "validator_logs" / "previous").rglob("review_*task-compliance*.json")
        )
    else:
        assert "review:.:task-compliance" in (evidence / "console.txt").read_text()


def test_gate_rejects_removed_implemented_phase(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    result = gate(repo, tasks, artifacts, base, env, "implemented")
    assert result.returncode != 0
    assert "invalid phase" in result.stderr


def test_reused_failed_verdict_stays_failed(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["MODES"] = "fail"
    assert gate(repo, tasks, artifacts, base, env).returncode == 1
    assert gate(repo, tasks, artifacts, base, env).returncode == 1
    assert record(artifacts)["result"] == "failed"
    assert len(review_calls(env)) == 1


def test_gate_not_declared(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["NODECLARE"] = "1"
    assert gate(repo, tasks, artifacts, base, env).returncode == 0
    assert record(artifacts)["result"] == "not-declared"
    assert review_calls(env) == []


def test_gate_records_a_binding_failure_as_not_run(tmp_path: Path) -> None:
    repo, tasks, artifacts, base, env = setup(tmp_path)
    env["MODES"] = "fail"
    assert gate(repo, tasks, artifacts, base, env).returncode == 1
    result = gate(repo, tasks, artifacts, "invalid-target-ref", env)
    # Exit 0 settles the gate loop: repair must not act on a stale or missing record.
    assert result.returncode == 0
    saved = record(artifacts)
    assert saved["result"] == "not-run"
    assert saved["violations"] == []
    assert saved["reason"].startswith("binding failed:")
    assert "invalid-target-ref" in saved["reason"]
    assert "fatal:" in saved["reason"]
    assert len(review_calls(env)) == 1


def test_review_timeout_stops_the_whole_process_group(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("task_compliance_gate", str(GATE))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        module.run_review([sys.executable, "-c", script], tmp_path, 2)
    pid = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, signal.SIGKILL)
        pytest.fail("the reviewer process outlived the review timeout")
