"""Small ``flyctl`` transport boundary; Machines lifecycle stays in the REST client."""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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

    def launch(self, parsed: object, manifest: Mapping[str, object], factory: Path) -> int:
        """Create/adopt a Machine before delivering inputs, then collect on DONE.

        This deliberately keeps API lifecycle and ssh transport separate: no
        secret-bearing transfer starts until persisted metadata has been read
        back and compared with the manifest.
        """
        from agent_factory.fly.api import FlyApiError, FlyMachinesClient
        from agent_factory.fly.guest import guest_init_script, job_script
        from agent_factory.fly.launcher import LaunchArguments

        if not isinstance(parsed, LaunchArguments):
            return 70
        fly = _mapping(manifest, "fly")
        try:
            app = _string(fly, "app")
            token = Path(_string(fly, "token_file"))
            client = FlyMachinesClient(app, token)
            deadline = _deadline(manifest)
            expected = _metadata(manifest, deadline)
            record = _read_record(factory / "machine.json")
            recorded_id = record.get("id")
            machine = client.get_machine(recorded_id) if isinstance(recorded_id, str) else None
            if machine is not None and not _owned(machine, expected):
                client.destroy(_string(record, "id"))
                _write_record(
                    factory / "machine.json", {"history": [record, {"disposal": "mismatch"}]}
                )
                machine = None
            if machine is None:
                machine = client.create_machine(
                    image=_string(manifest, "image"),
                    cpu_kind=_string(fly, "cpu_kind"),
                    cpus=_integer(fly, "cpus"),
                    memory_mb=_integer(fly, "memory_mb"),
                    region=_string(fly, "region"),
                    factory_owner="agent-factory",
                    run_id=_string(manifest, "run_id"),
                    claim_id=_string(manifest, "claim_id"),
                    nonce=_string(manifest, "nonce"),
                    deadline_epoch=str(deadline),
                    unit_key=_string(manifest, "unit_key"),
                    guest_init=guest_init_script(),
                )
                _write_record(
                    factory / "machine.json",
                    {
                        "app": app,
                        "id": _string(machine, "id"),
                        "nonce": expected["nonce"],
                        "deadline": deadline,
                        "created_at": datetime.now(UTC).isoformat(),
                        "image_ref": _image(machine),
                    },
                )
            machine_id = _string(machine, "id")
            verified = client.get_machine(machine_id)
            if not _owned(verified, expected):
                return 73
            self.machine_id = machine_id
            _write_record(
                factory / "machine.json",
                {
                    **_read_record(factory / "machine.json"),
                    "id": machine_id,
                    "deadline": deadline,
                    "image_ref": _image(verified),
                    "guest": _value(verified, "config", "guest"),
                    "region": _value(verified, "region"),
                },
            )
            self._deliver(parsed, manifest, deadline, job_script(manifest, parsed.script))
            return self._collect(parsed.artifact_dir, factory)
        except (FlyApiError, FlyTransportError, OSError, ValueError):
            return 70

    def _deliver(
        self, parsed: object, manifest: Mapping[str, object], deadline: int, script: str
    ) -> None:
        from agent_factory.fly.launcher import LaunchArguments

        if not isinstance(parsed, LaunchArguments):
            raise FlyTransportError("invalid launch request")
        # sftp transports secrets and ordinary inputs without putting either in
        # Machine config.  The guest paths are owned and mode-restricted there.
        self.command("mkdir -p /run/factory /var/lib/factory /artifacts/.factory/job/1")
        self.command(f"printf '%s\\n' {deadline} > /var/lib/factory/deadline")
        environment = "\n".join(_environment_lines(parsed)) + "\n"
        local_env = parsed.artifact_dir / ".factory" / "guest.env"
        local_job = parsed.artifact_dir / ".factory" / "job.sh"
        local_env.write_text(environment, encoding="utf-8")
        local_job.write_text(script, encoding="utf-8")
        local_job.chmod(0o700)
        try:
            self.put_file(local_env, "/run/factory/env")
            self.put_file(local_job, "/artifacts/.factory/job/1/job.sh")
        finally:
            local_env.unlink(missing_ok=True)
            local_job.unlink(missing_ok=True)
        self.command("chmod 700 /run/factory/env /artifacts/.factory/job/1/job.sh")
        self.command("touch /artifacts/.factory/job/1/start")

    def _collect(self, artifact: Path, factory: Path) -> int:
        done = "/artifacts/.factory/job/1/DONE"
        for _ in range(3600):
            if self.command(f"test -e {done}; printf %s $?").strip() == b"0":
                staging = factory / "staging"
                self.tar_get("/artifacts", staging)
                exit_code = staging / ".factory/job/1/exit-code"
                if not exit_code.is_file():
                    return 72
                value = int(exit_code.read_text(encoding="utf-8").strip())
                (artifact / "guest-exit-code").write_text(f"{value}\n", encoding="utf-8")
                return value
            time.sleep(1)
        return 72

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


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"manifest has no {key}")
    return cast(Mapping[str, object], result)


def _string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"manifest has no {key}")
    return result


def _integer(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError(f"manifest has invalid {key}")
    return result


def _deadline(manifest: Mapping[str, object]) -> int:
    values = _mapping(manifest, "deadline")
    return (
        int(time.time())
        + _integer(values, "total_seconds")
        + _integer(values, "collection_grace_seconds")
    )


def _metadata(manifest: Mapping[str, object], deadline: int) -> dict[str, str]:
    return {
        "factory-owner": "agent-factory",
        "run_id": _string(manifest, "run_id"),
        "claim_id": _string(manifest, "claim_id"),
        "nonce": _string(manifest, "nonce"),
        "deadline_epoch": str(deadline),
        "unit_key": _string(manifest, "unit_key"),
    }


def _owned(machine: Mapping[str, object], expected: Mapping[str, str]) -> bool:
    config = machine.get("config")
    config_values: Mapping[str, object] = (
        cast(Mapping[str, object], config) if isinstance(config, Mapping) else {}
    )
    metadata = config_values.get("metadata")
    metadata_values: Mapping[str, object] = (
        cast(Mapping[str, object], metadata) if isinstance(metadata, Mapping) else {}
    )
    return isinstance(metadata, Mapping) and all(
        metadata_values.get(key) == value for key, value in expected.items()
    )


def _read_record(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("machine record is invalid")
    return cast(dict[str, object], raw)


def _write_record(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), sort_keys=True), encoding="utf-8")


def _image(machine: Mapping[str, object]) -> object:
    config = machine.get("config")
    if not isinstance(config, Mapping):
        return ""
    values = cast(Mapping[str, object], config)
    return values.get("image_ref", values.get("image", ""))


def _value(machine: Mapping[str, object], *keys: str) -> object:
    if not keys:
        return machine
    value = machine.get(keys[0], "")
    if len(keys) == 1:
        return value
    if not isinstance(value, Mapping):
        return ""
    return _value(cast(Mapping[str, object], value), *keys[1:])


def _environment_lines(parsed: object) -> list[str]:
    from agent_factory.fly.launcher import LaunchArguments

    if not isinstance(parsed, LaunchArguments):
        return []
    result: list[str] = []
    for path in parsed.env_files:
        result.extend(path.read_text(encoding="utf-8").splitlines())
    for name in parsed.env_names:
        if name in os.environ:
            result.append(f"{name}={os.environ[name]}")
    return result
