"""E2E-004: inspect actual wrapper/launcher mounts using a model-free command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from agent_factory.config import SharedConfig
from agent_factory.suites.and_scene import GitWorktreeManager, SourceRepositories
from agent_factory.supervisor import container_matches_recorded_ownership, stop_owned_container


def _run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]
    return result.stdout.strip()


@pytest.mark.docker
def test_e2e_004_actual_pinned_wrapper_metadata_and_container_identity(tmp_path: Path) -> None:
    root = os.environ.get("AGENT_FACTORY_DOCKER_SOURCES")
    if not root:
        pytest.skip(
            "Required separate Docker check: set AGENT_FACTORY_DOCKER_SOURCES; not passing evidence"
        )
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    source_root = Path(root)
    paths: dict[str, Path] = {}
    pins: dict[str, object] = {"evals": shared.eval.harness_sha}
    for name, repository in (
        ("runner", "agent-runner"),
        ("skills", "agent-skills"),
        ("evals", "agent-evals"),
    ):
        target = tmp_path / ("source-" + name)
        _run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(source_root / repository),
                str(target),
            ]
        )
        paths[name] = target
        if name != "evals":
            pins[name] = _run(["git", "-C", str(target), "rev-parse", "HEAD"])
    manager = GitWorktreeManager(
        tmp_path, SourceRepositories(paths["runner"], paths["skills"], paths["evals"])
    )
    worktrees = manager.prepare("docker-proof", pins)
    tag = "agent-factory-test:" + uuid.uuid4().hex
    other_tag = tag + "-other"
    name = "factory-test-" + uuid.uuid4().hex
    decoy = name + "-decoy"
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    decoy_artifacts = tmp_path / "decoy"
    decoy_artifacts.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".codex").mkdir()
    (home / ".codex/auth.json").write_text("{}")
    environment = {"PATH": os.environ["PATH"], "HOME": str(home), "IMAGE": tag}
    wrapper = worktrees.evals / "evals/agent-runner/and-scene/run.sh"
    arguments = [
        str(wrapper),
        "--run-agent",
        "--agent-runner-dir",
        str(worktrees.runner),
        "--agent-skills-dir",
        str(worktrees.skills),
        "--artifact-dir",
        str(artifacts),
    ]
    for role in ("lead", "implementor", "reviewer"):
        arguments += [
            f"--{role}-cli",
            "codex",
            f"--{role}-model",
            "test",
            f"--{role}-effort",
            "high",
        ]
    container: str | None = None
    try:
        # Capture exact argv from the real wrappers, then execute their Docker
        # arguments with only the expensive model/bootstrap command replaced.
        capture = tmp_path / "docker-argv.jsonl"
        capture_bin = tmp_path / "capture-bin"
        capture_bin.mkdir()
        shim = capture_bin / "docker"
        shim.write_text(
            f"#!{sys.executable}\nimport json,sys\n"
            f"with open({str(capture)!r}, 'a') as f: "
            "f.write(json.dumps(['docker', *sys.argv[1:]])+'\\n')\n"
        )
        shim.chmod(0o755)
        _run(arguments, env={**environment, "PATH": f"{capture_bin}:{environment['PATH']}"})
        build, generated = [json.loads(line) for line in capture.read_text().splitlines()]
        _run(build, env=environment)
        image_index = generated.index(tag)
        # All source/auth/env/mount arguments are from the delivered wrappers.
        # Only the expensive bootstrap/model command is replaced by controlled Git checks.
        source_dirs = ["/agent-runner-source", "/agent-skills-source"]
        proof = " && ".join(
            f'git -C {path} rev-parse HEAD && test -z "$(git -C {path} status --porcelain)"'
            for path in source_dirs
        )
        prefix = generated[:image_index]
        _run(
            prefix + ["--name", name, "-d", tag, "sh", "-c", proof + " && sleep 120"],
            env=environment,
        )
        observed = json.loads(_run(["docker", "inspect", name]))[0]
        container = observed["Id"]
        recorded = {
            "id": container,
            "image": observed["Image"],
            "artifact_path": str(artifacts.resolve()),
        }
        assert container_matches_recorded_ownership(recorded, observed)
        _run(["docker", "exec", name, "sh", "-c", proof])
        for path in source_dirs:
            assert (
                _run(["docker", "exec", name, "git", "-C", path, "rev-parse", "HEAD"])
                in pins.values()
            )
        assert all(
            not mount["RW"] for mount in observed["Mounts"] if mount["Destination"] != "/artifacts"
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                decoy,
                "-v",
                f"{decoy_artifacts}:/artifacts",
                tag,
                "sleep",
                "120",
            ]
        )
        decoy_info = json.loads(_run(["docker", "inspect", decoy]))[0]
        assert not container_matches_recorded_ownership(recorded, decoy_info)
        wrong = dict(recorded, artifact_path=str(decoy_artifacts))
        assert not container_matches_recorded_ownership(wrong, observed)
        _run(["docker", "tag", observed["Image"], other_tag])
        # Drop the tag while the immutable image and both running containers survive.
        _run(["docker", "image", "rm", tag])
        assert json.loads(_run(["docker", "inspect", name]))[0]["Image"] == recorded["image"]
        assert container_matches_recorded_ownership(
            recorded, json.loads(_run(["docker", "inspect", name]))[0]
        )
        assert stop_owned_container(recorded)
        assert json.loads(_run(["docker", "inspect", decoy]))[0]["State"]["Running"]
    finally:
        for owned in (name, decoy):
            subprocess.run(["docker", "rm", "-f", owned], capture_output=True, check=False)
        for owned_tag in (tag, other_tag):
            subprocess.run(["docker", "image", "rm", owned_tag], capture_output=True, check=False)
        assert not manager.remove(worktrees)
