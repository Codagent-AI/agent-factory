"""Drive one and-scene evaluation repetition on a Fly Machine, one step per command.

Every command prints one JSON object. Nothing here prints a credential: the Fly
token and the suite environment file are read only by code, and only variable
names or states ever reach the output. The commands never touch the factory
database or its live release; per-repetition state lives under ``<run-dir>/.factory``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from agent_factory.config import ConfigurationError, FlyLocalConfig, LocalConfig
from agent_factory.fly.api import DIGEST_PATTERN, FlyApiError, FlyMachinesClient, read_token
from agent_factory.fly.guest import job_script
from agent_factory.fly.transport import (
    FlyTransport,
    FlyTransportError,
    JobRequest,
    Lifecycle,
    OwnershipMismatchError,
    environment_text,
    image_repository,
    read_record,
    resolve_claude_login,
)

OWNER = "agent-evals-archon"
IMAGE_TAG_PREFIX = "archon-eval-"
LOST_AFTER_FAILURES = 5
_NAME = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.MULTILINE)
_EVAL_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class CliError(RuntimeError):
    pass


def _emit(value: Mapping[str, object]) -> None:
    print(json.dumps(value, sort_keys=True))


def _write(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _read(path: Path) -> dict[str, object]:
    return read_record(path)


def _fly(config: Path) -> tuple[LocalConfig, FlyLocalConfig]:
    local = LocalConfig.from_file(config)
    if local.fly is None:
        raise CliError("the configuration has no [fly] settings")
    return local, local.fly


def _client(fly: FlyLocalConfig) -> FlyMachinesClient:
    return FlyMachinesClient(fly.app, fly.token_file)


def _version() -> str:
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _eval_id(value: str) -> str:
    if not _EVAL_ID.fullmatch(value):
        raise CliError("the eval id must be lowercase letters, digits and hyphens")
    return value


# -- settings, preflight -----------------------------------------------------


def command_env_file(options: argparse.Namespace) -> int:
    local, _fly_settings = _fly(options.config)
    print(local.credentials.suite_environment)
    return 0


def command_settings(options: argparse.Namespace) -> int:
    local, fly = _fly(options.config)
    _emit(
        {
            "app": fly.app,
            "region": fly.region,
            "cpu_kind": fly.cpu_kind,
            "cpus": fly.cpus,
            "memory_mb": fly.memory_mb,
            "env_file": str(local.credentials.suite_environment),
            "owner": OWNER,
        }
    )
    return 0


def command_preflight(options: argparse.Namespace) -> int:
    local, fly = _fly(options.config)
    errors: list[str] = []
    warnings: list[str] = []
    client = _client(fly)
    try:
        read_token(fly.token_file)
        client.get_app()
    except FlyApiError as error:
        errors.append(f"the Fly app is not reachable: {error}")
    env_file = options.env_file or local.credentials.suite_environment
    names: set[str] = set()
    try:
        names = set(_NAME.findall(Path(env_file).read_text(encoding="utf-8")))
    except OSError:
        errors.append("the suite environment file is unreadable")
    if names and not names & {"GITHUB_TOKEN", "GH_TOKEN"}:
        errors.append("the suite environment file defines neither GITHUB_TOKEN nor GH_TOKEN")
    claude = "not-requested"
    if options.claude_auth:
        login = resolve_claude_login([Path(env_file)])
        claude = login.kind
        if login.kind == "unavailable":
            errors.append(f"no usable Claude login: {login.reason}")
    codex = "not-requested"
    if options.codex_auth:
        codex = "present" if (Path.home() / ".codex/auth.json").is_file() else "missing"
        if codex == "missing":
            errors.append("the Codex login file is missing")
    leftovers: list[str] = []
    try:
        for machine in client.list_machines("factory-owner", options.owner):
            identifier = machine.get("id")
            if isinstance(identifier, str):
                leftovers.append(identifier)
    except FlyApiError as error:
        errors.append(f"machines could not be listed: {error}")
    if leftovers:
        warnings.append(f"{len(leftovers)} machine(s) owned by {options.owner} already exist")
    _emit(
        {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "env_names": sorted(names),
            "claude_login": claude,
            "codex_login": codex,
            "leftover_machines": leftovers,
            "factory_version": _version(),
        }
    )
    return 0 if not errors else 1


# -- image -------------------------------------------------------------------


def _archon_bun_version(archon: Path) -> str:
    package = json.loads((archon / "package.json").read_text(encoding="utf-8"))
    version = package.get("engines", {}).get("bun")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise CliError("Archon's package.json does not pin an exact Bun version")
    return version


def _git(*arguments: str) -> str:
    result = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise CliError(f"git {arguments[0]} failed: {result.stderr.strip()[-500:]}")
    return result.stdout


def _last(lines: Sequence[str], keyword: str) -> str:
    for line in reversed(lines):
        if line.lstrip().upper().startswith(f"{keyword} "):
            return line
    raise CliError(f"the Runner Dockerfile has no final {keyword}")


def archon_dockerfile(base: str, bun: str, archon_commit: str, openspec: str) -> str:
    """The Runner base image plus Archon at its frozen commit and a pinned OpenSpec."""
    lines = base.splitlines()
    final = max(i for i, line in enumerate(lines) if line.lstrip().upper().startswith("FROM "))
    user = _last(lines[final + 1 :], "USER")
    workdir = _last(lines[final + 1 :], "WORKDIR")
    tail = f"""
USER root
COPY --chmod=a+rX archon /opt/archon
COPY --chmod=0755 sandbox-sync-home.sh /opt/archon-eval/sandbox-sync-home.sh
RUN set -eu; \\
    export HOME=/root npm_config_cache=/root/.npm; \\
    npm install -g --prefix /opt/bun bun@{bun}; \\
    ln -sf /opt/bun/bin/bun /usr/local/bin/bun; \\
    cd /opt/archon; bun install --frozen-lockfile; \\
    npm install -g @fission-ai/openspec@{openspec}; \\
    printf '%s\\n' '#!/bin/sh' 'exec bun /opt/archon/packages/cli/src/cli.ts "$@"' \\
      >/usr/local/bin/archon; \\
    chmod +x /usr/local/bin/archon; \\
    mkdir -p /agent-workflows-source; chown pwuser /agent-workflows-source; \\
    chmod -R a+rX /opt/archon/node_modules; \\
    [ "$(git -C /opt/archon rev-parse HEAD)" = "{archon_commit}" ]; \\
    [ "$(bun --version)" = "{bun}" ]; \\
    [ "$(openspec --version)" = "{openspec}" ]
{user}
{workdir}
"""
    return base.rstrip() + "\n" + tail


def command_image_start(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    eval_id = _eval_id(options.eval_id)
    work = options.work_dir.resolve()
    context = work / "context"
    shutil.rmtree(context, ignore_errors=True)
    context.mkdir(parents=True)
    runner = options.runner_dir.resolve()
    archon = options.archon_dir.resolve()
    archon_commit = _git("-C", str(archon), "rev-parse", options.archon_commit).strip()
    runner_commit = _git("-C", str(runner), "rev-parse", options.runner_commit).strip()
    # `git archive` reads only the committed tree, so a dirty clone cannot leak in.
    archive = subprocess.run(
        ["git", "-C", str(runner), "archive", runner_commit],
        capture_output=True,
        check=False,
    )
    if archive.returncode:
        raise CliError("git archive of the Runner commit failed")
    extract = subprocess.run(
        ["tar", "-C", str(context), "-xf", "-"], input=archive.stdout, check=False
    )
    if extract.returncode:
        raise CliError("the Runner context could not be unpacked")
    # A shallow clone keeps `/opt/archon` a real checkout at exactly the frozen commit.
    source = context / "archon"
    _git("init", "-q", str(source))
    _git("-C", str(source), "fetch", "-q", "--depth", "1", f"file://{archon}", archon_commit)
    _git("-C", str(source), "checkout", "-q", "FETCH_HEAD")
    shutil.copy(context / "scripts/sandbox-sync-home.sh", context / "sandbox-sync-home.sh")
    base = (context / "docker/dev/Dockerfile").read_text(encoding="utf-8")
    dockerfile = context / "Dockerfile.archon"
    dockerfile.write_text(
        archon_dockerfile(
            base, _archon_bun_version(archon), archon_commit, options.openspec_version
        ),
        encoding="utf-8",
    )
    tag = f"{IMAGE_TAG_PREFIX}{eval_id}"
    repository = f"registry.fly.io/{fly.app}"
    config = work / "fly.toml"
    config.write_text(f'app = "{fly.app}"\nprimary_region = "{fly.region}"\n', encoding="utf-8")
    exit_file = work / "build.exit"
    exit_file.unlink(missing_ok=True)
    log = work / "build.log"
    command = [
        "flyctl", "deploy", "--build-only", "--push", "--remote-only",
        "-c", str(config), "-a", fly.app,
        "--dockerfile", str(dockerfile), "--image-label", tag,
        "--build-arg", f"FACTORY_CLI_REFRESH={eval_id}", ".",
    ]  # fmt: skip
    environment = FlyTransport(fly.app, None, fly.token_file).deploy_environment()
    wrapper = (
        f"{shlex.join(command)} >{shlex.quote(str(log))} 2>&1; "
        f"echo $? >{shlex.quote(str(exit_file))}"
    )
    process = subprocess.Popen(
        ["sh", "-c", wrapper],
        cwd=context,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    _write(
        work / "image-build.json",
        {
            "repository": repository,
            "tag": tag,
            "pid": process.pid,
            "archon_commit": archon_commit,
            "runner_commit": runner_commit,
        },
    )
    _emit({"state": "building", "tag": tag, "pid": process.pid})
    return 0


def command_image_status(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    work = options.work_dir.resolve()
    built = _read(work / "image-build.json")
    done = _read(work / "image.json")
    if done:
        _emit({"state": "built", **done})
        return 0
    exit_file = work / "build.exit"
    if not exit_file.is_file():
        _emit({"state": "building"})
        return 0
    code = exit_file.read_text(encoding="utf-8").strip()
    log = (work / "build.log").read_bytes()[-20000:]
    if code != "0":
        _emit({"state": "failed", "log_tail": log[-2000:].decode(errors="replace")})
        return 1
    repository, tag = str(built.get("repository")), str(built.get("tag"))
    image = f"{repository}:{tag}".encode()
    pushed = list(
        re.finditer(
            rb"(?m)^(?:#\d+[ \t]+)?pushing manifest for[ \t]+"
            + re.escape(image)
            + rb"@(sha256:[0-9a-fA-F]{64})\b",
            log,
        )
    )
    digest = pushed[-1].group(1).decode() if pushed else ""
    if not digest:
        digest = _client(fly).resolve_manifest(image.decode())
    if not DIGEST_PATTERN.fullmatch(digest):
        _emit({"state": "failed", "log_tail": "the build returned no digest"})
        return 1
    result: dict[str, object] = {
        "repository": repository,
        "tag": tag,
        "digest": digest,
        "archon_commit": built.get("archon_commit"),
        "runner_commit": built.get("runner_commit"),
    }
    _write(work / "image.json", result)
    _emit({"state": "built", **result})
    return 0


# -- one repetition ------------------------------------------------------------


def _paths(run_dir: Path) -> tuple[Path, Path]:
    artifact = run_dir.resolve()
    return artifact, artifact / ".factory"


def _phase(factory: Path) -> str:
    value = _read(factory / "eval-state.json").get("phase")
    return value if isinstance(value, str) else "none"


def _set_phase(factory: Path, phase: str, **extra: object) -> None:
    state = _read(factory / "eval-state.json")
    state.update(extra)
    state["phase"] = phase
    state["updated_at"] = int(time.time())
    _write(factory / "eval-state.json", state)


def _manifest(factory: Path, fly: FlyLocalConfig) -> dict[str, object]:
    manifest = _read(factory / "manifest.json")
    if not manifest:
        raise CliError("manifest.json is missing from the run directory")
    manifest["fly"] = {
        "app": fly.app,
        "token_file": str(fly.token_file),
        "region": fly.region,
        "cpu_kind": fly.cpu_kind,
        "cpus": fly.cpus,
        "memory_mb": fly.memory_mb,
        "heartbeat_seconds": fly.heartbeat_seconds,
    }
    manifest.setdefault("owner", OWNER)
    return manifest


def _lifecycle(factory: Path, fly: FlyLocalConfig) -> Lifecycle:
    return Lifecycle(_manifest(factory, fly), factory)


def _strings(value: object) -> list[str]:
    return (
        [item for item in cast(list[object], value) if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def command_start(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    artifact, factory = _paths(options.run_dir)
    if _phase(factory) not in {"none", "created"}:
        raise CliError(f"the repetition is already {_phase(factory)}")
    manifest = _manifest(factory, fly)
    image = _read(options.image_file)
    digest = image.get("digest")
    if not isinstance(digest, str) or not DIGEST_PATTERN.fullmatch(digest):
        raise CliError("the image file has no digest")
    manifest["image_repository"] = image.get("repository")
    manifest["image_digest"] = digest
    _write(factory / "manifest.json", manifest)
    launch = _read(factory / "parsed-launch.json")
    if not launch or launch.get("dry_run") is not True:
        raise CliError("parsed-launch.json from a dry run is missing")
    values = launch.get("env_values")
    if isinstance(values, dict):
        for name, value in cast(dict[str, object], values).items():
            if isinstance(value, str):
                os.environ[name] = value
    script = launch.get("script")
    input_dir = launch.get("input_dir")
    if not isinstance(script, str) or not isinstance(input_dir, str):
        raise CliError("parsed-launch.json is incomplete")
    deliveries = launch.get("deliveries")
    pairs = (
        tuple((Path(str(item[0])), str(item[1])) for item in cast(list[list[object]], deliveries))
        if isinstance(deliveries, list)
        else ()
    )
    request = JobRequest(
        artifact_dir=artifact,
        script=job_script(manifest, script),
        input_dir=Path(input_dir),
        environment=environment_text(
            [Path(p) for p in _strings(launch.get("env_files"))],
            _strings(launch.get("env_names")),
        ),
        codex_auth=launch.get("mount_codex_auth") is True,
        claude_auth=launch.get("mount_claude_auth") is True,
        deliveries=pairs,
    )
    _set_phase(factory, "created")
    code = Lifecycle(manifest, factory).start(request)
    record = _read(factory / "machine.json")
    if code != 0:
        _emit({"ok": False, "exit_code": code, "machine": record.get("id")})
        return 1
    _set_phase(factory, "delivered", machine=record.get("id"), job=record.get("job"))
    _emit({"ok": True, "machine": record.get("id"), "job": record.get("job")})
    return 0


def command_poll(options: argparse.Namespace) -> int:
    """Always exits 0; the state is in the output."""
    _local, fly = _fly(options.config)
    _artifact, factory = _paths(options.run_dir)
    state = _read(factory / "eval-state.json")
    phase = state.get("phase")
    if phase in {"collected", "destroyed"}:
        _emit({"state": "done", "phase": phase})
        return 0
    job = state.get("job")
    if phase != "delivered" or not isinstance(job, int):
        _emit({"state": "lost", "reason": f"no delivered job (phase {phase})"})
        return 0
    observed = _lifecycle(factory, fly).observe(job)
    poll = _read(factory / "poll-state.json")
    failures = poll.get("failures")
    count = failures if isinstance(failures, int) else 0
    result = observed.state
    if result == "unreachable":
        count += 1
        if count >= LOST_AFTER_FAILURES:
            result = "lost"
    else:
        count = 0
    _write(
        factory / "poll-state.json", {"failures": count, "state": result, "at": int(time.time())}
    )
    _emit(
        {
            "state": result,
            "failures": count,
            "setup_complete": observed.setup_complete,
            "checkpoint": observed.checkpoint,
            "latest": observed.latest,
        }
    )
    return 0


def command_collect(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    artifact, factory = _paths(options.run_dir)
    state = _read(factory / "eval-state.json")
    job = state.get("job")
    if state.get("phase") != "delivered" or not isinstance(job, int):
        raise CliError("collect needs a delivered repetition")
    lifecycle = _lifecycle(factory, fly)
    if lifecycle.observe(job).state != "done":
        raise CliError("the job is not done")
    (artifact / "guest-exit-code").unlink(missing_ok=True)
    code = lifecycle.collect(artifact, job)
    marker = artifact / "guest-exit-code"
    if not marker.is_file():
        _emit({"ok": False, "exit_code": code})
        return 1
    _set_phase(factory, "collected", guest_exit_code=code)
    _emit({"ok": True, "guest_exit_code": code})
    return 0


def command_destroy(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    _artifact, factory = _paths(options.run_dir)
    phase = _phase(factory)
    if phase != "collected" and not options.abandon:
        raise CliError(f"destroy needs a collected repetition (phase {phase}); use --abandon")
    try:
        outcome = _lifecycle(factory, fly).destroy_machine()
    except OwnershipMismatchError as error:
        _emit({"ok": False, "reason": f"ownership mismatch for {error}"})
        return 1
    _set_phase(factory, "destroyed", abandoned=bool(options.abandon))
    _emit({"ok": True, "machine": outcome})
    return 0


def command_teardown(options: argparse.Namespace) -> int:
    _local, fly = _fly(options.config)
    eval_id = _eval_id(options.eval_id)
    client = _client(fly)
    found: list[dict[str, object]] = []
    for machine in client.list_machines("factory-owner", options.owner):
        config = machine.get("config")
        metadata = (
            cast(Mapping[str, object], config).get("metadata")
            if isinstance(config, Mapping)
            else None
        )
        unit = (
            cast(Mapping[str, object], metadata).get("unit_key")
            if isinstance(metadata, Mapping)
            else None
        )
        identifier = machine.get("id")
        if isinstance(unit, str) and unit.startswith(eval_id) and isinstance(identifier, str):
            kept = unit in options.keep
            found.append({"id": identifier, "unit_key": unit, "kept": kept})
            if options.destroy and not kept:
                client.destroy(identifier)
    image: dict[str, object] = {"deleted": False}
    if options.delete_image:
        repository = f"registry.fly.io/{fly.app}"
        tag = f"{IMAGE_TAG_PREFIX}{eval_id}"
        try:
            digest = client.resolve_manifest(f"{repository}:{tag}")
        except FlyApiError as error:
            image = {"deleted": False, "reason": str(error)}
        else:
            image = {"deleted": client.delete_manifest(image_repository(repository), digest)}
    _emit({"machines": found, "destroyed": bool(options.destroy), "image": image})
    return 0


# -- parsing ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-factory-fly-eval")
    parser.add_argument(
        "--version", action="version", version=f"agent-factory-fly-eval {_version()}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: object, *, run_dir: bool = False) -> argparse.ArgumentParser:
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, required=True)
        if run_dir:
            sub.add_argument("--run-dir", type=Path, required=True)
        sub.set_defaults(handler=handler)
        return sub

    add("env-file", command_env_file)
    add("settings", command_settings)
    preflight = add("preflight", command_preflight)
    preflight.add_argument("--owner", default=OWNER)
    preflight.add_argument("--env-file", type=Path)
    preflight.add_argument("--claude-auth", action="store_true")
    preflight.add_argument("--codex-auth", action="store_true")
    image = commands.add_parser("image")
    steps = image.add_subparsers(dest="step", required=True)
    start = steps.add_parser("start")
    status = steps.add_parser("status")
    for sub, handler in ((start, command_image_start), (status, command_image_status)):
        sub.add_argument("--config", type=Path, required=True)
        sub.add_argument("--work-dir", type=Path, required=True)
        sub.set_defaults(handler=handler)
    start.add_argument("--eval-id", required=True)
    start.add_argument("--archon-dir", type=Path, required=True)
    start.add_argument("--archon-commit", required=True)
    start.add_argument("--runner-dir", type=Path, required=True)
    start.add_argument("--runner-commit", required=True)
    start.add_argument("--openspec-version", default="1.17.0")
    launch = add("start", command_start, run_dir=True)
    launch.add_argument("--image-file", type=Path, required=True)
    add("poll", command_poll, run_dir=True)
    add("collect", command_collect, run_dir=True)
    destroy = add("destroy", command_destroy, run_dir=True)
    destroy.add_argument("--abandon", action="store_true")
    teardown = add("teardown", command_teardown)
    teardown.add_argument("--eval-id", required=True)
    teardown.add_argument("--owner", default=OWNER)
    teardown.add_argument("--destroy", action="store_true")
    teardown.add_argument("--keep", action="append", default=[])
    teardown.add_argument("--delete-image", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return int(options.handler(options))
    except (
        CliError,
        ConfigurationError,
        FlyApiError,
        FlyTransportError,
        OSError,
        ValueError,
    ) as error:
        _emit({"ok": False, "error": str(error)[-1000:]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
