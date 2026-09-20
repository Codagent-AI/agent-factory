"""Small ``flyctl`` transport boundary; Machines lifecycle stays in the REST client."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast


class FlyTransportError(RuntimeError):
    pass


class FlyTransport:
    def __init__(self, app: str, machine_id: str | None = None) -> None:
        self.app = app
        self.machine_id = machine_id

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, object]) -> FlyTransport:
        fly = manifest.get("fly")
        if not isinstance(fly, Mapping):
            raise FlyTransportError("manifest has no Fly app")
        fly_values = cast(Mapping[str, object], fly)
        app = fly_values.get("app")
        if not isinstance(app, str):
            raise FlyTransportError("manifest has no Fly app")
        machine = manifest.get("machine")
        machine_values: Mapping[str, object] = (
            cast(Mapping[str, object], machine)
            if isinstance(machine, Mapping)
            else dict[str, object]()
        )
        machine_id = machine_values.get("id")
        return cls(app, machine_id if isinstance(machine_id, str) else None)

    def command(self, command: str, *, input: bytes | None = None) -> bytes:
        if not self.machine_id:
            raise FlyTransportError("Machine identity is unavailable")
        result = subprocess.run(
            (
                "flyctl",
                "ssh",
                "console",
                "--app",
                self.app,
                "--machine",
                self.machine_id,
                "-C",
                command,
            ),
            input=input,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise FlyTransportError(result.stderr.decode(errors="replace").strip() or "ssh failed")
        return result.stdout

    def put_file(self, local: Path, remote: str) -> None:
        self._sftp(("put", str(local), remote))

    def tar_get(self, remote: str, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        payload = self.command(f"tar -C {remote!s} -cf - .")
        extracted = subprocess.run(
            ("tar", "-C", str(destination), "-xf", "-"), input=payload, check=False
        )
        if extracted.returncode:
            raise FlyTransportError("tar collection failed")

    def stream(self, command: str) -> subprocess.Popen[bytes]:
        if not self.machine_id:
            raise FlyTransportError("Machine identity is unavailable")
        return subprocess.Popen(
            (
                "flyctl",
                "ssh",
                "console",
                "--app",
                self.app,
                "--machine",
                self.machine_id,
                "-C",
                command,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def launch(self, parsed: object, manifest: Mapping[str, object]) -> int:
        del parsed, manifest
        return 70

    def _sftp(self, arguments: Sequence[str]) -> None:
        if not self.machine_id:
            raise FlyTransportError("Machine identity is unavailable")
        result = subprocess.run(
            ("flyctl", "ssh", "sftp", "--app", self.app, "--machine", self.machine_id, *arguments),
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise FlyTransportError(result.stderr.decode(errors="replace").strip() or "sftp failed")
