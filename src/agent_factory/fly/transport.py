"""``flyctl`` transport and the launcher's Machine lifecycle.

Lifecycle calls go to the Machines REST client; ``flyctl`` only carries bytes to
and from a Machine whose ownership has already been verified.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_factory.fly.api import FlyApiError, FlyMachinesClient, is_gone, read_token

EXIT_TRANSPORT = 70
EXIT_MACHINE_LOST = 71
EXIT_COLLECTION_FAILED = 72
EXIT_MISMATCH = 73

OWNER = "agent-factory"
_START_WAIT_WINDOWS = 5
_LOG_MARKER = b"---FACTORY-LOG---\n"
_BUILD_TIMEOUT_SECONDS = 1800
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-fA-F]{64}")
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
class ClaudeLogin:
    kind: str
    data: bytes = field(default=b"", repr=False)
    path: Path | None = None
    reason: str = ""


def _claude_token_value(value: str) -> str:
    """Decode optional quotes on the one Docker env value used for readiness."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _valid_claude_oauth(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    document = cast(Mapping[str, object], value)
    oauth: object = document.get("claudeAiOauth")
    if not isinstance(oauth, Mapping):
        return False
    fields = cast(Mapping[str, object], oauth)
    return (
        all(
            isinstance(fields.get(key), str) and bool(fields[key])
            for key in ("accessToken", "refreshToken")
        )
        and isinstance(fields.get("expiresAt"), int)
        and not isinstance(fields["expiresAt"], bool)
    )


def service_user() -> str:
    return os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name


def resolve_claude_login(
    env_files: Sequence[Path],
    *,
    user: str | None = None,
    home: Path | None = None,
    platform: str | None = None,
    env_text: str = "",
) -> ClaudeLogin:
    """Resolve only the credential source; never expose the token or login in diagnostics."""
    for source in [env_text, *(path.read_text(encoding="utf-8") for path in env_files)]:
        for raw in source.splitlines():
            line = raw.lstrip()
            if re.match(r"export\s", line):
                line = line[6:].lstrip()
            name, separator, value = line.partition("=")
            if (
                separator
                and name.rstrip() == "CLAUDE_CODE_OAUTH_TOKEN"
                and _claude_token_value(value)
            ):
                return ClaudeLogin("token")
    credentials = (home or Path.home()) / ".claude/.credentials.json"
    if (platform or sys.platform) == "darwin":
        account = user or service_user()
        keychain = f"Claude Code-credentials Keychain item for account {account}"
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    "Claude Code-credentials",
                    "-a",
                    account,
                    "-w",
                ],
                capture_output=True,
                check=False,
                timeout=10,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ClaudeLogin("unavailable", reason=f"{keychain} read timed out")
        except OSError:
            return ClaudeLogin("unavailable", reason=f"{keychain} could not be read")
        if result.returncode == 0:
            try:
                value = json.loads(result.stdout)
            except (ValueError, UnicodeDecodeError):
                value = None
            if not _valid_claude_oauth(value):
                return ClaudeLogin("unavailable", reason=f"{keychain} is invalid")
            return ClaudeLogin("keychain", data=result.stdout)
        if result.returncode != 44:
            return ClaudeLogin("unavailable", reason=f"{keychain} read failed")
    if credentials.is_file() and os.access(credentials, os.R_OK):
        return ClaudeLogin("file", path=credentials)
    return ClaudeLogin("unavailable", reason="Claude credential file is missing or unreadable")


def image_digest(machine: Mapping[str, object]) -> str:
    value = machine.get("image_ref")
    if isinstance(value, Mapping):
        value = cast(Mapping[str, object], value).get("digest")
    if isinstance(value, str):
        match = re.search(r"sha256:[0-9a-fA-F]+", value)
        if match:
            return match.group()
    return "unavailable"


def image_repository(image: str) -> str:
    if "@" in image:
        return image.split("@", 1)[0]
    suffix = image.rsplit("/", 1)[-1]
    return image.rsplit(":", 1)[0] if ":" in suffix else image


def build_claim_image(
    app: str,
    repository: str,
    claim_id: str,
    runner: Path,
    factory: Path,
    environment: Mapping[str, str],
    client: FlyMachinesClient | None = None,
    region: str = "ewr",
) -> str:
    """Build once in the detached launcher and persist its immutable digest."""
    tag = f"claim-{claim_id[:12]}"
    image = f"{repository}:{tag}"
    command = [
        "flyctl",
        "deploy",
        "--build-only",
        "--push",
        "--remote-only",
        "-a",
        app,
        "--dockerfile",
        "docker/dev/Dockerfile",
        "--image-label",
        tag,
        "--build-arg",
        f"FACTORY_CLI_REFRESH={claim_id}",
        ".",
    ]
    with tempfile.TemporaryDirectory() as scratch:
        config = Path(scratch) / "fly.toml"
        config.write_text(f'app = "{app}"\nprimary_region = "{region}"\n', encoding="utf-8")
        command[2:2] = ["-c", str(config)]
        # The child writes directly to the open artifact, so a silent remote
        # builder cannot strand us in a blocking pipe read.
        with (factory / "image-build.log").open("wb+") as log:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=runner,
                    env=dict(environment),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as error:
                raise FlyTransportError(
                    f"Fly image build could not start: {type(error).__name__}"
                ) from error
            try:
                try:
                    code = process.wait(timeout=_BUILD_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired as error:
                    log.flush()
                    log.seek(max(0, log.seek(0, os.SEEK_END) - 2000))
                    diagnostic = log.read().decode(errors="replace")
                    raise FlyTransportError(f"Fly image build timed out: {diagnostic}") from error
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            log.flush()
            log.seek(max(0, log.seek(0, os.SEEK_END) - 20000))
            tail = log.read()
    if code:
        diagnostic = bytes(tail[-2000:]).decode(errors="replace")
        raise FlyTransportError(f"Fly image build failed: {diagnostic}")
    # BuildKit's progress reports the pushed manifest as
    # "#N pushing manifest for <image>@sha256:<digest> <time> done"; anything else
    # (cached or exported layers, other tags) is not this build's pushed image.
    pushed = list(
        re.finditer(
            rb"(?m)^(?:#\d+[ \t]+)?pushing manifest for[ \t]+"
            + re.escape(image.encode())
            + rb"@(sha256:[0-9a-fA-F]{64})\b",
            tail,
        )
    )
    digest = pushed[-1].group(1).decode() if pushed else ""
    if not digest and client is not None:
        digest = client.resolve_manifest(image)
    if not digest:
        raise FlyTransportError("Fly image build returned no digest")
    _write_record(
        factory / "image-build.json", {"repository": repository, "tag": tag, "digest": digest}
    )
    return f"{repository}@{digest}"


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

    def deploy_environment(self) -> dict[str, str]:
        return self._environment()

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

    def put_bytes(self, data: bytes, remote: str) -> None:
        """Stream credential bytes into a private guest file without a host file."""
        target = shlex.quote(remote)
        command = (
            f"umask 077; cat > {target}.tmp && chmod 0600 {target}.tmp && mv {target}.tmp {target}"
        )
        try:
            result = subprocess.run(
                (*self._console(command)[:-2], "--pty=false", *self._console(command)[-2:]),
                input=data,
                capture_output=True,
                check=False,
                timeout=120,
                env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FlyTransportError(
                f"ssh credential delivery failed: {type(error).__name__}"
            ) from error
        if result.returncode:
            raise FlyTransportError("ssh credential delivery failed")

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
                with tarfile.open(fileobj=spool, mode="r:") as archive:
                    archive.extractall(destination, filter=_regular_members_only)
        except (OSError, subprocess.TimeoutExpired, tarfile.TarError) as error:
            raise CollectionError(f"artifact transfer failed: {type(error).__name__}") from error


def _regular_members_only(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
    """Extraction filter for an archive produced by an untrusted job.

    Links and special files are never placed, so they are skipped rather than
    extracted; a real eval tree may hold harmless ones. Everything kept still
    passes the stdlib data filter, which refuses absolute paths and traversal.
    """
    if not (member.isreg() or member.isdir()):
        return None
    return tarfile.data_filter(member, destination)


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
            stage = getattr(self, "_stage", "credential")
            if stage != "suite":
                _write_record(
                    self.factory / "launch-stage.json",
                    {"failure_stage": "pre-suite", "stage": stage, "detail": str(error)[-2000:]},
                )
            _log(self.factory, f"transport failure: {error}")
            return EXIT_TRANSPORT

    # -- claiming -----------------------------------------------------------

    def _run(self, request: JobRequest) -> int:
        (self.factory / "launch-stage.json").unlink(missing_ok=True)
        self._stage = "credential"
        # Validate all local credential sources before creating a billable Machine.
        self._resolved_credentials = self._credential_files(request)
        self._claude_login = (
            resolve_claude_login([], env_text=request.environment)
            if request.claude_auth
            else ClaudeLogin("token")
        )
        if self._claude_login.kind == "unavailable":
            raise FlyTransportError(self._claude_login.reason)
        if self._claude_login.kind == "file" and self._claude_login.path is not None:
            self._resolved_credentials.append((self._claude_login.path, "claude/.credentials.json"))
        elif self._claude_login.kind == "token" and request.claude_auth:
            optional = Path.home() / ".claude/.credentials.json"
            if optional.is_file():
                self._resolved_credentials.append((optional, "claude/.credentials.json"))
        self._stage = "machine"
        machine_id, deadline = self._claim()
        self.transport.machine_id = machine_id
        self._stage = "delivery"
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
        self._stage = "suite"
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
        image = self._machine_image()
        self._stage = "machine"
        machine = self.client.create_machine(
            image=image,
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
        self._wait_started(machine_id)
        started = self.client.get_machine(machine_id)
        self._update_record(
            {
                "image_ref": _image(started),
                "guest": _value(started, "config", "guest"),
                "region": _value(started, "region"),
            }
        )
        return machine_id, deadline

    def _machine_image(self) -> str:
        repository = self.manifest.get("image_repository")
        if not isinstance(repository, str):
            # Existing manifests remain usable by launchers already in flight.
            return string_field(self.manifest, "image")
        digest = self.manifest.get("image_digest")
        if isinstance(digest, str) and digest.startswith("sha256:"):
            return f"{repository}@{digest}"
        self._stage = "build"
        built = read_record(self.factory / "image-build.json")
        if built:
            claim_id = string_field(self.manifest, "claim_id")
            recorded_digest = built.get("digest")
            if (
                built.get("repository") != repository
                or built.get("tag") != f"claim-{claim_id[:12]}"
                or not isinstance(recorded_digest, str)
                or _DIGEST_PATTERN.fullmatch(recorded_digest) is None
            ):
                raise FlyTransportError("Fly image build record is invalid for this claim")
            return f"{repository}@{recorded_digest}"
        worktrees = mapping_field(self.manifest, "worktrees")
        runner = Path(string_field(worktrees, "runner"))
        return build_claim_image(
            string_field(self.fly, "app"),
            repository,
            string_field(self.manifest, "claim_id"),
            runner,
            self.factory,
            self.transport.deploy_environment(),
            self.client,
            string_field(self.fly, "region"),
        )

    def _ensure_started(
        self, machine_id: str, machine: Mapping[str, object], deadline: int, run_id: str
    ) -> None:
        state = machine.get("state")
        if state in {"stopped", "suspended"}:
            # The deadline is in the config before boot, so the guest can never
            # start against the expired one it was stopped with.
            self.client.update_stopped_deadline(machine_id, deadline, {"run_id": run_id})
            # A config update leaves the Machine "replacing" for a moment, and Fly
            # answers a start in that state with HTTP 412.
            if not self.client.wait_state(machine_id, "stopped", timeout_seconds=60):
                raise FlyTransportError(f"Machine {machine_id} did not settle after its update")
            self.client.start(machine_id)
        else:
            self.client.set_metadata(machine_id, "deadline_epoch", str(deadline))
            self.client.set_metadata(machine_id, "run_id", run_id)
        self._wait_started(machine_id)
        self.transport.machine_id = machine_id
        self.transport.command(
            f"mkdir -p /var/lib/factory && printf '%s\\n' {deadline} > /var/lib/factory/deadline"
        )

    def _wait_started(self, machine_id: str) -> None:
        """Fly caps one wait at 60 s; a first image pull on a host can take minutes."""
        for _ in range(_START_WAIT_WINDOWS):
            if self.client.wait_state(machine_id, "started", timeout_seconds=60):
                return
        raise FlyTransportError(f"Machine {machine_id} did not start")

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
        credentials = self._resolved_credentials
        # One ssh round trip prepares every target, so the uploads need none each.
        self.transport.command(
            "rm -rf /eval-input /host-home /run/factory/env && "
            f"mkdir -p /run/factory /var/lib/factory /host-home/codex /host-home/claude "
            f"{directory} && chmod 711 /run/factory /host-home && rm -f {directory}/job.sh && "
            f"printf '%s\\n' {deadline} > /var/lib/factory/deadline"
        )
        if request.input_dir is not None:
            self.transport.put_directory(request.input_dir, "/eval-input")
        for path, target in credentials:
            self.transport.put_file(path, f"/host-home/{target}", prepare=False)
        if self._claude_login.kind == "keychain":
            self.transport.put_bytes(self._claude_login.data, "/host-home/claude/.credentials.json")
        with tempfile.TemporaryDirectory() as scratch:
            # Secret-bearing files are staged outside the artifact tree.
            environment = Path(scratch) / "env"
            environment.write_text(request.environment, encoding="utf-8")
            environment.chmod(0o600)
            self.transport.put_file(environment, "/run/factory/env", prepare=False)
            script = Path(scratch) / "job.sh"
            script.write_text(request.script, encoding="utf-8")
            self.transport.put_file(script, f"{directory}/job.sh", mode="0700", prepare=False)
        # ssh delivers as root, but the image runs the guest init, and so the job,
        # as its own user. Only the job's own children are handed to the owner of a
        # directory the init created. Their parents stay root-owned (traversable,
        # not listable), so a job cannot swap them for links that a later root
        # delivery would follow; the change itself never follows a link either.
        handed_over = [directory, "/host-home/codex", "/host-home/claude", "/run/factory/env"]
        if request.input_dir is not None:
            handed_over.append("/eval-input")
        self.transport.command(
            'owner="$(stat -c %u:%g /artifacts/.factory/job 2>/dev/null '
            '|| stat -f %u:%g /artifacts/.factory/job)" && '
            f'chown -R -h -P "$owner" {" ".join(handed_over)} && touch {directory}/start'
        )
        _log(self.factory, f"delivered job {job}")

    @staticmethod
    def _credential_files(request: JobRequest) -> list[tuple[Path, str]]:
        """Resolve the allowlist before anything is sent, so a gap fails early."""
        selected = [
            *(_CODEX_FILES if request.codex_auth else ()),
            *((entry for entry in _CLAUDE_FILES if not entry[2]) if request.claude_auth else ()),
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
        try:
            heartbeat = read_record(heartbeat_path)
        except ValueError:
            heartbeat = {}  # only a progress hint; it is rewritten below
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
        # The job already emptied these before DONE; root removes the shells.
        self.transport.command("rm -rf /host-home /run/factory/env")
        staging = self.factory / "staging"
        shutil.rmtree(staging, ignore_errors=True)
        self.transport.tar_get("/artifacts", staging)
        job_directory = staging / ".factory" / "job" / str(job)
        listing = job_directory / "files.txt"
        code_path = job_directory / "exit-code"
        if not listing.is_file() or not code_path.is_file():
            raise CollectionError("guest manifest or exit code is missing")
        declared = _declared_files(listing, staging, job)
        try:
            value = int(code_path.read_text(encoding="utf-8").strip())
        except ValueError as error:
            raise CollectionError("guest exit code is unreadable") from error
        placed = _place(staging, artifact_dir, declared)
        shutil.rmtree(staging, ignore_errors=True)
        (artifact_dir / "guest-exit-code").write_text(f"{value}\n", encoding="utf-8")
        if not (artifact_dir / ".factory" / "job" / str(job) / "setup-complete").is_file():
            _write_record(
                self.factory / "launch-stage.json",
                {"failure_stage": "pre-suite", "stage": "guest-setup"},
            )
        _log(self.factory, f"collected job {job}: {placed} files, exit code {value}")
        return value


def _is_regular(path: Path) -> bool:
    """True only for a real file; a symlink to one does not count."""
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _declared_files(listing: Path, staging: Path, job: int) -> list[Path]:
    """Relative paths the guest declared and the collection really contains.

    The guest job is untrusted. Only regular files it listed, with the size it
    listed, are eligible; the guest's ``.factory`` tree is host-managed state and
    is accepted only for job evidence, so a job cannot overwrite the Machine
    record or manifest. The current job's own evidence files were still being
    written when the listing was taken, so they are checked for type only.
    """
    job_prefix = (".factory", "job", str(job))
    declared: list[Path] = []
    for line in listing.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or not fields[1].isdigit():
            raise CollectionError("guest manifest is malformed")
        relative = Path(fields[0])
        if relative.is_absolute() or ".." in relative.parts:
            raise CollectionError(f"guest manifest names an unsafe path: {fields[0]}")
        if relative.parts[:1] == (".factory",) and relative.parts[:2] != (".factory", "job"):
            continue
        collected = staging / relative
        current_job = relative.parts[:3] == job_prefix
        if not _is_regular(collected) or (
            not current_job and collected.lstat().st_size != int(fields[1])
        ):
            raise CollectionError(f"collected file does not match the manifest: {fields[0]}")
        declared.append(relative)
    for late in ("DONE", "files.txt", "exit-code"):
        relative = Path(*job_prefix, late)
        if relative not in declared and _is_regular(staging / relative):
            declared.append(relative)
    return declared


def _place(staging: Path, artifact_dir: Path, declared: Sequence[Path]) -> int:
    """Move the declared files into place, the result file last.

    Anything the archive held beyond the declared files, including every
    symlink, is left in staging and discarded. The supervisor finishes an
    attempt when ``result.json`` appears, so it must never appear before the
    evidence it summarizes.
    """
    result = Path("result.json")
    ordered = sorted(path for path in declared if path != result)
    if result in declared:
        ordered.append(result)
    for relative in ordered:
        target = artifact_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        os.replace(staging / relative, target)
    return len(ordered)


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
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        # An unreadable record must never read as "no Machine": that would create
        # a second billed Machine while the first keeps running.
        raise ValueError(f"record is invalid: {path}") from error
    if not isinstance(raw, dict):
        raise ValueError(f"record is invalid: {path}")
    return cast(dict[str, object], raw)


def _write_record(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    scratch.write_text(json.dumps(dict(value), sort_keys=True), encoding="utf-8")
    os.replace(scratch, path)


def _image(machine: Mapping[str, object]) -> object:
    return image_digest(machine)


def _value(machine: Mapping[str, object], *keys: str) -> object:
    value: object = machine
    for key in keys:
        if not isinstance(value, Mapping):
            return ""
        value = cast(Mapping[object, object], value).get(key, "")
    return value


def environment_text(env_files: Sequence[Path], env_names: Sequence[str]) -> str:
    # The guest sources this file, but env files hold Docker-style literal values:
    # each one is re-quoted so quotes, spaces, and substitutions stay data.
    lines: list[str] = []
    for path in env_files:
        for raw in path.read_text(encoding="utf-8").splitlines():
            # Only the parsing prefix is trimmed; a value's trailing spaces are data.
            line = raw.lstrip()
            if not line.strip() or line.startswith("#"):
                continue
            if re.match(r"export\s", line):
                line = line[6:].lstrip()
            name, separator, value = line.partition("=")
            name = name.rstrip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"env file {path} has an invalid entry name")
            if separator:
                if name == "CLAUDE_CODE_OAUTH_TOKEN":
                    value = _claude_token_value(value)
                lines.append(f"{name}={shlex.quote(value)}")
            elif name in os.environ:
                lines.append(f"{name}={shlex.quote(os.environ[name])}")
    for name in env_names:
        if name in os.environ:
            lines.append(f"{name}={shlex.quote(os.environ[name])}")
    return "\n".join(lines) + "\n"
