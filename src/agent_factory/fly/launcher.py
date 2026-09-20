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
    from agent_factory.fly.transport import FlyTransport

    # Keep diagnostics off stdout: run.sh's parent treats stdout as suite progress.
    with (factory / "launcher.log").open("a", encoding="utf-8") as log:
        log.write("validated Fly launch manifest; lifecycle transport is starting\n")
    transport = FlyTransport.from_manifest(manifest)
    return transport.launch(parsed, manifest, factory)


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
    if parts.get("type") != "bind" or parts.get("readonly") is not None or not source or not target:
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
