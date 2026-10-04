# pyright: reportPrivateUsage=false
"""INT-005: the notifier wrapper runs only the fake Claude CLI with a narrow environment."""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, NotifyConfig, SharedConfig
from agent_factory.notify import deliver as deliver_module
from agent_factory.notify import marker
from agent_factory.notify import store as records
from agent_factory.notify import supervise as supervise_module
from agent_factory.notify.deliver import start
from agent_factory.notify.registry import LiveSession
from agent_factory.notify.supervise import parse_result, supervise
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import ProcessProbeError, process_identity_status
from tests.integration.test_fix_config import _LOCAL_BASE


def test_fake_claude_command_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "Path('../argv.json').write_text(json.dumps(sys.argv[1:]))\n"
        "Path('../env.json').write_text(json.dumps(dict(os.environ)))\n"
        "Path('../cwd.txt').write_text(os.getcwd())\n"
        "print(json.dumps({'is_error':False,'structured_output':"
        "{'outcome':'sent','detail':'ok'},'total_cost_usd':0.007}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("GH_TOKEN", "dummy")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy")
    evidence = tmp_path / "evidence"
    identity = start(evidence, "claude:fake-model:low", "target", "notice\n")
    for _ in range(100):
        if (evidence / "exit.json").exists():
            break
        time.sleep(0.02)
    assert (evidence / "exit.json").exists()
    argv = json.loads((evidence / "argv.json").read_text())
    for pair in (
        ("--model", "fake-model"),
        ("--effort", "low"),
        ("--tools", "ListAgents,SendMessage"),
        ("--allowedTools", "ListAgents,SendMessage"),
    ):
        assert argv[argv.index(pair[0]) + 1] == pair[1]
    assert "--permission-mode" not in argv
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    env = json.loads((evidence / "env.json").read_text())
    assert "GH_TOKEN" not in env and "GITHUB_TOKEN" not in env
    assert Path((evidence / "cwd.txt").read_text()).name == "agent-factory-notify"
    assert parse_result(evidence / "stdout.json") == ("sent", "ok", 0.007)
    assert process_identity_status(identity) in {"alive", "missing"}


def test_supervision_isolates_bad_rows_and_invalid_stderr(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = LocalConfig.from_toml(_LOCAL_BASE)
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        now = datetime.now(UTC)
        for number in range(3):
            records.insert(
                store,
                claim.id,
                f"run-{number}",
                "o/r",
                number,
                "fix",
                ("failed", "settled", "cancelled")[number],
                now,
            )
        for number, row in enumerate(records.rows(store)):
            evidence = tmp_path / str(number)
            evidence.mkdir()
            if number == 0:
                (evidence / "stdout.json").write_text("bad json")
                (evidence / "stderr.log").write_bytes(b"\xff\xfebad stderr")
            else:
                (evidence / "stdout.json").write_text(
                    json.dumps(
                        {
                            "is_error": False,
                            "structured_output": {"outcome": "sent", "detail": "ok"},
                        }
                    )
                )
            records.update(
                store,
                row["id"],
                "settling",
                state="launched",
                evidence_path=str(evidence),
                launched_at=now.isoformat(),
                deadline_at=None if number == 1 else now.isoformat(),
                process_json=json.dumps({"pid": 1, "start": "wrong"}),
            )
        supervise(store, local)
        outcomes = [row["outcome"] for row in records.rows(store)]
        assert outcomes == ["failed", "failed", "sent"]
        assert all(row["state"] == "ended" for row in records.rows(store))
    finally:
        store.close()


def test_unprobeable_pid_is_retried_until_its_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unprobeable(pid: int) -> str:
        raise ProcessProbeError("ps unavailable")

    monkeypatch.setattr(supervise_module, "process_start_identity", unprobeable)
    store = ClaimStore(tmp_path / "state.sqlite3")
    local = LocalConfig.from_toml(_LOCAL_BASE)
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        now = datetime.now(UTC)
        records.insert(store, claim.id, "run", "o/r", 1, "fix", "failed", now)
        row = records.rows(store)[0]
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        (evidence / "pid").write_text(str(os.getpid()))
        records.update(
            store,
            row["id"],
            "settling",
            state="launched",
            evidence_path=str(evidence),
            launched_at=(now - timedelta(minutes=3)).isoformat(),
            deadline_at=(now + timedelta(minutes=2)).isoformat(),
        )
        supervise(store, local)
        assert records.rows(store)[0]["state"] == "launched"
        records.update(
            store, row["id"], "launched", deadline_at=(now - timedelta(seconds=1)).isoformat()
        )
        supervise(store, local)
        ended = records.rows(store)[0]
        assert (ended["state"], ended["outcome"], ended["detail"]) == (
            "ended",
            "failed",
            "launch lost",
        )
    finally:
        store.close()


def test_launched_session_counts_when_recording_its_identity_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(shared, notify=NotifyConfig(enabled=True, agent="claude:fake:low"))
    local = LocalConfig.from_toml(_LOCAL_BASE)
    session = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"
    store = ClaimStore(tmp_path / "state.sqlite3")
    original_update = records.update

    def failing_update(*args: object, **fields: object) -> bool:
        if "process_json" in fields:
            raise RuntimeError("database is locked")
        return original_update(*args, **fields)  # type: ignore[arg-type]

    def resolve(session_id: str) -> LiveSession:
        return LiveSession(session_id, "target", 1)

    def diagnostics(*args: object) -> list[object]:
        return []

    def fake_start(*args: object) -> dict[str, object]:
        return {"pid": 1, "start": "x"}

    monkeypatch.setattr(deliver_module.registry, "resolve", resolve)
    monkeypatch.setattr(deliver_module.readiness, "diagnostics", diagnostics)
    monkeypatch.setattr(deliver_module, "start", fake_start)
    monkeypatch.setattr(records, "update", failing_update)
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        now = datetime.now(UTC)
        records.insert(store, claim.id, run.id, "o/r", 1, "fix", "failed", now)
        with pytest.raises(RuntimeError, match="database is locked"):
            deliver_module.deliver(
                store, shared, local, {("o/r", 1): marker.render(session, "target")}, now
            )
        row = records.rows(store)[0]
        assert row["state"] == "launched"
        assert records.daily_count(store, local, now) == 1
    finally:
        store.close()
