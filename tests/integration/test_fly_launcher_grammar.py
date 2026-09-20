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
