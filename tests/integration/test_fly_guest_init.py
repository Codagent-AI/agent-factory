from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def test_guest_init_runs_queued_jobs_and_records_artifact_manifest(tmp_path: Path) -> None:
    from agent_factory.fly.guest import guest_init_script

    root = tmp_path / "guest"
    artifacts = root / "artifacts"
    job = artifacts / ".factory/job/1"
    job.mkdir(parents=True)
    (job / "job.sh").write_text(
        '#!/bin/bash\necho hello > "$FACTORY_ROOT/artifacts/value"\nexit 7\n'
    )
    (job / "job.sh").chmod(0o700)
    deadline = root / "var/lib/factory/deadline"
    deadline.parent.mkdir(parents=True)
    deadline.write_text(str(int(time.time()) + 10))
    environment = {
        **os.environ,
        "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 10),
        "FACTORY_ROOT": str(root),
        "FACTORY_WATCHDOG_SECONDS": "1",
    }
    process = subprocess.Popen(["bash", "-c", guest_init_script()], env=environment)
    try:
        (job / "start").touch()
        done = artifacts / ".factory/job/1/DONE"
        for _ in range(50):
            if done.exists():
                break
            time.sleep(0.05)
        assert done.exists()
        assert (job / "exit-code").read_text().strip() == "7"
        assert "value" in (job / "files.txt").read_text()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_job_cleanup_empties_what_the_job_user_owns_without_unlinking_it(tmp_path: Path) -> None:
    """The credential directories and env file sit in root-owned parents.

    The job's user owns them but cannot unlink them, so the trap deletes the
    directories' contents and truncates the env file. Nothing secret may remain
    when the job ends, and the cleanup must not print errors into the job output.
    """
    import subprocess

    from agent_factory.fly.guest import job_script, stand_in_script

    manifest: dict[str, object] = {
        "repositories": {"runner": "https://example.test/r", "skills": "https://example.test/s"},
        "commits": {"runner": "a" * 40, "skills": "b" * 40},
    }
    for script in (stand_in_script("true"), job_script(manifest, "true")):
        trap = next(line for line in script.splitlines() if line.startswith("trap "))
        assert "rm -rf /host-home" not in trap and "rm -rf /run/factory/env" not in trap

    root = tmp_path / "guest"
    for name in ("host-home/codex", "host-home/claude/nested", "run/factory"):
        (root / name).mkdir(parents=True)
    (root / "host-home/codex/auth.json").write_text("secret")
    (root / "host-home/claude/.credentials.json").write_text("secret")
    (root / "host-home/claude/nested/settings.json").write_text("secret")
    (root / "run/factory/env").write_text("TOKEN=secret\n")
    # Parents the job's user cannot modify, as in a real Machine.
    for parent in (root / "host-home", root / "run/factory"):
        parent.chmod(0o555)
    try:
        script = stand_in_script("echo ran")
        for path in ("/host-home", "/run/factory"):
            script = script.replace(path, f"{root}{path}")
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    finally:
        for parent in (root / "host-home", root / "run/factory"):
            parent.chmod(0o755)
    assert done.stdout == "ran\n" and done.stderr == ""
    assert [p for p in (root / "host-home").rglob("*") if p.is_file()] == []
    assert (root / "run/factory/env").read_text() == ""


def test_restarted_guest_with_a_fresh_deadline_ignores_an_old_expiry_marker(
    tmp_path: Path,
) -> None:
    """Without auto-destroy a Machine that hit its deadline only stops.

    Its disk persists, including the expiry marker. When the factory restarts it
    with a later deadline for a recovery attempt, the guest must serve jobs again
    rather than exit at once on the stale marker.
    """
    from agent_factory.fly.guest import guest_init_script

    root = tmp_path / "guest"
    job = root / "artifacts/.factory/job/1"
    job.mkdir(parents=True)
    (job / "job.sh").write_text("#!/bin/bash\nexit 0\n")
    (job / "job.sh").chmod(0o700)
    (job / "start").touch()
    marker = root / "artifacts/.factory/deadline-expired"
    marker.touch()
    environment = {
        **os.environ,
        "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 60),
        "FACTORY_ROOT": str(root),
        "FACTORY_WATCHDOG_SECONDS": "1",
    }
    process = subprocess.Popen(["bash", "-c", guest_init_script()], env=environment)
    try:
        for _ in range(60):
            if (job / "DONE").exists():
                break
            time.sleep(0.05)
        assert (job / "DONE").exists()
        assert not marker.exists()
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_guest_with_an_expired_deadline_still_exits_at_once(tmp_path: Path) -> None:
    from agent_factory.fly.guest import guest_init_script

    root = tmp_path / "guest"
    (root / "artifacts/.factory").mkdir(parents=True)
    (root / "artifacts/.factory/deadline-expired").touch()
    environment = {
        **os.environ,
        "FACTORY_DEADLINE_EPOCH": str(int(time.time()) - 5),
        "FACTORY_ROOT": str(root),
        "FACTORY_WATCHDOG_SECONDS": "1",
    }
    done = subprocess.run(
        ["bash", "-c", guest_init_script()], env=environment, timeout=20, check=False
    )
    assert done.returncode == 1
