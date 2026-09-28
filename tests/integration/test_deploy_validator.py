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
    enabled: bool = True,
    path: str | None = None,
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
build_validator={str(enabled).lower()}
if [[ $build_validator == true ]]; then
  validator_checkout
  {"validator_preflight" if preflight else ":"}
fi
if [[ $build_validator == true ]]; then validator_build "$4"; fi
'''
    env = {**os.environ, "PATH": path or f"{bin_dir}:{os.environ['PATH']}"}
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


def test_missing_bun_fails_before_update(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    new = _commit(upstream, "next")
    _git(upstream, "push", "-q", "origin", "main")
    old = _git(checkout, "rev-parse", "HEAD")
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = _step(
        checkout,
        config,
        tmp_path / "runner",
        bin_dir,
        "host attempts: 0",
        path=f"{bin_dir}:/usr/bin:/bin",
    )
    assert result.returncode == 1
    assert "bun" in result.stderr and "--no-validator" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == old
    assert _git(checkout, "rev-parse", "origin/main") == new


def test_unknown_host_status_skips_update_and_build(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    _commit(upstream, "next")
    _git(upstream, "push", "-q", "origin", "main")
    old = _git(checkout, "rev-parse", "HEAD")
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bun_log = tmp_path / "bun.log"
    bun = bin_dir / "bun"
    bun.write_text(f'#!/bin/sh\necho called >> "{bun_log}"\n')
    bun.chmod(0o755)
    result = _step(checkout, config, tmp_path / "runner", bin_dir, "eval slots: 0")
    assert result.returncode == 0
    assert "skipping" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == old
    assert not bun_log.exists()


def test_failed_build_restores_checkout_and_dist(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    _commit(upstream, "next")
    _git(upstream, "push", "-q", "origin", "main")
    old = _git(checkout, "rev-parse", "HEAD")
    dist = checkout / "dist"
    dist.mkdir()
    (dist / "index.js").write_text("working build")
    (checkout / ".git/info/exclude").write_text("dist/\n")
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bun = bin_dir / "bun"
    bun.write_text("#!/bin/sh\necho broken > dist/index.js\nexit 7\n")
    bun.chmod(0o755)
    result = _step(checkout, config, tmp_path / "runner", bin_dir, "host attempts: 0")
    assert result.returncode == 1
    assert "must be rebuilt before resuming" in result.stderr
    assert _git(checkout, "rev-parse", "HEAD") == old
    assert (dist / "index.js").read_text() == "working build"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_failed_build_reports_rebuild_when_dist_cannot_be_removed(tmp_path: Path) -> None:
    _origin, checkout, upstream = _repos(tmp_path)
    _commit(upstream, "next")
    _git(upstream, "push", "-q", "origin", "main")
    dist = checkout / "dist"
    dist.mkdir()
    (dist / "index.js").write_text("working build")
    (checkout / ".git/info/exclude").write_text("dist/\n")
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bun = bin_dir / "bun"
    # The failed build leaves the checkout unwritable, so removing its dist fails.
    bun.write_text("#!/bin/sh\nchmod 555 .\nexit 7\n")
    bun.chmod(0o755)
    try:
        result = _step(checkout, config, tmp_path / "runner", bin_dir, "host attempts: 0")
    finally:
        checkout.chmod(0o755)
    assert result.returncode == 1
    assert "backup restore failed" in result.stderr
    assert "must be rebuilt before resuming" in result.stderr


def test_wrong_plist_path_warns_without_relinking(tmp_path: Path) -> None:
    _origin, checkout, _upstream = _repos(tmp_path)
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{checkout}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    other = tmp_path / "npm-validator"
    other.write_text("other")
    link = bin_dir / "agent-validator"
    link.symlink_to(other)
    plutil = bin_dir / "plutil"
    plutil.write_text(f'#!/bin/sh\necho "{bin_dir}"\n')
    plutil.chmod(0o755)
    bun = bin_dir / "bun"
    bun.write_text('#!/bin/sh\nif [ "$1" = run ]; then mkdir -p dist; touch dist/index.js; fi\n')
    bun.chmod(0o755)
    result = _step(checkout, config, tmp_path / "runner", bin_dir, "host attempts: 0")
    assert result.returncode == 0, result.stderr
    assert str(other) in result.stderr
    assert str(checkout / "dist/index.js") in result.stderr
    assert link.is_symlink() and link.resolve() == other


def test_no_validator_skips_all_functions(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    config = tmp_path / "config.toml"
    config.write_text(f'[repositories]\nagent_validator = "{missing}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = _step(missing, config, tmp_path / "runner", bin_dir, "host attempts: 0", enabled=False)
    assert result.returncode == 0, result.stderr
    assert not result.stderr and not result.stdout
