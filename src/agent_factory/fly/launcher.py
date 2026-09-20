"""Compatibility launcher used by and-scene's ``SANDBOX_RUNNER`` seam.

Docker-looking arguments are intentionally validation-only: execution details
come exclusively from the non-secret manifest prepared by the factory.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast


class ArgumentError(ValueError):
    pass


@dataclass(frozen=True)
class LaunchArguments:
    artifact_dir: Path
    input_dir: Path
    env_names: tuple[str, ...]
    env_files: tuple[Path, ...]
    mounts: tuple[str, ...]
    mount_codex_auth: bool
    mount_claude_auth: bool
    script: str
    dry_run: bool


def parse_arguments(argv: Sequence[str], manifest: Mapping[str, object]) -> LaunchArguments:
    values = list(argv)
    dry_run = _take(values, "--dry-run")
    artifact = Path(_value(values, "--artifact-dir"))
    input_dir = Path(_value(values, "--input-dir"))
    _required_flag(values, "--dev-audit")
    _required_pair(values, "--docker-run-arg", "--security-opt")
    _required_pair(values, "--docker-run-arg", "seccomp=unconfined")
    mounts: list[str] = []
    while values[:2] == ["--docker-run-arg", "--mount"]:
        del values[:2]
        mount = _value(values, "--docker-run-arg")
        _validate_mount(mount, manifest)
        mounts.append(mount)
    env_names: list[str] = []
    while values[:1] == ["--env"]:
        del values[:1]
        env_names.append(_next(values, "--env"))
    env_files: list[Path] = []
    while values[:1] == ["--env-file"]:
        del values[:1]
        env_files.append(Path(_next(values, "--env-file")))
    codex = _take(values, "--mount-codex-auth")
    claude = _take(values, "--mount-claude-auth")
    if values[:1] == ["--mount-cursor-auth"]:
        raise ArgumentError("--mount-cursor-auth is unsupported on Fly")
    if len(values) != 2 or values[0] != "--":
        raise ArgumentError(values[0] if values else "missing --")
    return LaunchArguments(
        artifact.resolve(),
        input_dir.resolve(),
        tuple(env_names),
        tuple(env_files),
        tuple(mounts),
        codex,
        claude,
        values[1],
        dry_run,
    )


def main(argv: Sequence[str] | None = None, *, manifest_path: Path | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ("attach",):
        return _attach(arguments[1:])
    if arguments[:1] == ("stand-in",):
        return _stand_in(arguments[1:])
    path = manifest_path or Path(os.environ.get("AGENT_FACTORY_FLY_MANIFEST", ""))
    if not str(path):
        print("AGENT_FACTORY_FLY_MANIFEST is required", file=sys.stderr)
        return 2
    try:
        manifest = _manifest(path)
        parsed = parse_arguments(arguments, manifest)
    except (ArgumentError, OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Fly launcher rejected argument: {error}", file=sys.stderr)
        return 2
    factory = parsed.artifact_dir / ".factory"
    factory.mkdir(parents=True, exist_ok=True)
    (factory / "parsed-launch.json").write_text(
        json.dumps({"dry_run": parsed.dry_run, "mounts": parsed.mounts, "script": parsed.script}),
        encoding="utf-8",
    )
    if parsed.dry_run:
        return 0
    # The full lifecycle is intentionally delegated to the transport-backed runner
    # once a valid manifest has crossed this compatibility boundary.
    return _launch(parsed, manifest, factory)


def _launch(parsed: LaunchArguments, manifest: Mapping[str, object], factory: Path) -> int:
    from agent_factory.fly.guest import job_script
    from agent_factory.fly.transport import JobRequest, Lifecycle, environment_text

    try:
        request = JobRequest(
            artifact_dir=parsed.artifact_dir,
            script=job_script(manifest, parsed.script),
            input_dir=parsed.input_dir,
            environment=environment_text(parsed.env_files, parsed.env_names),
            codex_auth=parsed.mount_codex_auth,
            claude_auth=parsed.mount_claude_auth,
        )
    except (OSError, ValueError) as error:
        print(f"Fly launcher could not prepare the job: {error}", file=sys.stderr)
        return 70
    return Lifecycle(manifest, factory).run(request)


def _attach(arguments: Sequence[str]) -> int:
    """Reconnect to the job already running in this attempt's recorded Machine."""
    from agent_factory.fly.transport import Lifecycle

    if len(arguments) != 2 or arguments[0] != "--run-dir":
        print("usage: agent-factory-fly-launcher attach --run-dir DIR", file=sys.stderr)
        return 2
    artifact = Path(arguments[1]).resolve()
    factory = artifact / ".factory"
    try:
        manifest = _manifest(factory / "manifest.json")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Fly launcher cannot read the manifest: {error}", file=sys.stderr)
        return 2
    return Lifecycle(manifest, factory).attach(artifact)


def _stand_in(arguments: Sequence[str]) -> int:
    """Run an operator script through the production Machine path.

    Used for acceptance and diagnosis. It never touches the claim store, and it
    destroys its Machine on exit unless ``--keep`` is given.
    """
    import argparse
    import time

    from agent_factory.config import LocalConfig
    from agent_factory.fly.api import FlyApiError, FlyMachinesClient
    from agent_factory.fly.guest import stand_in_script
    from agent_factory.fly.transport import (
        JobRequest,
        Lifecycle,
        read_record,
    )

    parser = argparse.ArgumentParser(prog="agent-factory-fly-launcher stand-in")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--deadline-seconds", type=int)
    parser.add_argument("--mount-codex-auth", action="store_true")
    parser.add_argument("--mount-claude-auth", action="store_true")
    parser.add_argument("--keep", action="store_true")
    try:
        options = parser.parse_args(list(arguments))
    except SystemExit:
        return 2
    local = LocalConfig.from_file(options.config)
    if local.fly is None:
        print("the configuration has no [fly] settings", file=sys.stderr)
        return 2
    fly = local.fly
    artifact = options.run_dir.resolve()
    factory = artifact / ".factory"
    factory.mkdir(parents=True, exist_ok=True)
    manifest_path = factory / "manifest.json"
    if manifest_path.is_file():
        # A second stand-in against the same directory continues in its Machine.
        manifest = {**_manifest(manifest_path), "run_id": f"stand-in-{int(time.time())}"}
    else:
        manifest = {
            "run_id": f"stand-in-{int(time.time())}",
            "claim_id": "stand-in",
            "unit_key": artifact.name,
            "nonce": os.urandom(16).hex(),
            "image": fly.image,
            "fly": {
                "app": fly.app,
                "token_file": str(fly.token_file),
                "region": fly.region,
                "cpu_kind": fly.cpu_kind,
                "cpus": fly.cpus,
                "memory_mb": fly.memory_mb,
                "heartbeat_seconds": fly.heartbeat_seconds,
            },
        }
    total = options.deadline_seconds
    manifest["deadline"] = (
        {"total_seconds": total, "collection_grace_seconds": 0}
        if total is not None
        else {
            "total_seconds": local.limits.total_seconds,
            "collection_grace_seconds": fly.collection_grace_seconds,
        }
    )
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    request = JobRequest(
        artifact_dir=artifact,
        script=stand_in_script(options.script.read_text(encoding="utf-8")),
        input_dir=None,
        environment="\n",
        codex_auth=options.mount_codex_auth,
        claude_auth=options.mount_claude_auth,
    )
    code = Lifecycle(manifest, factory).run(request)
    if not options.keep:
        machine_id = read_record(factory / "machine.json").get("id")
        if isinstance(machine_id, str):
            try:
                FlyMachinesClient(fly.app, fly.token_file).destroy(machine_id)
            except FlyApiError as error:
                print(f"stand-in Machine {machine_id} was not destroyed: {error}", file=sys.stderr)
    return code


def _manifest(path: Path) -> Mapping[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("manifest must be an object")
    return cast(Mapping[str, object], raw)


def _take(values: list[str], flag: str) -> bool:
    if values[:1] == [flag]:
        del values[:1]
        return True
    return False


def _required_flag(values: list[str], flag: str) -> None:
    if not _take(values, flag):
        raise ArgumentError(flag)


def _value(values: list[str], flag: str) -> str:
    _required_flag(values, flag)
    return _next(values, flag)


def _next(values: list[str], flag: str) -> str:
    if not values:
        raise ArgumentError(flag)
    return values.pop(0)


def _required_pair(values: list[str], first: str, second: str) -> None:
    _required_flag(values, first)
    if not values or values.pop(0) != second:
        raise ArgumentError(second)


def _validate_mount(mount: str, manifest: Mapping[str, object]) -> None:
    fields = mount.split(",")
    if len(fields) != 4 or fields[-1] != "readonly":
        raise ArgumentError(mount)
    if any("=" not in item for item in fields[:3]):
        raise ArgumentError(mount)
    parts = dict(item.split("=", 1) for item in fields[:3])
    if len(parts) != 3 or set(parts) != {"type", "source", "target"}:
        raise ArgumentError(mount)
    source = parts.get("source")
    target = parts.get("target")
    if parts.get("type") != "bind" or not source or not target:
        raise ArgumentError(mount)
    worktrees = manifest.get("worktrees")
    if not isinstance(worktrees, Mapping):
        raise ArgumentError(mount)
    worktree_values = cast(Mapping[str, object], worktrees)
    allowed = [
        Path(value).resolve() for value in worktree_values.values() if isinstance(value, str)
    ]
    common_dirs = manifest.get("git_common_dirs", ())
    if isinstance(common_dirs, list):
        common_values = cast(list[object], common_dirs)
        allowed.extend(Path(value).resolve() for value in common_values if isinstance(value, str))
    candidate = Path(source).resolve()
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise ArgumentError(mount)


if __name__ == "__main__":
    raise SystemExit(main())
