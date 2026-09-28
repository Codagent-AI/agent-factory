"""The claim builder uses the frozen Validator source and a derived Dockerfile."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_factory.fly.transport import build_claim_image
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
