"""Run the real guest init program locally and stop it without leaking its watchdog."""

from __future__ import annotations

import contextlib
import os
import select
import signal
import subprocess
from collections.abc import Mapping

from agent_factory.fly.guest import guest_init_script


def start_guest(environment: Mapping[str, str], *, quiet: bool = False) -> subprocess.Popen[bytes]:
    """Start the guest init in its own session, so its background watchdog can be killed.

    Killing only the init leaves its watchdog subshell polling until the deadline.
    """
    stream = subprocess.DEVNULL if quiet else None
    # Popen returns before bash executes the boot step. Signal readiness after
    # that step so tests deliver new jobs only once the guest is serving them.
    read_fd, write_fd = os.pipe()
    script = guest_init_script().replace("watchdog &", f"printf ready >&{write_fd}\nwatchdog &")
    try:
        process = subprocess.Popen(
            ["bash", "-c", script],
            env=dict(environment),
            stdin=stream,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
            pass_fds=(write_fd,),
        )
    finally:
        os.close(write_fd)
    try:
        if not select.select([read_fd], [], [], 30)[0] or os.read(read_fd, 5) != b"ready":
            stop_guest(process)
            raise RuntimeError("guest init did not finish booting")
    finally:
        os.close(read_fd)
    return process


def stop_guest(process: subprocess.Popen[bytes]) -> None:
    """Kill the guest's whole process group: the init, its watchdog, and any job."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
