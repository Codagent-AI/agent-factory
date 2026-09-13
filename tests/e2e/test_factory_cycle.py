"""Real CLI/Git/SQLite/process journey with stub GitHub and a controlled suite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agent_factory.config import SharedConfig
from agent_factory.store import ClaimStore


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir()
    _git(path, "init", "-b", "main")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        if name.endswith(".sh"):
            target.chmod(0o755)
    _git(path, "add", ".")
    _git(
        path,
        "-c",
        "user.name=Factory Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "test fixture",
    )
    remote = path.parent / (path.name + "-origin.git")
    subprocess.run(["git", "clone", "--quiet", "--bare", str(path), str(remote)], check=True)
    _git(path, "remote", "add", "origin", str(remote))
    # A real clone tracks its remote branch immediately; match that so doctor's
    # no-fetch harness resolution sees the same state a genuinely cloned checkout would.
    _git(path, "fetch", "--quiet", "origin")
    return _git(path, "rev-parse", "HEAD")


def _setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str], SharedConfig]:
    prefix = "evals/agent-runner/and-scene/"
    suite = f"""#!{sys.executable}
import json,pathlib,sys,time
args=sys.argv
out=pathlib.Path(args[args.index('--artifact-dir')+1]);out.mkdir(parents=True,exist_ok=True)
(out/'run-state.json').write_text(json.dumps({{'schema_version':1}}))
(out/'started').touch()
while not (out/'finish').exists(): time.sleep(.02)
result=({{'evaluation_status':'pending-human-review',
'product_verdict':'unavailable','automated_subtotal':60}})
if (out/'finish').read_text(): result=json.loads((out/'finish').read_text())
(out/'result.json').write_text(json.dumps(result))
"""
    _repo(
        tmp_path / "evals",
        {
            prefix + "run.sh": suite,
            prefix + "human-review.sh": "#!/bin/sh\nexit 0\n",
            **{
                prefix + "lib/" + name + ".mjs": "// fixture"
                for name in ("phases", "outcomes", "result")
            },
        },
    )
    _repo(
        tmp_path / "runner",
        {
            "scripts/sandbox-run.sh": "#!/bin/sh\n# --docker-run-arg\n",
            "workflows/core/implement-change-v1.0.yaml": "# fixture",
        },
    )
    _repo(tmp_path / "skills", {"README.md": "fixture"})
    text = Path("config/codagent.toml").read_text()
    (tmp_path / "shared.toml").write_text(text)
    shared = SharedConfig.from_toml(text)
    config = tmp_path / "local.toml"
    config.write_text(f'''shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path / "factory"}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[schedule]
timezone = "UTC"
poll_seconds = 1
start_hour = 0
stop_hour = 15
[limits]
minimum_free_gib = 0
inactivity_seconds = 20
execution_seconds = 30
total_seconds = 60
codex_reset_fallback_seconds = 18000
[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
''')
    (tmp_path / "key.pem").write_text("test key")
    (tmp_path / "key.pem").chmod(0o600)
    (tmp_path / "suite.env").write_text("CANDIDATE_TOKEN=test-only\n")
    (tmp_path / "suite.env").chmod(0o600)
    board = tmp_path / "board.json"
    fields: list[dict[str, object]] = []
    for configured in (shared.project.status, shared.project.owner, shared.project.verdict):
        fields.append(
            {
                "id": configured.id,
                "dataType": "SINGLE_SELECT",
                "options": [{"id": v, "name": k} for k, v in configured.options.items()],
            }
        )
    fields.append({"id": shared.project.refs.id, "dataType": "TEXT"})
    body = (
        "```eval\nrepetitions=1\n"
        + "".join(f'{role}="codex:test:high"\n' for role in ("lead", "implementor", "tester"))
        + "```"
    )
    item = {
        "id": "P1",
        "content": {
            "__typename": "Issue",
            "id": "I1",
            "number": 1,
            "body": body,
            "state": "OPEN",
            "author": {"login": "writer"},
            "repository": {"nameWithOwner": shared.routing.eval_source},
            "labels": {"nodes": [{"name": shared.routing.eval_label}]},
            "issueType": {"name": shared.routing.eval_type},
        },
        "fieldValues": {
            "nodes": [
                {
                    "field": {"id": shared.project.status.id},
                    "optionId": shared.project.status.option("ready"),
                },
                {
                    "field": {"id": shared.project.owner.id},
                    "optionId": shared.project.owner.option("factory"),
                },
            ]
        },
    }
    board.write_text(json.dumps({"items": [item], "fields": fields, "comments": []}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = f"""#!{sys.executable}
import json,sys,pathlib
p=pathlib.Path({str(board)!r});s=json.loads(p.read_text());args=sys.argv
body=json.load(sys.stdin) if '--input' in args else {{}}
endpoint=args[2];q=body.get('query','');v=body.get('variables',{{}})
if '/collaborators/' in endpoint: result={{'permission':'write'}}
elif endpoint=='graphql':
 if 'query Fields' in q:
  result={{'data':{{'node':{{'fields':{{'nodes':s['fields'],'pageInfo':{{'hasNextPage':False}}}}}}}}}}
 elif 'query Items' in q:
  result={{'data':{{'node':{{'items':{{'nodes':s['items'],'pageInfo':{{'hasNextPage':False}}}}}}}}}}
 else:
  item=next(x for x in s['items'] if x['id']==v['item']);vals=item['fieldValues']['nodes']
  vals[:]=[x for x in vals if x['field']['id']!=v['field']]
  if 'option' in v: vals.append({{'field':{{'id':v['field']}},'optionId':v['option']}})
  result={{'data':{{'updateProjectV2ItemFieldValue':{{'projectV2Item':{{'id':v['item']}}}}}}}}
elif '/comments' in endpoint:
 if 'POST' in args:
  result={{'id':len(s['comments'])+1,'body':body['body'],'user':{{'login':{shared.bot_login!r}}}}};s['comments'].append(result)
 else: result=s['comments']
elif '/labels' in endpoint: result=[]
else: raise Exception('Unexpected gh request '+repr(args))
p.write_text(json.dumps(s));print(json.dumps(result))
"""
    for name, content in {
        "gh": gh,
        "openssl": "#!/bin/sh\ncat >/dev/null\nprintf signature",
        "docker": (
            "#!/bin/sh\n"
            'if [ "$1" = "info" ]; then echo 17179869184\n'
            'elif [ "$1" = "stats" ]; then printf ""\n'
            "fi\n"
            "exit 0\n"
        ),
        "codex": "#!/bin/sh\nexit 0",
        "cursor": "#!/bin/sh\nexit 0",
    }.items():
        script = bin_dir / name
        script.write_text(content)
        script.chmod(0o755)
    environment = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return config, board, environment, shared


def _cli(
    config: Path,
    environment: dict[str, str],
    command: str,
    *,
    before_cli: str = "",
    expected_error: str | None = None,
) -> None:
    # Stub the HTTP authentication boundary just as gh stubs the Project API.
    # The real Bearer exchange is covered with a local HTTP server separately.
    # Fix only the admission hour; quota/recovery clocks retain their real timestamps.
    entrypoint = """
import io, runpy, urllib.request
from agent_factory import runtime
from agent_factory.config import ScheduleConfig
allows_admission = ScheduleConfig.allows_admission
ScheduleConfig.allows_admission = lambda self, now: allows_admission(self, now.replace(hour=12))
def token_response(request, *, timeout):
    assert request.full_url.startswith('https://api.github.com/app/installations/')
    assert request.full_url.endswith('/access_tokens')
    assert request.get_method() == 'POST'
    assert request.get_header('Authorization').startswith('Bearer ')
    return io.BytesIO(b'{"token":"test","expires_at":"2099-01-01T00:00:00Z"}')
urllib.request.urlopen = token_response
"""
    entrypoint += before_cli + "\nrunpy.run_module('agent_factory.cli', run_name='__main__')\n"
    done = subprocess.run(
        [sys.executable, "-c", entrypoint, "--config", str(config), command],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if expected_error is None:
        assert done.returncode == 0, done.stderr
    else:
        assert done.returncode != 0
        assert "Traceback (most recent call last)" in done.stderr
        assert expected_error in done.stderr


def _field_value(board: Path, field_id: str) -> str | None:
    for field in json.loads(board.read_text())["items"][0]["fieldValues"]["nodes"]:
        if field["field"]["id"] == field_id:
            return str(field["optionId"])
    return None


def _status(board: Path, shared: SharedConfig, value: str) -> None:
    data: dict[str, Any] = json.loads(board.read_text())
    for field in data["items"][0]["fieldValues"]["nodes"]:
        if field["field"]["id"] == shared.project.status.id:
            field["optionId"] = shared.project.status.option(value)
    board.write_text(json.dumps(data))


def test_e2e_001_003_cli_admits_reports_and_cleans_reviewed_worktrees(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    _cli(config, env, "pause")
    _status(board, shared, "running")
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.status.id) == shared.project.status.option("ready")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    assert not store.nonterminal_runs()
    _cli(config, env, "resume")
    _cli(config, env, "tick")
    runs = store.nonterminal_runs()
    assert len(runs) == 1, "normal tick must claim and launch eligible queued work"
    run = runs[0]
    artifact = Path(run.evidence_path)
    try:
        deadline = time.monotonic() + 5
        while not (artifact / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (artifact / "started").exists()
        assert artifact.is_relative_to(tmp_path / "factory/artifacts")
        _status(board, shared, "done")
        _cli(config, env, "tick")
        assert (tmp_path / "factory/worktrees" / run.claim_id / "runner").exists()
        _cli(config, env, "pause")
        (artifact / "finish").touch()
        deadline = time.monotonic() + 5
        while store.nonterminal_runs() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not store.nonterminal_runs()
        _cli(config, env, "tick")
        claim = store.get_claim(run.claim_id)
        assert claim and claim.lifecycle == "settled"
        assert claim.outcome["verdict"] == "pending-human-review"
        assert "human-review.sh" in board.read_text()
        _cli(config, env, "tick")
        _status(board, shared, "done")
        _cli(config, env, "tick")
        assert not (tmp_path / "factory/worktrees" / run.claim_id / "runner").exists()
        assert (artifact / "result.json").exists()
        assert len(store.runs_for_claim(run.claim_id)) == 1
    finally:
        (artifact / "finish").touch()
        store.close()


def _finish(store: ClaimStore, evidence: Path) -> None:
    (evidence / "finish").touch()
    deadline = time.monotonic() + 5
    while store.nonterminal_runs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not store.nonterminal_runs()


def test_e2e_001_pins_survive_source_updates_and_cleanup_retries(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    data = json.loads(board.read_text())
    data["items"][0]["content"]["body"] = data["items"][0]["content"]["body"].replace(
        "repetitions=1", "repetitions=2"
    )
    board.write_text(json.dumps(data))
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    first = store.nonterminal_runs()[0]
    claim = store.get_claim(first.claim_id)
    assert claim is not None
    original = claim.frozen_spec["revisions"]
    root = tmp_path / "factory/worktrees" / claim.id
    for source in ("runner", "skills"):
        _git(
            tmp_path / source,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "advance moving main",
        )
    _cli(config, env, "pause")
    _finish(store, Path(first.evidence_path))
    _cli(config, env, "tick")
    assert len(store.runs_for_claim(claim.id)) == 1
    _cli(config, env, "resume")
    _cli(config, env, "tick")
    second = store.nonterminal_runs()[0]
    assert second.claim_id == first.claim_id and second.unit_key == "rep-2"
    assert second.evidence_path != first.evidence_path
    assert store.get_claim(claim.id).frozen_spec["revisions"] == original  # pyright: ignore[reportOptionalMemberAccess]
    for source in ("runner", "skills"):
        assert _git(root / source, "rev-parse", "HEAD") != _git(
            tmp_path / source, "rev-parse", "HEAD"
        )
    _finish(store, Path(second.evidence_path))
    _cli(config, env, "pause")
    _cli(config, env, "tick")
    _git(tmp_path / "runner", "worktree", "lock", str(root / "runner"))
    try:
        _status(board, shared, "done")
        _cli(config, env, "tick")
        saved = store.get_claim(claim.id)
        assert saved is not None and saved.cleanup["last_error"]
        assert (root / "runner").exists()
        assert not (root / "skills").exists()
    finally:
        _git(tmp_path / "runner", "worktree", "unlock", str(root / "runner"))
    _cli(config, env, "tick")
    assert not (root / "runner").exists()
    assert (Path(first.evidence_path) / "result.json").exists()
    assert (Path(second.evidence_path) / "result.json").exists()
    store.close()


def test_cli_quota_result_holds_admission_without_consuming_recovery(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    run = store.nonterminal_runs()[0]
    artifact = Path(run.evidence_path)
    (artifact / "finish").write_text(
        json.dumps(
            {"evaluation_status": "failed", "failure": {"reason": "Codex usage limit reached"}}
        )
    )
    _finish(store, artifact)
    _cli(config, env, "tick")
    assert store.get_run(run.id).status == "deferred"  # pyright: ignore[reportOptionalMemberAccess]
    assert store.get_setting("admission", "quota:codex")
    assert store.recovery_attempts(run.claim_id, run.unit_key) == 0
    _cli(config, env, "tick")
    assert len(store.runs_for_claim(run.claim_id)) == 1
    assert _field_value(board, shared.project.verdict.id) == shared.project.verdict.option(
        "quota-deferred"
    )
    # Advance the isolated hold fixture instead of waiting five hours in a test.
    store.set_setting("admission", "quota:codex", {"until": "2020-01-01T00:00:00+00:00"})
    store.set_hold(run.claim_id, "quota", {"until": "2020-01-01T00:00:00+00:00"})
    (artifact / "finish").unlink()
    _cli(config, env, "tick")
    continuation = store.nonterminal_runs()[0]
    assert continuation.claim_id == run.claim_id and continuation.reason == "quota"
    assert continuation.evidence_path == run.evidence_path
    assert "--resume" in continuation.plan["argv"]  # pyright: ignore[reportOperatorIssue]
    _finish(store, artifact)
    store.close()


def test_cli_clearing_delivered_deferral_verdict_creates_fresh_claim(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    first = store.nonterminal_runs()[0]
    artifact = Path(first.evidence_path)
    _cli(config, env, "pause")
    (artifact / "finish").write_text(
        json.dumps(
            {
                "evaluation_status": "failed",
                "failure": {"reason": "controlled infrastructure failure"},
            }
        )
    )
    _finish(store, artifact)
    _cli(config, env, "tick")
    data = json.loads(board.read_text())
    values = data["items"][0]["fieldValues"]["nodes"]
    assert any(value["field"]["id"] == shared.project.verdict.id for value in values)
    data["items"][0]["fieldValues"]["nodes"] = [
        value for value in values if value["field"]["id"] != shared.project.verdict.id
    ]
    board.write_text(json.dumps(data))
    _cli(config, env, "resume")
    _cli(config, env, "tick")
    fresh = store.nonterminal_runs()[0]
    assert fresh.claim_id != first.claim_id and fresh.reason == "initial"
    assert store.get_claim(first.claim_id).lifecycle == "superseded"  # pyright: ignore[reportOptionalMemberAccess]
    data = json.loads(board.read_text())
    data["items"][0]["fieldValues"]["nodes"].append(
        {
            "field": {"id": shared.project.verdict.id},
            "optionId": shared.project.verdict.option("infra-error"),
        }
    )
    board.write_text(json.dumps(data))
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.verdict.id) is None
    _finish(store, Path(fresh.evidence_path))
    store.close()


def test_cli_retries_proven_precheckpoint_launch_failure_under_same_unit(tmp_path: Path) -> None:
    config, _, env, _ = _setup(tmp_path)
    interpreter = tmp_path / "suite-python"
    wrapper = tmp_path / "evals/evals/agent-runner/and-scene/run.sh"
    wrapper.write_text(wrapper.read_text().replace(f"#!{sys.executable}", f"#!{interpreter}"))
    _git(tmp_path / "evals", "add", ".")
    _git(
        tmp_path / "evals",
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "controlled missing interpreter",
    )
    _git(tmp_path / "evals", "push", "origin", "main")
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    claim = store.all_claims()[0]
    first = store.runs_for_claim(claim.id)[0]
    deadline = time.monotonic() + 5
    while store.nonterminal_runs() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert store.get_run(first.id).result["reason"] == "suite launch failed"  # pyright: ignore[reportOptionalMemberAccess]
    assert not (Path(first.evidence_path) / "run-state.json").exists()
    interpreter.symlink_to(sys.executable)
    _cli(config, env, "tick")
    retry = store.nonterminal_runs()[0]
    assert retry.reason == "recovery" and retry.evidence_path == first.evidence_path
    assert "--resume" not in retry.plan["argv"]  # pyright: ignore[reportOperatorIssue]
    _finish(store, Path(retry.evidence_path))
    assert store.recovery_attempts(claim.id, retry.unit_key) == 1
    store.close()


def test_bad_request_ref_reports_feedback_and_admits_next_card(tmp_path: Path) -> None:
    import copy

    config, board, env, _ = _setup(tmp_path)
    data = json.loads(board.read_text())
    next_card = copy.deepcopy(data["items"][0])
    next_card["id"] = "P2"
    next_card["content"]["id"] = "I2"
    next_card["content"]["number"] = 2
    data["items"].append(next_card)
    data["items"][0]["content"]["body"] = '```eval\nagent_runner_ref="does-not-exist"\n```'
    board.write_text(json.dumps(data))
    _cli(config, env, "tick")
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    try:
        assert not store.claims_for_item("P1")
        assert "revision" in " ".join(
            comment["body"] for comment in json.loads(board.read_text())["comments"]
        )
        run = store.nonterminal_runs()[0]
        assert store.get_claim(run.claim_id).project_item_id == "P2"  # pyright: ignore[reportOptionalMemberAccess]
        _finish(store, Path(run.evidence_path))
    finally:
        for run in store.nonterminal_runs():
            _finish(store, Path(run.evidence_path))
        store.close()


def test_invalid_quota_hold_keeps_reconciliation_and_feedback_running(tmp_path: Path) -> None:
    config, board, env, shared = _setup(tmp_path)
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    data = json.loads(board.read_text())
    data["items"][0]["content"]["body"] = "```eval\nrepetitions=0\n```"
    board.write_text(json.dumps(data))
    for value in ({}, {"until": 123}, {"until": "bad"}, {"until": "2026-09-09T13:00:00"}):
        store.set_setting("admission", "quota:codex", value)
        _status(board, shared, "running")
        _cli(config, env, "tick")
        assert not store.nonterminal_runs()
        assert _field_value(board, shared.project.status.id) == shared.project.status.option(
            "ready"
        )
        assert store.get_setting("runtime", "quota-error")
    comments = json.loads(board.read_text())["comments"]
    assert any("needs-input" in comment["body"] for comment in comments)
    store.close()


def test_missing_frozen_revision_reports_error_without_aborting_tick(tmp_path: Path) -> None:
    from agent_factory.store import ClaimDraft

    config, board, env, _ = _setup(tmp_path)
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    shared = SharedConfig.from_file(tmp_path / "shared.toml")
    claim = store.create_claim(
        ClaimDraft(
            shared.routing.eval_source,
            1,
            "I1",
            "P1",
            "eval",
            "x",
            {"revisions": {"runner": "a" * 40}},
        )
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    _cli(config, env, "pause")
    _cli(config, env, "tick")
    _cli(config, env, "tick")
    comments = json.loads(board.read_text())["comments"]
    errors = [comment for comment in comments if "frozen revisions" in comment["body"]]
    assert len(errors) == 1
    assert "skills" in errors[0]["body"] and "evals" in errors[0]["body"]
    assert store.get_setting("field-delivery", f"{claim.id}:refs") is None
    store.close()


def test_active_claim_clears_and_can_redeliver_the_same_verdict(tmp_path: Path) -> None:
    from agent_factory.store import ClaimDraft

    config, board, env, shared = _setup(tmp_path)
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    claim = store.create_claim(
        ClaimDraft(
            shared.routing.eval_source,
            1,
            "I1",
            "P1",
            "eval",
            "x",
            {"revisions": {"runner": "a" * 40, "skills": "b" * 40, "evals": "c" * 40}},
        )
    )
    _cli(config, env, "pause")
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "infra-error"})
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.verdict.id) == shared.project.verdict.option(
        "infra-error"
    )

    store.reserve_run(
        claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path / "artifacts")
    )
    store.set_claim_lifecycle(claim.id, "active", {})
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.verdict.id) is None

    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "infra-error"})
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.verdict.id) == shared.project.verdict.option(
        "infra-error"
    )
    store.close()


def test_ready_card_with_cleared_verdict_starts_a_fresh_settled_claim(tmp_path: Path) -> None:
    from agent_factory.store import ClaimDraft

    config, board, env, shared = _setup(tmp_path)
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    original = store.create_claim(
        ClaimDraft(
            shared.routing.eval_source,
            1,
            "I1",
            "P1",
            "eval",
            "x",
            {"revisions": {"runner": "a" * 40, "skills": "b" * 40, "evals": "c" * 40}},
        )
    )
    store.set_claim_lifecycle(original.id, "settled", {"verdict": "infra-error"})
    _cli(config, env, "tick")
    assert _field_value(board, shared.project.status.id) == shared.project.status.option("review")

    data: dict[str, Any] = json.loads(board.read_text())
    fields = data["items"][0]["fieldValues"]["nodes"]
    for field in fields:
        if field["field"]["id"] == shared.project.status.id:
            field["optionId"] = shared.project.status.option("ready")
    data["items"][0]["fieldValues"]["nodes"] = [
        field for field in fields if field["field"]["id"] != shared.project.verdict.id
    ]
    board.write_text(json.dumps(data))

    _cli(config, env, "tick")
    runs = store.nonterminal_runs()
    try:
        assert len(runs) == 1
        assert runs[0].claim_id != original.id
    finally:
        for run in runs:
            _finish(store, Path(run.evidence_path))
        store.close()


@pytest.mark.parametrize("failure", ["WorktreeError", "RuntimeError"])
def test_planning_failure_finalizes_reserved_attempt(tmp_path: Path, failure: str) -> None:
    config, board, env, shared = _setup(tmp_path)
    before_cli = f"""
from agent_factory.suites.and_scene import WorktreeError
def fail_plan(*args, **kwargs):
    raise {failure}('planning failed before launch')
from agent_factory.work_kinds.eval import handler as eval_handler
eval_handler.plan_attempt = fail_plan
"""
    _cli(
        config,
        env,
        "tick",
        before_cli=before_cli,
        expected_error="RuntimeError: planning failed before launch"
        if failure == "RuntimeError"
        else None,
    )
    store = ClaimStore(tmp_path / "factory/state.sqlite3")
    try:
        claim = store.all_claims()[0]
        attempts = store.runs_for_claim(claim.id)
        assert len(attempts) == 1
        assert attempts[0].status == "failed"
        assert attempts[0].result["reason"] == "planning failed before launch"
        assert attempts[0].result["error_type"] == failure
        assert not store.nonterminal_runs()
        if failure == "WorktreeError":
            assert store.get_hold(claim.id, "readiness") == {
                "reason": "planning failed before launch"
            }
            assert _field_value(board, shared.project.status.id) == shared.project.status.option(
                "ready"
            )
            assert "planning failed before launch" in board.read_text()
    finally:
        store.close()
