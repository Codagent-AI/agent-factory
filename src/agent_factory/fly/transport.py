"""``flyctl`` transport and the launcher's Machine lifecycle.

Lifecycle calls go to the Machines REST client; ``flyctl`` only carries bytes to
and from a Machine whose ownership has already been verified.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_factory.fly.api import FlyApiError, FlyMachinesClient, is_gone, read_token

EXIT_TRANSPORT = 70
EXIT_MACHINE_LOST = 71
EXIT_COLLECTION_FAILED = 72
EXIT_MISMATCH = 73

OWNER = "agent-factory"
_LOG_MARKER = b"---FACTORY-LOG---\n"
# Exact per-provider allowlist, mirroring the Docker launcher's auth mounts.
_CODEX_FILES = ((".codex/auth.json", "codex/auth.json", True),)
_CLAUDE_FILES = (
    (".claude/.credentials.json", "claude/.credentials.json", True),
    (".claude/settings.json", "claude/settings.json", False),
    (".claude/settings.local.json", "claude/settings.local.json", False),
)


class FlyTransportError(RuntimeError):
    pass


class MachineLostError(RuntimeError):
    pass


class OwnershipMismatchError(RuntimeError):
    pass


class CollectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobRequest:
    """Everything one guest job needs, already validated by the launcher."""

    artifact_dir: Path
    script: str
    input_dir: Path | None
    environment: str
    codex_auth: bool
    claude_auth: bool


class FlyTransport:
    def __init__(
        self, app: str, machine_id: str | None = None, token_file: Path | None = None
    ) -> None:
        self.app = app
        self.machine_id = machine_id
        self.token_file = token_file

    @classmethod
    def from_manifest(cls, manifest: Mapping[str, object]) -> FlyTransport:
        fly = manifest.get("fly")
        if not isinstance(fly, Mapping):
            raise FlyTransportError("manifest has no Fly app")
        fly_values = cast(Mapping[str, object], fly)
        app = fly_values.get("app")
        if not isinstance(app, str):
            raise FlyTransportError("manifest has no Fly app")
        token = fly_values.get("token_file")
        return cls(app, None, Path(token) if isinstance(token, str) and token else None)

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if self.token_file is not None:
            # The deploy token reaches flyctl through its environment, never argv,
            # so it cannot appear in a process listing or a log line.
            try:
                environment["FLY_ACCESS_TOKEN"] = read_token(self.token_file)
            except FlyApiError as error:
                raise FlyTransportError(str(error)) from error
        return environment

    def _console(self, command: str) -> tuple[str, ...]:
        if not self.machine_id:
            raise FlyTransportError("Machine identity is unavailable")
        # ``-C`` is not run through a shell by Fly's ssh server, so every command
        # is handed to bash explicitly; redirects and pipes then behave.
        return (
            "flyctl",
            "ssh",
            "console",
            "--app",
            self.app,
            "--machine",
            self.machine_id,
            "--quiet",
            "-C",
            "bash -c " + shlex.quote(command),
        )

    def command(self, command: str, *, timeout: float | None = 120) -> bytes:
        try:
            result = subprocess.run(
                self._console(command),
                capture_output=True,
                check=False,
                timeout=timeout,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FlyTransportError(f"ssh could not run: {type(error).__name__}") from error
        if result.returncode:
            raise FlyTransportError(result.stderr.decode(errors="replace").strip() or "ssh failed")
        return result.stdout

    def put_file(
        self, local: Path, remote: str, *, mode: str = "0600", prepare: bool = True
    ) -> None:
        """Upload one file. ``prepare=False`` when the caller already cleared the path."""
        if not self.machine_id:
            raise FlyTransportError("Machine identity is unavailable")
        if prepare:
            # flyctl's sftp put refuses to overwrite and does not create directories.
            self.command(
                f"mkdir -p {shlex.quote(os.path.dirname(remote))}; rm -f {shlex.quote(remote)}"
            )
        # The flags belong to ``put``, not to ``sftp``.
        try:
            result = subprocess.run(
                (
                    "flyctl",
                    "ssh",
                    "sftp",
                    "put",
                    "--app",
                    self.app,
                    "--machine",
                    self.machine_id,
                    "--quiet",
                    "--mode",
                    mode,
                    str(local),
                    remote,
                ),
                capture_output=True,
                check=False,
                timeout=600,
                env=self._environment(),
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FlyTransportError(f"sftp could not run: {type(error).__name__}") from error
        if result.returncode:
            raise FlyTransportError(result.stderr.decode(errors="replace").strip() or "sftp failed")

    def put_directory(self, local: Path, remote: str) -> None:
        """Deliver a directory byte-identically as one tarball."""
        with tempfile.TemporaryDirectory() as scratch:
            bundle = Path(scratch) / "bundle.tar"
            with tarfile.open(bundle, "w") as archive:
                archive.add(local, arcname=".")
            staged = f"/tmp/factory-bundle-{os.getpid()}.tar"
            self.put_file(bundle, staged)
        target = shlex.quote(remote)
        self.command(
            f"mkdir -p {target} && tar -C {target} -xf {staged}; status=$?; rm -f {staged}; "
            "exit $status",
            timeout=600,
        )

    def tar_get(self, remote: str, destination: Path, *, timeout: float | None = 3600) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile() as spool:
                pulled = subprocess.run(
                    self._console(
                        f"tar -C {shlex.quote(remote)} --exclude ./.factory/staging -cf - ."
                    ),
                    stdout=spool,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=timeout,
                    env=self._environment(),
                    stdin=subprocess.DEVNULL,
                )
                if pulled.returncode:
                    raise CollectionError("artifact transfer was interrupted")
                spool.seek(0)
                extracted = subprocess.run(
                    ("tar", "-C", str(destination), "-xf", "-"),
                    stdin=spool,
                    capture_output=True,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CollectionError(f"artifact transfer failed: {type(error).__name__}") from error
        if extracted.returncode:
            raise CollectionError("artifact archive was incomplete")


def _log(factory: Path, message: str) -> None:
    # Diagnostics never go to stdout: the parent treats stdout as suite progress.
    with (factory / "launcher.log").open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(UTC).isoformat()} {message}\n")


class Lifecycle:
    """One launcher invocation: claim a Machine, run one job, collect it."""

    def __init__(
        self,
        manifest: Mapping[str, object],
        factory: Path,
        *,
        client: FlyMachinesClient | None = None,
        transport: FlyTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.manifest = manifest
        self.factory = factory
        self.fly = mapping_field(manifest, "fly")
        self.transport = transport or FlyTransport.from_manifest(manifest)
        self.client = client or FlyMachinesClient(
            string_field(self.fly, "app"), Path(string_field(self.fly, "token_file"))
        )
        self.sleep = sleep
        self.record_path = factory / "machine.json"

    # -- entry points -------------------------------------------------------

    def run(self, request: JobRequest) -> int:
        return self._guarded(lambda: self._run(request))

    def attach(self, artifact_dir: Path) -> int:
        return self._guarded(lambda: self._attach(artifact_dir))

    def _guarded(self, action: Callable[[], int]) -> int:
        try:
            return action()
        except MachineLostError as error:
            _log(self.factory, f"machine lost: {error}")
            return EXIT_MACHINE_LOST
        except OwnershipMismatchError as error:
            _log(self.factory, f"ownership mismatch: {error}")
            return EXIT_MISMATCH
        except CollectionError as error:
            _log(self.factory, f"collection failed: {error}")
            return EXIT_COLLECTION_FAILED
        except (FlyApiError, FlyTransportError, OSError, ValueError) as error:
            _log(self.factory, f"transport failure: {error}")
            return EXIT_TRANSPORT

    # -- claiming -----------------------------------------------------------

    def _run(self, request: JobRequest) -> int:
        machine_id, deadline = self._claim()
        self.transport.machine_id = machine_id
        job, running = self._next_job()
        if running:
            # A launcher for this same attempt died after starting the job.
            _log(self.factory, f"job {job} is already running; attaching")
        else:
            if self.manifest.get("expect_checkpoint") is True:
                present = self.transport.command(
                    "test -e /artifacts/run-state.json && printf 1 || printf 0"
                )
                if present.strip() != b"1":
                    raise MachineLostError("the suite checkpoint is missing from the Machine")
            self._deliver(request, deadline, job)
        self._update_record({"job": job})
        return self._follow(request.artifact_dir, job)

    def _attach(self, artifact_dir: Path) -> int:
        record = read_record(self.record_path)
        machine_id = record.get("id")
        if not isinstance(machine_id, str):
            raise MachineLostError("no Machine was recorded for this attempt")
        machine = self._get(machine_id)
        if machine is None:
            raise MachineLostError("recorded Machine no longer exists")
        if not _owned(machine, self._stable_metadata(record)):
            raise OwnershipMismatchError(machine_id)
        self.transport.machine_id = machine_id
        job, _running = self._next_job()
        recorded = record.get("job")
        if isinstance(recorded, int) and recorded < job:
            # The recorded job finished while no launcher was attached.
            job = recorded
        return self._follow(artifact_dir, job)

    def _claim(self) -> tuple[str, int]:
        """Return a verified Machine for this attempt and its deadline."""
        record = read_record(self.record_path)
        run_id = string_field(self.manifest, "run_id")
        recorded_id = record.get("id")
        if isinstance(recorded_id, str):
            same_attempt = record.get("run_id") == run_id or "run_id" not in record
            machine = self._get(recorded_id)
            if machine is None:
                if same_attempt and "job" not in record:
                    # Created but never used: nothing was delivered, so replace it.
                    _log(self.factory, f"recorded Machine {recorded_id} is gone; creating another")
                    return self._create(
                        [*_history(record), {"id": recorded_id, "disposal": "gone"}]
                    )
                raise MachineLostError(f"recorded Machine {recorded_id} no longer exists")
            if not _owned(machine, self._stable_metadata(record)):
                # Never adopt or terminate a Machine that is not provably ours.
                raise OwnershipMismatchError(recorded_id)
            # An adopted attempt keeps its deadline; a recovery attempt gets a fresh one.
            deadline = (_recorded_deadline(record) if same_attempt else None) or (
                self._fresh_deadline()
            )
            action = "adopting" if same_attempt else "resuming in"
            _log(self.factory, f"{action} Machine {recorded_id} with deadline {deadline}")
            self._ensure_started(recorded_id, machine, deadline, run_id)
            # The supervisor trusts the record only once it names this attempt, so
            # it is rewritten after the Machine carries the new identity.
            self._update_record({"run_id": run_id, "deadline": deadline})
            return recorded_id, deadline
        return self._create(_history(record))

    def _create(self, history: list[object]) -> tuple[str, int]:
        from agent_factory.fly.guest import guest_init_script

        deadline = self._fresh_deadline()
        run_id = string_field(self.manifest, "run_id")
        nonce = string_field(self.manifest, "nonce")
        machine = self.client.create_machine(
            image=string_field(self.manifest, "image"),
            cpu_kind=string_field(self.fly, "cpu_kind"),
            cpus=_integer(self.fly, "cpus"),
            memory_mb=_integer(self.fly, "memory_mb"),
            region=string_field(self.fly, "region"),
            factory_owner=OWNER,
            run_id=run_id,
            claim_id=string_field(self.manifest, "claim_id"),
            nonce=nonce,
            deadline_epoch=str(deadline),
            unit_key=string_field(self.manifest, "unit_key"),
            guest_init=guest_init_script(),
        )
        machine_id = string_field(machine, "id")
        # Durable before anything else happens, so a crash here is recoverable.
        _write_record(
            self.record_path,
            {
                "app": string_field(self.fly, "app"),
                "id": machine_id,
                "run_id": run_id,
                "claim_id": string_field(self.manifest, "claim_id"),
                "unit_key": string_field(self.manifest, "unit_key"),
                "nonce": nonce,
                "deadline": deadline,
                "created_at": datetime.now(UTC).isoformat(),
                "history": history,
            },
        )
        verified = self.client.get_machine(machine_id)
        expected = {**self._stable_metadata(read_record(self.record_path)), "run_id": run_id}
        if not _owned(verified, expected):
            raise OwnershipMismatchError(machine_id)
        if not self.client.wait_state(machine_id, "started", timeout_seconds=60):
            raise FlyTransportError("Machine did not start")
        started = self.client.get_machine(machine_id)
        self._update_record(
            {
                "image_ref": _image(started),
                "guest": _value(started, "config", "guest"),
                "region": _value(started, "region"),
            }
        )
        return machine_id, deadline

    def _ensure_started(
        self, machine_id: str, machine: Mapping[str, object], deadline: int, run_id: str
    ) -> None:
        state = machine.get("state")
        if state in {"stopped", "suspended"}:
            # The deadline is in the config before boot, so the guest can never
            # start against the expired one it was stopped with.
            self.client.update_stopped_deadline(machine_id, deadline, {"run_id": run_id})
            self.client.start(machine_id)
        else:
            self.client.set_metadata(machine_id, "deadline_epoch", str(deadline))
            self.client.set_metadata(machine_id, "run_id", run_id)
        if not self.client.wait_state(machine_id, "started", timeout_seconds=60):
            raise FlyTransportError("Machine did not start")
        self.transport.machine_id = machine_id
        self.transport.command(
            f"mkdir -p /var/lib/factory && printf '%s\\n' {deadline} > /var/lib/factory/deadline"
        )

    def _stable_metadata(self, record: Mapping[str, object]) -> dict[str, str]:
        """Keys that identify the repetition's Machine across attempts."""
        nonce = record.get("nonce")
        return {
            "factory-owner": OWNER,
            "claim_id": string_field(self.manifest, "claim_id"),
            "unit_key": string_field(self.manifest, "unit_key"),
            "nonce": nonce
            if isinstance(nonce, str) and nonce
            else string_field(self.manifest, "nonce"),
        }

    def _fresh_deadline(self) -> int:
        values = mapping_field(self.manifest, "deadline")
        return (
            int(time.time())
            + _integer(values, "total_seconds")
            + _integer(values, "collection_grace_seconds")
        )

    def _get(self, machine_id: str) -> Mapping[str, object] | None:
        try:
            machine = self.client.get_machine(machine_id)
        except FlyApiError as error:
            if error.status == 404:
                return None
            raise
        return None if is_gone(machine) else machine

    def _update_record(self, values: Mapping[str, object]) -> None:
        _write_record(self.record_path, {**read_record(self.record_path), **values})

    # -- delivery -----------------------------------------------------------

    def _next_job(self) -> tuple[int, bool]:
        output = self.transport.command(
            "n=1; while [ -e /artifacts/.factory/job/$n/DONE ]; do n=$((n+1)); done; "
            "if [ -e /artifacts/.factory/job/$n/start ]; then printf '%s running' $n; "
            "else printf '%s new' $n; fi"
        )
        fields = output.decode(errors="replace").split()
        if len(fields) != 2 or not fields[0].isdigit():
            raise FlyTransportError("guest job state could not be read")
        return int(fields[0]), fields[1] == "running"

    def _deliver(self, request: JobRequest, deadline: int, job: int) -> None:
        directory = f"/artifacts/.factory/job/{job}"
        credentials = self._credential_files(request)
        # One ssh round trip prepares every target, so the uploads need none each.
        self.transport.command(
            "rm -rf /eval-input /host-home /run/factory/env && "
            f"mkdir -p /run/factory /var/lib/factory /host-home/codex /host-home/claude "
            f"{directory} && chmod 700 /run/factory /host-home && rm -f {directory}/job.sh && "
            f"printf '%s\\n' {deadline} > /var/lib/factory/deadline"
        )
        if request.input_dir is not None:
            self.transport.put_directory(request.input_dir, "/eval-input")
        for path, target in credentials:
            self.transport.put_file(path, f"/host-home/{target}", prepare=False)
        with tempfile.TemporaryDirectory() as scratch:
            # Secret-bearing files are staged outside the artifact tree.
            environment = Path(scratch) / "env"
            environment.write_text(request.environment, encoding="utf-8")
            environment.chmod(0o600)
            self.transport.put_file(environment, "/run/factory/env", prepare=False)
            script = Path(scratch) / "job.sh"
            script.write_text(request.script, encoding="utf-8")
            self.transport.put_file(script, f"{directory}/job.sh", mode="0700", prepare=False)
        self.transport.command(f"touch {directory}/start")
        _log(self.factory, f"delivered job {job}")

    @staticmethod
    def _credential_files(request: JobRequest) -> list[tuple[Path, str]]:
        """Resolve the allowlist before anything is sent, so a gap fails early."""
        selected = [
            *(_CODEX_FILES if request.codex_auth else ()),
            *(_CLAUDE_FILES if request.claude_auth else ()),
        ]
        files: list[tuple[Path, str]] = []
        for source, target, required in selected:
            path = Path.home() / source
            if path.is_file():
                files.append((path, target))
            elif required:
                raise FlyTransportError(f"required credential file is missing: ~/{source}")
        return files

    # -- observation and collection ----------------------------------------

    def _follow(self, artifact_dir: Path, job: int) -> int:
        """Relay guest output to stdout and a heartbeat file until DONE."""
        import sys

        heartbeat_seconds = self.fly.get("heartbeat_seconds")
        interval = heartbeat_seconds if isinstance(heartbeat_seconds, int) else 20
        directory = f"/artifacts/.factory/job/{job}"
        offset_path = self.factory / f"job-{job}.offset"
        try:
            offset = int(offset_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            offset = 0
        heartbeat_path = self.factory / "heartbeat.json"
        heartbeat = read_record(heartbeat_path)
        failures = 0
        while True:
            try:
                raw = self.transport.command(
                    f"test -e {directory}/DONE && printf 1 || printf 0; printf '\\n'; "
                    "test -e /artifacts/run-state.json && printf 1 || printf 0; printf '\\n'; "
                    "find /artifacts -path /artifacts/.factory -prune -o -type f "
                    "-printf '%T@ %p\\n' 2>/dev/null | sort -n | tail -n 1; printf '\\n'; "
                    f"printf '%s' '{_LOG_MARKER.decode().strip()}'; printf '\\n'; "
                    f"tail -c +{offset + 1} {directory}/job.log 2>/dev/null || true"
                )
                failures = 0
            except FlyTransportError as error:
                failures += 1
                _log(self.factory, f"observation failed ({failures}): {error}")
                machine_id = self.transport.machine_id
                if machine_id and self._get(machine_id) is None:
                    raise MachineLostError("Machine disappeared during observation") from error
                self.sleep(min(interval * failures, 120))
                continue
            head, _, log = raw.partition(_LOG_MARKER)
            lines = head.decode(errors="replace").splitlines()
            done = bool(lines) and lines[0].strip() == "1"
            checkpoint = len(lines) > 1 and lines[1].strip() == "1"
            latest = lines[2].strip() if len(lines) > 2 else ""
            if log:
                sys.stdout.buffer.write(log)
                sys.stdout.buffer.flush()
                offset += len(log)
                offset_path.write_text(str(offset), encoding="utf-8")
            updated = {
                "checkpoint_seen": bool(heartbeat.get("checkpoint_seen")) or checkpoint,
                "latest": latest,
            }
            if updated != heartbeat:
                # Rewritten only on change, so relay activity is never progress.
                heartbeat = updated
                _write_record(heartbeat_path, heartbeat)
            if done:
                return self._collect(artifact_dir, job)
            self.sleep(interval)

    def _collect(self, artifact_dir: Path, job: int) -> int:
        staging = self.factory / "staging"
        shutil.rmtree(staging, ignore_errors=True)
        self.transport.tar_get("/artifacts", staging)
        job_directory = staging / ".factory" / "job" / str(job)
        listing = job_directory / "files.txt"
        code_path = job_directory / "exit-code"
        if not listing.is_file() or not code_path.is_file():
            raise CollectionError("guest manifest or exit code is missing")
        for line in listing.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) < 2 or not fields[1].isdigit():
                raise CollectionError("guest manifest is malformed")
            name = fields[0]
            if name.startswith(f".factory/job/{job}/"):
                continue  # still being written when the manifest was taken
            collected = staging / name
            if not collected.is_file() or collected.stat().st_size != int(fields[1]):
                raise CollectionError(f"collected file does not match the manifest: {name}")
        try:
            value = int(code_path.read_text(encoding="utf-8").strip())
        except ValueError as error:
            raise CollectionError("guest exit code is unreadable") from error
        _place(staging, artifact_dir)
        shutil.rmtree(staging, ignore_errors=True)
        (artifact_dir / "guest-exit-code").write_text(f"{value}\n", encoding="utf-8")
        _log(self.factory, f"collected job {job} with exit code {value}")
        return value


def _place(staging: Path, artifact_dir: Path) -> None:
    """Move a verified tree into place, the result file last.

    The supervisor finishes an attempt when ``result.json`` appears, so it must
    never appear before the evidence it summarizes.
    """
    last: list[tuple[Path, Path]] = []
    for source in sorted(path for path in staging.rglob("*") if path.is_file()):
        relative = source.relative_to(staging)
        target = artifact_dir / relative
        if relative.parts[:2] == (".factory", "staging"):
            continue
        if relative == Path("result.json"):
            last.append((source, target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    for source, target in last:
        os.replace(source, target)


def mapping_field(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"manifest has no {key}")
    return cast(Mapping[str, object], result)


def string_field(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"manifest has no {key}")
    return result


def _integer(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ValueError(f"manifest has invalid {key}")
    return result


def _recorded_deadline(record: Mapping[str, object]) -> int | None:
    value = record.get("deadline")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _history(record: Mapping[str, object]) -> list[object]:
    value = record.get("history")
    return list(cast(Sequence[object], value)) if isinstance(value, list) else []


def _owned(machine: Mapping[str, object], expected: Mapping[str, str]) -> bool:
    config = machine.get("config")
    config_values: Mapping[str, object] = (
        cast(Mapping[str, object], config) if isinstance(config, Mapping) else {}
    )
    metadata = config_values.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    metadata_values = cast(Mapping[str, object], metadata)
    return all(metadata_values.get(key) == value for key, value in expected.items())


def read_record(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        raise ValueError("machine record is invalid")
    return cast(dict[str, object], raw)


def _write_record(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps(dict(value), sort_keys=True), encoding="utf-8")
    os.replace(scratch, path)


def _image(machine: Mapping[str, object]) -> object:
    config = machine.get("config")
    if not isinstance(config, Mapping):
        return ""
    values = cast(Mapping[str, object], config)
    return machine.get("image_ref") or values.get("image_ref") or values.get("image", "")


def _value(machine: Mapping[str, object], *keys: str) -> object:
    value: object = machine
    for key in keys:
        if not isinstance(value, Mapping):
            return ""
        value = cast(Mapping[object, object], value).get(key, "")
    return value


def environment_text(env_files: Sequence[Path], env_names: Sequence[str]) -> str:
    lines: list[str] = []
    for path in env_files:
        lines.extend(path.read_text(encoding="utf-8").splitlines())
    for name in env_names:
        if name in os.environ:
            lines.append(f"{name}={shlex.quote(os.environ[name])}")
    return "\n".join(lines) + "\n"
