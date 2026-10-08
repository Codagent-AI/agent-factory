# pyright: reportPrivateUsage=false
"""INT-006: readiness and status with a fake Claude executable."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, NotifyConfig, SharedConfig
from agent_factory.notify import store as records
from agent_factory.notify.readiness import diagnostics
from agent_factory.notify.status import lines
from agent_factory.store import ClaimDraft, ClaimStore
from tests.integration.test_fix_config import _LOCAL_BASE


def test_readiness_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "claude"
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = auth ]; then exit 0; fi\n'
        "echo '--tools --allowedTools --json-schema --strict-mcp-config'\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HOME", str(tmp_path))
    shared = replace(
        SharedConfig.from_file(Path("config/codagent.toml")),
        notify=NotifyConfig(enabled=True, agent="claude:fake:low"),
    )
    local = LocalConfig.from_toml(_LOCAL_BASE)
    assert any(not check.available for check in diagnostics(local, shared))
    (tmp_path / ".claude" / "sessions").mkdir(parents=True)
    assert all(check.available for check in diagnostics(local, shared))
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        now = datetime.now(UTC)
        for i in range(4):
            records.insert(
                store,
                claim.id,
                f"run-{i}",
                "o/r",
                1,
                "fix",
                ("failed", "settled", "cancelled", "not-queued")[i],
                now,
            )
        for i, row in enumerate(records.rows(store)):
            if i < 2:
                records.update(
                    store,
                    row["id"],
                    "settling",
                    state="launched",
                    launched_at=now.isoformat(),
                    deadline_at=now.isoformat(),
                )
                records.end(
                    store, row["id"], "launched", "sent", session_name="target", cost_usd=0.01
                )
            else:
                records.end(store, row["id"], "settling", "unmarked" if i == 3 else "no-session")
        rendered = "\n".join(lines(store, local, shared))
        assert "notify sessions today: 2 of 30" in rendered
        assert "no-session" in rendered
        assert "unmarked" not in rendered
        assert "notifications: disabled" in lines(
            store, local, replace(shared, notify=NotifyConfig())
        )
    finally:
        store.close()
