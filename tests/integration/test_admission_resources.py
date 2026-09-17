from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agent_factory.operations import check_memory_headroom

_GIB = 1024**3


def _write_docker_stub(path: Path, *, mem_total: int, stats_lines: list[str]) -> Path:
    script = path / "docker"
    stats_output = "\\n".join(stats_lines)
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "info" ]; then\n'
        f'  echo "{mem_total}"\n'
        'elif [ "$1" = "stats" ]; then\n'
        f'  printf "{stats_output}\\n"\n'
        "fi\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_ample_headroom_permits_admission(tmp_path: Path) -> None:
    docker = _write_docker_stub(tmp_path, mem_total=16 * _GIB, stats_lines=["512MiB / 16GiB"])

    result = check_memory_headroom(3, docker=str(docker))

    assert result.available is True


def test_exact_shortfall_blocks_admission(tmp_path: Path) -> None:
    # 16 GiB total, 13 GiB used -> 3 GiB headroom, exactly the reservation: allowed.
    docker = _write_docker_stub(tmp_path, mem_total=16 * _GIB, stats_lines=["13GiB / 16GiB"])
    result = check_memory_headroom(3, docker=str(docker))
    assert result.available is True

    # One more MiB of usage drops headroom just under the reservation: blocked.
    docker = _write_docker_stub(tmp_path, mem_total=16 * _GIB, stats_lines=["13.01GiB / 16GiB"])
    result = check_memory_headroom(3, docker=str(docker))
    assert result.available is False
    assert "memory" in result.detail.lower() or "gib" in result.detail.lower()


def test_running_container_consuming_allowance_blocks_admission(tmp_path: Path) -> None:
    docker = _write_docker_stub(tmp_path, mem_total=8 * _GIB, stats_lines=["7.5GiB / 8GiB"])

    result = check_memory_headroom(3, docker=str(docker))

    assert result.available is False


def test_probe_failure_reports_explanatory_reason(tmp_path: Path) -> None:
    result = check_memory_headroom(3, docker=str(tmp_path / "does-not-exist"))

    assert result.available is False
    assert result.detail


@pytest.mark.parametrize("stats_lines", [[], ["0B / 16GiB"]])
def test_no_running_containers_uses_zero_usage(tmp_path: Path, stats_lines: list[str]) -> None:
    docker = _write_docker_stub(tmp_path, mem_total=16 * _GIB, stats_lines=stats_lines)

    result = check_memory_headroom(3, docker=str(docker))

    assert result.available is True


def test_eval_admission_ignores_failed_fix_diagnostics() -> None:
    """A failed fix-host or fix-sandbox line (the LaunchAgent PATH under host execution)
    must never hold Docker evaluations; only shared and eval-sandbox groups can."""
    from typing import cast

    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.operations import Diagnostic
    from agent_factory.runtime import _kind_failures  # pyright: ignore[reportPrivateUsage]
    from agent_factory.work_kinds.base import WorkKindHandler

    class EvalHandler:
        kind = "eval"

    shared_failure = Diagnostic("shared configuration", False, "bad", "fix", group="shared")
    sandbox_failure = Diagnostic("Docker", False, "down", "start", group="eval-sandbox")
    host_failure = Diagnostic("LaunchAgent PATH", False, "missing", "add", group="fix-host")
    fix_sandbox_failure = Diagnostic("fix mirror", False, "missing", "add", group="fix-sandbox")
    memory = Diagnostic("sandbox memory", True, "ok", "", group="eval-sandbox")

    failures = _kind_failures(
        cast(WorkKindHandler, EvalHandler()),
        cast(LocalConfig, None),
        cast(SharedConfig, None),
        [shared_failure, sandbox_failure, host_failure, fix_sandbox_failure],
        lambda: memory,
    )

    assert failures == [shared_failure, sandbox_failure]
