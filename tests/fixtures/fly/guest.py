"""Run the real guest init program locally and stop it without leaking its watchdog."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping

from agent_factory.fly.guest import guest_init_script


def start_guest(environment: Mapping[str, str], *, quiet: bool = False) -> subprocess.Popen[bytes]:
    """Start the guest init in its own session, so its background watchdog can be killed.

    Killing only the init leaves its watchdog subshell polling until the deadline.
    """
    stream = subprocess.DEVNULL if quiet else None
    return subprocess.Popen(
        ["bash", "-c", guest_init_script()],
        env=dict(environment),
        stdin=stream,
        stdout=stream,
        stderr=stream,
        start_new_session=True,
    )


def stop_guest(process: subprocess.Popen[bytes]) -> None:
    """Kill the guest's whole process group: the init, its watchdog, and any job."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
