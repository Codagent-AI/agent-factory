from __future__ import annotations

import json
from pathlib import Path


def test_launcher_dry_run_accepts_harness_shape_without_contacting_fly(tmp_path: Path) -> None:
    from agent_factory.fly.launcher import main

    artifact = tmp_path / "artifact"
    factory = artifact / ".factory"
    factory.mkdir(parents=True)
    manifest = factory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact_dir": str(artifact),
                "worktrees": {
                    "runner": str(tmp_path / "runner"),
                    "skills": str(tmp_path / "skills"),
                },
            }
        )
    )
    result = main(
        [
            "--dry-run",
            "--artifact-dir",
            str(artifact),
            "--input-dir",
            str(tmp_path / "input"),
            "--dev-audit",
            "--docker-run-arg",
            "--security-opt",
            "--docker-run-arg",
            "seccomp=unconfined",
            "--env",
            "NAME",
            "--env-file",
            str(tmp_path / "env"),
            "--",
            "echo ok",
        ],
        manifest_path=manifest,
    )
    assert result == 0
    assert json.loads((factory / "parsed-launch.json").read_text())["dry_run"] is True


def _manifest_for(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "artifact"
    factory = artifact / ".factory"
    factory.mkdir(parents=True)
    manifest = factory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "artifact_dir": str(artifact),
                "worktrees": {
                    "runner": str(tmp_path / "runner"),
                    "skills": str(tmp_path / "skills"),
                },
            }
        )
    )
    return artifact, manifest


def _harness_argv(artifact: Path, tmp_path: Path, auth: list[str]) -> list[str]:
    return [
        "--dry-run",
        "--artifact-dir",
        str(artifact),
        "--input-dir",
        str(tmp_path / "input"),
        "--dev-audit",
        "--docker-run-arg",
        "--security-opt",
        "--docker-run-arg",
        "seccomp=unconfined",
        "--env",
        "NAME",
        "--env-file",
        str(tmp_path / "env"),
        *auth,
        "--",
        "echo ok",
    ]


def test_launcher_accepts_claude_auth_before_codex_auth(tmp_path: Path) -> None:
    """The harness emits auth flags in role order, so a claude lead comes first."""
    from agent_factory.fly.launcher import main

    artifact, manifest = _manifest_for(tmp_path)
    result = main(
        _harness_argv(artifact, tmp_path, ["--mount-claude-auth", "--mount-codex-auth"]),
        manifest_path=manifest,
    )
    assert result == 0
