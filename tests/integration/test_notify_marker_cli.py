"""INT-002: marker commands preserve prose and an executable eval request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_factory.notify.marker import parse
from agent_factory.work_kinds.eval import EvalDefaults, parse_request

SESSION = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"


def _command(*paths: str, environment: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "agent_factory.notify.marker", *paths],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_stamp_carry_and_eval_template(tmp_path: Path) -> None:
    sessions = tmp_path / ".claude" / "sessions"
    sessions.mkdir(parents=True)
    pid = os.getpid()
    start = subprocess.check_output(
        ["ps", "-o", "lstart=", "-p", str(pid)], text=True, env={**os.environ, "TZ": "UTC"}
    ).strip()
    (sessions / f"{pid}.json").write_text(
        json.dumps(
            {"sessionId": SESSION, "name": "agent-factory-ab", "pid": pid, "procStart": start}
        )
    )
    env = {**os.environ, "HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": SESSION}
    body = tmp_path / "body.md"
    original = Path("tests/fixtures/eval-request.md").read_text()
    body.write_text(original)
    _command("stamp", str(body), environment=env)
    stamped = body.read_text()
    assert stamped.startswith(original)
    assert stamped.count("codagent-session:") == 1
    saved = parse(stamped)
    assert saved is not None
    assert saved["session_id"] == SESSION and saved["name"] == "agent-factory-ab"
    assert stamped.count("```eval") == 1
    defaults = EvalDefaults(
        "main",
        "main",
        {role: "codex:gpt-5.6-sol:high" for role in ("lead", "implementor", "tester")},
        False,
        3,
    )
    parse_request(stamped, defaults)
    edited = tmp_path / "edited.md"
    edited.write_text("Edited prose\n")
    _command("carry", str(body), str(edited), environment=env)
    carried = parse(edited.read_text())
    assert carried is not None and carried["session_id"] == SESSION
    unmarked = tmp_path / "unmarked.md"
    unmarked.write_text("plain\n")
    env.pop("CLAUDE_CODE_SESSION_ID")
    _command("stamp", str(unmarked), environment=env)
    assert unmarked.read_text() == "plain\n"


def test_marker_command_prints_no_interpreter_warning(tmp_path: Path) -> None:
    """Acceptance F-1: `-m agent_factory.notify.marker` must not re-execute a loaded module."""
    body = tmp_path / "body.md"
    body.write_text("Plain prose.\n")
    environment = {
        key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"
    }
    environment["HOME"] = str(tmp_path)
    environment["PYTHONWARNINGS"] = "default"
    for arguments in (("stamp", str(body)), ("carry", str(body), str(body))):
        result = subprocess.run(
            [sys.executable, "-m", "agent_factory.notify.marker", *arguments],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stderr == ""
        assert len(result.stdout.splitlines()) == 1
    assert body.read_text() == "Plain prose.\n"
