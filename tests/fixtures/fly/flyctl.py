"""A minimal executable flyctl double for doctor transport checks."""

from __future__ import annotations

import stat
from pathlib import Path


def write_flyctl(directory: Path, *, exit_code: int = 0) -> Path:
    executable = directory / "flyctl"
    executable.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable
