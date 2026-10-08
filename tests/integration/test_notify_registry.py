"""INT-001: the real ps probe and a temporary Claude registry."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_factory.notify.registry import resolve

SESSION = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"


def test_resolve_live_renamed_and_reused_pid(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    pid = os.getpid()
    start = subprocess.check_output(
        ["ps", "-o", "lstart=", "-p", str(pid)], text=True, env={**os.environ, "TZ": "UTC"}
    ).strip()
    path = root / f"{pid}.json"
    value = {"sessionId": SESSION, "name": "one", "pid": pid, "procStart": start}
    path.write_text(json.dumps(value))
    (root / f"{pid}.secret.key").mkdir()
    assert resolve(SESSION, root).name == "one"  # type: ignore[union-attr]
    value["name"] = "two"
    path.write_text(json.dumps(value))
    assert resolve(SESSION, root).name == "two"  # type: ignore[union-attr]
    value["procStart"] = "wrong"
    path.write_text(json.dumps(value))
    assert resolve(SESSION, root) is None
    assert resolve(SESSION, tmp_path / "missing") is None


def test_disappearing_json_does_not_hide_another_live_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sessions"
    root.mkdir()
    vanished = root / "0.json"
    vanished.write_text("{}")
    pid = os.getpid()
    start = subprocess.check_output(
        ["ps", "-o", "lstart=", "-p", str(pid)], text=True, env={**os.environ, "TZ": "UTC"}
    ).strip()
    (root / f"{pid}.json").write_text(
        json.dumps({"sessionId": SESSION, "name": "live", "pid": pid, "procStart": start})
    )
    original_stat = Path.stat

    def flaky_stat(path: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if path == vanished:
            raise FileNotFoundError(path)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    found = resolve(SESSION, root)
    assert found is not None and found.name == "live"
