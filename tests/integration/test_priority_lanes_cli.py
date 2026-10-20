"""INT-004: public lane commands and the resident startup boundary."""

import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from agent_factory import cli
from agent_factory.store import ClaimDraft, ClaimStore


def _cli(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_factory.cli", "--state", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_lane_commands(tmp_path: Path) -> None:
    path = tmp_path / "db"
    result = subprocess.run(
        [sys.executable, "-m", "agent_factory.cli", "lanes", "supported"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and result.stdout == "priority-lanes\n"
    with closing(ClaimStore(path)) as store:
        assert store.lane_mode() == "kind"
        assert _cli(path, "lanes", "downgrade", "--check").returncode == 0
        assert store.lane_mode() == "kind"
        assert _cli(path, "lanes", "enable").returncode == 0
        assert store.lane_mode() == "lanes"
        claims = [
            store.create_claim(ClaimDraft("o/r", i, f"I{i}", f"P{i}", "fix", "fp", {}))
            for i in (1, 2)
        ]
        low = store.reserve_run(
            claims[0].id, "one", lane="low", reason="initial", evidence_path="/e"
        )
        high = store.reserve_run(
            claims[1].id, "one", lane="high", reason="initial", evidence_path="/e"
        )
        for args in [("lanes", "downgrade", "--check"), ("lanes", "downgrade")]:
            result = _cli(path, *args)
            assert result.returncode == 1
            assert f"fix: {claims[0].id} o/r#1 low" in result.stdout
            assert f"fix: {claims[1].id} o/r#2 high" in result.stdout
            assert store.lane_mode() == "lanes"
        store.finish_run(high.id, execution_status="completed", result={})
        assert _cli(path, "lanes", "downgrade", "--check").returncode == 0
        assert store.lane_mode() == "lanes"
        assert _cli(path, "lanes", "downgrade").returncode == 0
        assert store.lane_mode() == "kind"
        assert _cli(path, "status").returncode == 0
        assert _cli(path, "tick").returncode == 0  # reserved, so no watcher reattachment
        assert store.lane_mode() == "kind"
        assert store.get_run(low.id) is not None
        assert _cli(path, "lanes", "downgrade").returncode == 0


def test_resident_enables_before_first_cycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "db"
    ClaimStore(path).close()
    seen: list[str] = []

    def tick(state: Path, config: Path | None) -> None:
        with closing(ClaimStore(state)) as store:
            seen.append(store.lane_mode())

    def stop(seconds: float) -> None:
        raise InterruptedError

    monkeypatch.setattr(cli, "_tick", tick)
    monkeypatch.setattr(cli.time, "sleep", stop)

    def ignore_signal(*args: object) -> None:
        pass

    monkeypatch.setattr(cli.signal, "signal", ignore_signal)
    monkeypatch.setattr(sys, "argv", ["agent-factory", "--state", str(path), "resident"])
    with pytest.raises(InterruptedError):
        cli.main()
    assert seen == ["lanes"]


def test_downgrade_check_does_not_add_column(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "v4"
    with sqlite3.connect(path) as raw:
        raw.executescript(Path("tests/fixtures/priority_lanes_v4.sql").read_text())
    before = path.read_bytes()
    assert _cli(path, "lanes", "downgrade", "--check").returncode == 0
    assert path.read_bytes() == before
    with sqlite3.connect(path) as raw:
        assert "lane" not in {row[1] for row in raw.execute("PRAGMA table_info(run)")}


def test_doctor_never_switches_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_factory.operations import Diagnostic

    path = tmp_path / "db"
    ClaimStore(path).close()

    def diagnostics(config: Path) -> list[Diagnostic]:
        return [Diagnostic("stub", True, "ok", "")]

    monkeypatch.setattr(cli, "_doctor_diagnostics", diagnostics)
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-factory", "--state", str(path), "--config", str(tmp_path / "config"), "doctor"],
    )
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 0
    with closing(ClaimStore(path)) as store:
        assert store.lane_mode() == "kind"
