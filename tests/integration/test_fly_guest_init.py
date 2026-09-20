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
