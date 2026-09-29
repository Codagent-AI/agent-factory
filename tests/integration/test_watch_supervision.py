"""INT-004: a crashed launch lease ends with an alert and frees the cap."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_factory.config import (
    CredentialsConfig,
    LimitsConfig,
    LocalConfig,
    RepositoryConfig,
    ScheduleConfig,
)
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import store as watch_store
from agent_factory.watch import supervise


def test_launch_lease_expires_without_process(tmp_path: Path) -> None:
    local = LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        old = datetime.now(UTC) - timedelta(minutes=3)
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id="r",
            repository="o/r",
            issue_number=1,
            pr_number=None,
            pr_url=None,
            event_at=old.isoformat(),
            now=old.isoformat(),
        )
        row = watch_store.rows(store)[0]
        assert watch_store.claim_launch(
            store, row["id"], old, 90, "claude:m:e", str(tmp_path / "E")
        )
        supervise.supervise(store, local, tmp_path / "local.toml")
        ended = watch_store.get(store, row["id"])
        assert ended is not None
        assert ended["state"] == "launch-failed"
        assert watch_store.running_count(store) == 0
        assert "alert" in watch_store.json_field(ended, "deliveries_json")
    finally:
        store.close()
