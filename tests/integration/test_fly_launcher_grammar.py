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


@pytest.mark.parametrize("panel_before_audit", [True, False])
def test_launcher_delivers_panel_in_current_and_legacy_mount_order(
    tmp_path: Path, panel_before_audit: bool
) -> None:
    from agent_factory.fly.launcher import main, parse_arguments

    artifact, manifest_path = _manifest_for(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    evals = tmp_path / "evals"
    panel = evals / "evals/lib/panel-judging"
    panel.mkdir(parents=True)
    manifest["worktrees"]["evals"] = str(evals)
    metadata = tmp_path / "git-metadata"
    manifest["git_common_dirs"] = [str(metadata)]
    manifest_path.write_text(json.dumps(manifest))
    panel_mount = f"type=bind,source={panel},target=/lib/panel-judging,readonly"
    mounts = [
        f"type=bind,source={metadata},target={metadata},readonly",
        f"type=bind,source={tmp_path / 'skills'},target=/agent-skills-source,readonly",
    ]
    argv = _harness_argv(artifact, tmp_path, ["--mount-claude-auth", "--mount-codex-auth"])
    index = argv.index("--env")
    for mount in reversed(mounts):
        argv[index:index] = ["--docker-run-arg", "--mount", "--docker-run-arg", mount]
    index = argv.index("--dev-audit") if panel_before_audit else argv.index("--env")
    argv[index:index] = ["--docker-run-arg", "--mount", "--docker-run-arg", panel_mount]

    assert main(argv, manifest_path=manifest_path) == 0
    parsed = parse_arguments(argv, manifest)
    assert parsed.deliveries == ((panel.resolve(), "/lib/panel-judging"),)
    assert parsed.mount_claude_auth and parsed.mount_codex_auth
    recorded = json.loads((artifact / ".factory/parsed-launch.json").read_text())
    assert recorded["deliveries"] == [[str(panel.resolve()), "/lib/panel-judging"]]
    assert len(recorded["mounts"]) == 3


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/eval-input",
        "//eval-input",
        "//",
        "/eval-input/nested",
        "/artifacts",
        "/agent-runner-source",
        "/agent-skills-source",
        "/host-home",
        "/run",
        "/var/lib/factory",
        "/var/lib",
        "/workspace",
        "/tmp",
        "/usr",
        "/usr/lib",
        "/bin",
        "/lib",
        "/lib/other",
        "/etc",
        "/home",
        "/root",
        "/opt",
        "/var",
        "relative",
        "/lib/../panel",
    ],
)
def test_launcher_rejects_unsafe_harness_delivery_target(tmp_path: Path, target: str) -> None:
    from agent_factory.fly.launcher import ArgumentError, parse_arguments

    artifact, path = _manifest_for(tmp_path)
    manifest = json.loads(path.read_text())
    evals = tmp_path / "evals"
    evals.mkdir()
    manifest["worktrees"]["evals"] = str(evals)
    argv = _harness_argv(artifact, tmp_path, [])
    index = argv.index("--env")
    argv[index:index] = [
        "--docker-run-arg",
        "--mount",
        "--docker-run-arg",
        f"type=bind,source={evals},target={target},readonly",
    ]
    with pytest.raises(ArgumentError):
        parse_arguments(argv, manifest)


@pytest.mark.parametrize(
    "source_kind", ["outside", "parent-escape", "symlink-escape", "file", "missing"]
)
def test_launcher_rejects_unsafe_harness_delivery_source(tmp_path: Path, source_kind: str) -> None:
    from agent_factory.fly.launcher import ArgumentError, parse_arguments

    artifact, path = _manifest_for(tmp_path)
    manifest = json.loads(path.read_text())
    evals = tmp_path / "evals"
    evals.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (evals / "link").symlink_to(outside, target_is_directory=True)
    (evals / "file").write_text("not a directory")
    sources = {
        "outside": outside,
        "parent-escape": evals / ".." / "outside",
        "symlink-escape": evals / "link",
        "file": evals / "file",
        "missing": evals / "missing",
    }
    manifest["worktrees"]["evals"] = str(evals)
    argv = _harness_argv(artifact, tmp_path, [])
    index = argv.index("--env")
    argv[index:index] = [
        "--docker-run-arg",
        "--mount",
        "--docker-run-arg",
        f"type=bind,source={sources[source_kind]},target=/lib/panel-judging,readonly",
    ]
    with pytest.raises(ArgumentError):
        parse_arguments(argv, manifest)


def test_launcher_passes_harness_deliveries_to_machine_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Mapping

    from agent_factory.fly.launcher import main
    from agent_factory.fly.transport import JobRequest, Lifecycle

    artifact, manifest_path = _manifest_for(tmp_path)
    manifest = json.loads(manifest_path.read_text())
    evals = tmp_path / "evals"
    evals.mkdir()
    manifest["worktrees"]["evals"] = str(evals)
    manifest["fly"] = {"app": "test", "token_file": str(tmp_path / "token")}
    manifest_path.write_text(json.dumps(manifest))
    argv = _harness_argv(artifact, tmp_path, [])
    argv.remove("--dry-run")
    env_file = tmp_path / "env"
    env_file.write_text("NAME=value\n")
    index = argv.index("--dev-audit")
    argv[index:index] = [
        "--docker-run-arg",
        "--mount",
        "--docker-run-arg",
        f"type=bind,source={evals},target=/lib/panel-judging,readonly",
    ]
    seen: list[JobRequest] = []

    def job_script(_manifest: Mapping[str, object], script: str) -> str:
        return script

    def run(_self: Lifecycle, request: JobRequest) -> int:
        seen.append(request)
        return 0

    monkeypatch.setattr("agent_factory.fly.guest.job_script", job_script)
    monkeypatch.setattr(Lifecycle, "run", run)

    assert main(argv, manifest_path=manifest_path) == 0
    assert seen[0].deliveries == ((evals.resolve(), "/lib/panel-judging"),)
