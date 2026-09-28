"""Exercise the deploy's Validator functions without deploying the service."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.integration.test_update_runner import (
    _commit,  # pyright: ignore[reportPrivateUsage]
    _git,  # pyright: ignore[reportPrivateUsage]
    _repos,  # pyright: ignore[reportPrivateUsage]
)

ROOT = Path(__file__).resolve().parents[2]


def _step(
    checkout: Path,
    config: Path,
    runner: Path,
    bin_dir: Path,
    status: str,
    *,
    preflight: bool = True,
) -> subprocess.CompletedProcess[str]:
    script = f'''set -euo pipefail
source "{ROOT / "scripts/slots.sh"}"
source "{ROOT / "scripts/validator.sh"}"
warn() {{ printf 'warning: %s\\n' "$*" >&2; }}
die() {{ printf 'error: %s\\n' "$*" >&2; exit 1; }}
say() {{ printf '%s\\n' "$*"; }}
config=$1
runner=$2
plist=$3
build_validator=true
validator_checkout
{"validator_preflight" if preflight else ":"}
if [[ $build_validator == true ]]; then validator_build "$4"; fi
'''
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", script, "test", str(config), str(runner), str(bin_dir / "plist"), status],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_validator_preflight_and_build_respect_host_attempts(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    new = _commit(upstream, "merged-pr")
    _git(upstream, "push", "-q", "origin", "main")
    old = _git(checkout, "rev-parse", "HEAD")
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bun_log = tmp_path / "bun.log"
    bun = bin_dir / "bun"
    bun.write_text(
        "#!/bin/bash\n"
        f"echo \"$*\" >> '{bun_log}'\n"
        'if [[ "$1" == run ]]; then mkdir -p dist; echo "#!/bin/sh" > dist/index.js; fi\n'
    )
    bun.chmod(0o755)
    (bin_dir / "plutil").write_text(f'#!/bin/sh\necho "{bin_dir}"\n')
    (bin_dir / "plutil").chmod(0o755)
    (bin_dir / "agent-validator").symlink_to(checkout / "dist/index.js")
    (bin_dir / "plist").write_text("fixture")

    busy = _step(checkout, config, tmp_path / "runner", bin_dir, "host attempts: 1")
    assert busy.returncode == 0, busy.stderr
    assert "host attempt" in busy.stderr
    assert _git(checkout, "rev-parse", "HEAD") == old
    assert _git(checkout, "rev-parse", "origin/main") == new
    assert not bun_log.exists()

    free = _step(checkout, config, tmp_path / "runner", bin_dir, "host attempts: 0")
    assert free.returncode == 0, free.stderr
    assert _git(checkout, "rev-parse", "HEAD") == new
    assert bun_log.read_text().splitlines() == ["install --frozen-lockfile", "run build:local"]


@pytest.mark.parametrize("explicit", [False, True])
def test_missing_validator_checkout_is_skipped_only_when_default(
    tmp_path: Path, explicit: bool
) -> None:
    config = tmp_path / "config.toml"
    missing = tmp_path / "agent-validator"
    config.write_text(
        f'[repositories]\nagent_validator = "{missing}"\n' if explicit else "[repositories]\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = _step(missing, config, tmp_path / "runner", bin_dir, "host attempts: 0")
    assert result.returncode == (1 if explicit else 0)
    assert str(missing) in result.stderr
