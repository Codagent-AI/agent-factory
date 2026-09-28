"""The claim builder uses the frozen Validator source and a derived Dockerfile."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_factory.fly.transport import FlyTransportError, Lifecycle, build_claim_image
from tests.fixtures.fly.flyctl import write_guest_flyctl


def test_pinned_validator_build_uses_frozen_source(tmp_path: Path) -> None:
    runner = tmp_path / "runner"
    dockerfile = runner / "docker/dev/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    original = 'FROM alpine\nUSER pwuser\nWORKDIR /workspace\nCMD ["sh"]\n'
    dockerfile.write_text(original)
    factory = tmp_path / "artifact" / ".factory"
    factory.mkdir(parents=True)
    log = tmp_path / "flyctl.jsonl"
    bin_dir = tmp_path / "bin"
    write_guest_flyctl(bin_dir, tmp_path / "guest", log, factory / "machine.json")
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    sha = "a" * 40
    source = "https://github.com/Codagent-AI/agent-validator.git"

    image = build_claim_image(
        "test-app",
        "registry.fly.io/test-app",
        "claim-1234567890",
        runner,
        factory,
        env,
        validator=(sha, source),
    )

    assert image == "registry.fly.io/test-app@sha256:" + "0" * 64
    records = [json.loads(line) for line in log.read_text().splitlines()]
    argv = records[0]["argv"]
    args = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--build-arg"]
    assert "FACTORY_CLI_REFRESH=claim-1234567890" in args
    assert f"AGENT_VALIDATOR_REVISION={sha}" in args
    assert f"AGENT_VALIDATOR_REPOSITORY={source}" in args
    derived = factory / "claim.Dockerfile"
    assert argv[argv.index("--dockerfile") + 1] == str(derived.resolve())
    content = derived.read_text()
    assert content.startswith(original)
    assert "git fetch -q --depth 1" in content
    assert "bun@" in content
    assert content.rstrip().endswith("USER pwuser\nWORKDIR /workspace")
    assert records[1]["dockerfile"] == content
    assert dockerfile.read_text() == original


def test_legacy_claim_uses_runner_dockerfile(tmp_path: Path) -> None:
    runner = tmp_path / "runner"
    runner.mkdir()
    factory = tmp_path / ".factory"
    factory.mkdir()
    log = tmp_path / "flyctl.jsonl"
    bin_dir = tmp_path / "bin"
    write_guest_flyctl(bin_dir, tmp_path / "guest", log, factory / "machine.json")
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}

    build_claim_image("test-app", "registry.fly.io/test-app", "claim-legacy", runner, factory, env)

    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert argv[argv.index("--dockerfile") + 1] == "docker/dev/Dockerfile"
    assert "AGENT_VALIDATOR_REVISION=" not in " ".join(argv)
    assert not (factory / "claim.Dockerfile").exists()


def test_pinned_multistage_build_restores_only_final_stage_settings(tmp_path: Path) -> None:
    runner = tmp_path / "runner"
    source = runner / "docker/dev/Dockerfile"
    source.parent.mkdir(parents=True)
    source.write_text(
        "FROM alpine AS base\nUSER early\nWORKDIR /early\n"
        'FROM base AS final\nUSER final\nWORKDIR /final\nCMD ["sh"]\n'
    )
    factory = tmp_path / ".factory"
    factory.mkdir()
    bin_dir = tmp_path / "bin"
    write_guest_flyctl(
        bin_dir, tmp_path / "guest", tmp_path / "flyctl.log", factory / "machine.json"
    )
    build_claim_image(
        "app",
        "registry.fly.io/app",
        "claim",
        runner,
        factory,
        {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        validator=("a" * 40, "https://github.com/a/b.git"),
    )
    assert (
        (factory / "claim.Dockerfile").read_text().rstrip().endswith("USER final\nWORKDIR /final")
    )


def test_lifecycle_uses_frozen_https_source_and_failure_records_no_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = tmp_path / "runner"
    dockerfile = runner / "docker/dev/Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM alpine\nUSER pwuser\nWORKDIR /workspace\n")
    checkout = tmp_path / "validator"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "remote",
            "add",
            "origin",
            "git@github.com:Codagent-AI/agent-validator.git",
        ],
        check=True,
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(checkout), "remote", "get-url", "origin"], text=True
        )
        .strip()
        .startswith("git@")
    )
    factory = tmp_path / ".factory"
    factory.mkdir()
    bin_dir = tmp_path / "bin"
    log = tmp_path / "flyctl.jsonl"
    write_guest_flyctl(bin_dir, tmp_path / "guest", log, factory / "machine.json")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    token = tmp_path / "token"
    token.write_text("test-token")
    source = "https://github.com/Codagent-AI/agent-validator.git"
    sha = "a" * 40
    manifest = {
        "fly": {"app": "test-app", "token_file": str(token), "region": "ewr"},
        "claim_id": "claim-frozen",
        "image_repository": "registry.fly.io/test-app",
        "worktrees": {"runner": str(runner), "validator": str(checkout)},
        "commits": {"validator": sha},
        "validator_repository": source,
    }
    lifecycle = Lifecycle(manifest, factory)
    monkeypatch.setenv("FAKE_FLY_BUILD_FAIL", "1")
    with pytest.raises(FlyTransportError, match="agent-validator version mismatch"):
        lifecycle._machine_image()  # pyright: ignore[reportPrivateUsage]
    assert not (factory / "image-build.json").exists()
    monkeypatch.delenv("FAKE_FLY_BUILD_FAIL")
    image = lifecycle._machine_image()  # pyright: ignore[reportPrivateUsage]
    assert image.endswith("@sha256:" + "0" * 64)
    assert json.loads((factory / "image-build.json").read_text())["digest"] == "sha256:" + "0" * 64
    argv = json.loads(log.read_text().splitlines()[0])["argv"]
    assert f"AGENT_VALIDATOR_REPOSITORY={source}" in argv
    assert "git@github.com" not in " ".join(argv)


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM first\nUSER early\nWORKDIR /early\nFROM final\nWORKDIR /final\n",
        "FROM first\nUSER early\nWORKDIR /early\nFROM final\nUSER final\n",
        "FROM first\nUSER early\nWORKDIR /early\nFROM final\nUSER final\nWORKDIR app\n",
    ],
)
def test_pinned_build_rejects_missing_final_settings_or_relative_workdir(
    tmp_path: Path, dockerfile: str
) -> None:
    runner = tmp_path / "runner"
    source = runner / "docker/dev/Dockerfile"
    source.parent.mkdir(parents=True)
    source.write_text(dockerfile)
    factory = tmp_path / ".factory"
    factory.mkdir()
    with pytest.raises(FlyTransportError, match="final USER or WORKDIR|relative final WORKDIR"):
        build_claim_image(
            "app",
            "registry.fly.io/app",
            "claim",
            runner,
            factory,
            os.environ,
            validator=("a" * 40, "https://github.com/a/b.git"),
        )
    assert not (factory / "image-build.json").exists()
