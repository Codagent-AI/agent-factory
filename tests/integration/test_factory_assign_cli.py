"""The factory-assign helper starts on a release that predates session notifications."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / ".claude/skills/factory-assign/assign.py"

# Runs the helper as the skill does, with agent_factory.notify missing as on an older release.
OLD_RELEASE = (
    "import runpy, sys\n"
    "sys.modules['agent_factory.notify'] = None\n"
    "sys.argv = [sys.argv[1], *sys.argv[2:]]\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)


def test_helper_starts_without_notify_package() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", OLD_RELEASE, str(HELPER), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout
    assert "Traceback" not in completed.stderr
