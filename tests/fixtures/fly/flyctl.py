"""Executable flyctl doubles.

``write_flyctl`` is the inert double used by doctor checks. ``write_guest_flyctl``
emulates ssh and sftp against a local directory standing in for a Machine's
filesystem, so the launcher's real delivery, observation, and collection code runs.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

_GUEST_PATHS = (
    "/artifacts",
    "/run/factory",
    "/var/lib/factory",
    "/eval-input",
    "/host-home",
    "/tmp/factory-bundle",
)

_SHIM = r"""#!{python}
import json, os, shutil, subprocess, sys

ROOT = {root!r}
LOG = {log!r}
PATHS = {paths!r}
PER_MACHINE = {per_machine!r}


def rewrite(text):
    for path in PATHS:
        text = text.replace(path, ROOT + path)
    return text


def option(args, name):
    return args[args.index(name) + 1] if name in args else None


args = sys.argv[1:]
if PER_MACHINE:
    # Each Machine has its own filesystem: one directory per Machine id.
    ROOT = os.path.join(ROOT, option(args, "--machine") or "no-machine")
with open(LOG, "a", encoding="utf-8") as stream:
    stream.write(json.dumps({{
        "argv": args,
        "token_in_env": bool(os.environ.get("FLY_ACCESS_TOKEN")),
        "record_exists": os.path.exists({record!r}),
    }}) + "\n")
if os.path.exists(ROOT + "/.unreachable"):
    sys.stderr.write("no route to machine\n")
    sys.exit(1)
if args[:1] == ["deploy"]:
    app = option(args, "-a")
    claim = option(args, "--build-arg").split("=", 1)[1]
    sys.stdout.write(
        "#14 pushing manifest for registry.fly.io/" + app + ":claim-" + claim[:12]
        + "@sha256:" + "0" * 64 + " 0.4s done\n"
    )
    sys.exit(0)
if args[:2] == ["machine", "list"]:
    sys.exit(0)
if args[:2] == ["ssh", "console"]:
    command = rewrite(option(args, "-C"))
    done = subprocess.run(["bash", "-c", command], capture_output=True)
    output = done.stdout
    if os.path.exists(ROOT + "/.truncate-tar") and "-cf -" in command:
        output = output[: len(output) // 2]
    sys.stdout.buffer.write(output)
    sys.stderr.buffer.write(done.stderr)
    sys.exit(done.returncode)
if args[:3] == ["ssh", "sftp", "put"]:
    local, remote = args[-2], ROOT + args[-1]
    if os.path.exists(remote):
        sys.stderr.write("file exists\n")
        sys.exit(1)
    os.makedirs(os.path.dirname(remote), exist_ok=True)
    if remote.endswith("job.sh"):
        with open(local, encoding="utf-8") as source, open(remote, "w", encoding="utf-8") as out:
            out.write(rewrite(source.read()))
    else:
        shutil.copyfile(local, remote)
    os.chmod(remote, int(option(args, "--mode") or "0644", 8))
    sys.exit(0)
sys.stderr.write("unsupported flyctl invocation\n")
sys.exit(64)
"""


def write_flyctl(directory: Path, *, exit_code: int = 0) -> Path:
    executable = directory / "flyctl"
    executable.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def write_guest_flyctl(
    directory: Path, guest_root: Path, log: Path, record: Path, *, per_machine: bool = False
) -> Path:
    """Write the guest double; ``per_machine`` roots each Machine at ``guest_root/<id>``."""
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "flyctl"
    executable.write_text(
        _SHIM.format(
            python=sys.executable,
            root=str(guest_root),
            log=str(log),
            paths=_GUEST_PATHS,
            record=str(record),
            per_machine=per_machine,
        ),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable
