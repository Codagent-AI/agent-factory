# pyright: reportPrivateUsage=false
"""E2E-001: one marked pull request notifies through the public tick command."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from agent_factory.notify import store as records
from agent_factory.notify.marker import render
from tests.e2e.test_fix_cycle import Harness, _pr_outcome

SESSION = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"


def test_marked_fix_notifies_once_through_tick(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    shared_file = tmp_path / "shared.toml"
    shared_file.write_text(
        shared_file.read_text() + '\n[notify]\nenabled = true\nagent = "claude:fake:low"\n'
        "settle_seconds = 0\nwatch_wait_minutes = 0\n"
    )
    board = h.state()
    body = board["items"][0]["content"]["body"] + "\n\n" + render(SESSION, "test-session")
    board["items"][0]["content"]["body"] = body
    board["issue"]["body"] = body
    h.board.write_text(json.dumps(board))
    sessions = h.home / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    pid = os.getpid()
    start = subprocess.check_output(
        ["ps", "-o", "lstart=", "-p", str(pid)], text=True, env={**os.environ, "TZ": "UTC"}
    ).strip()
    (sessions / f"{pid}.json").write_text(
        json.dumps({"sessionId": SESSION, "name": "test-session", "pid": pid, "procStart": start})
    )
    fake = tmp_path / "bin" / "claude"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n"
        "if sys.argv[1:3] == ['auth','status']: print('ok'); sys.exit(0)\n"
        "if sys.argv[1:2] == ['--help']: "
        "print('--tools --allowedTools --json-schema --strict-mcp-config'); sys.exit(0)\n"
        "Path('invoked').write_text(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'is_error':False,'total_cost_usd':0.01,"
        "'structured_output':{'outcome':'sent','detail':'ok'}}))\n"
    )
    fake.chmod(0o755)
    h.tick()
    run = h.active_run()
    artifact = h.wait_started(run)
    claim = h.store.get_claim(run.claim_id)
    assert claim is not None
    h.finish(artifact, _pr_outcome(h.branch_for(claim.id)))
    h.tick()
    for _ in range(8):
        h.tick()
        ended = records.rows(h.store, "ended")
        if ended:
            break
        time.sleep(0.05)
    assert len(records.rows(h.store)) == 1
    row = records.rows(h.store)[0]
    assert row["outcome"] == "sent"
    assert row["message"].splitlines() == [
        "Agent Factory: example/work#1 (fix) opened or updated a pull request.",
        "Issue: https://github.com/example/work/issues/1",
        "Pull request: https://github.com/example/work/pull/214",
        f"Claim: {claim.id}",
    ]
    assert "notify ended:" in h.cli("status")
    h.cli("pause")
    h.tick()
    h.tick()
    assert len(records.rows(h.store)) == 1
