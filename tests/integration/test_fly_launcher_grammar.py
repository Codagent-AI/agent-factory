from __future__ import annotations

import json
from pathlib import Path

import pytest


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


def test_env_file_values_reach_the_guest_as_literal_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from agent_factory.fly.transport import environment_text

    env_file = tmp_path / "candidate.env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "SPACED=a b\n"
        "SUBST=$(touch pwned)\n"
        "QUOTED='unmatched\n"
        "export EXPORTED=kept\n"
        "TRAILING=value  \n"
        "PASSED\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PASSED", "from host")

    text = environment_text([env_file], [])
    script = tmp_path / "env"
    script.write_text(text, encoding="utf-8")
    shown = subprocess.run(
        [
            "bash",
            "-c",
            'set -a; . "$1"; set +a; '
            'printf "%s|" "$SPACED" "$SUBST" "$QUOTED" "$EXPORTED" "$PASSED" "$TRAILING"',
            "_",
            str(script),
        ],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
        check=True,
    )

    assert shown.stdout == "a b|$(touch pwned)|'unmatched|kept|from host|value  |"
    assert not (tmp_path / "pwned").exists()
