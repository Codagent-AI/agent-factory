"""Resolve a recorded UUID against Claude Code's local live-session registry."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class LiveSession:
    session_id: str
    name: str
    pid: int


def resolve(session_id: str, root: Path | None = None) -> LiveSession | None:
    root = root or Path.home() / ".claude" / "sessions"
    try:
        if not root.is_dir() or not os.access(root, os.R_OK | os.X_OK):
            return None
        matches: list[LiveSession] = []
        for path in root.glob("*.json"):
            try:
                if path.name.count(".") != 1 or path.is_symlink() or path.stat().st_size > 65536:
                    continue
                value: Any = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    continue
                value = cast(dict[str, object], value)
                if value.get("sessionId") != session_id:
                    continue
                name, pid, start = (value.get(key) for key in ("name", "pid", "procStart"))
                if (
                    not isinstance(name, str)
                    or not isinstance(pid, int)
                    or not isinstance(start, str)
                ):
                    continue
                probe = subprocess.run(
                    ["ps", "-o", "lstart=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                    env={**os.environ, "TZ": "UTC"},
                )
                if probe.returncode == 0 and " ".join(probe.stdout.split()) == " ".join(
                    start.split()
                ):
                    matches.append(LiveSession(session_id, name, pid))
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None
