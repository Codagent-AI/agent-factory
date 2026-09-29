"""Apply the daily budget and concurrency cap before starting sessions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agent_factory.watch import deliver, readiness, result, session
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import InstallationTokenProvider
    from agent_factory.store import ClaimStore

logger = logging.getLogger(__name__)


def dispatch(
    store: ClaimStore,
    local: LocalConfig,
    shared: SharedConfig,
    config_path: Path,
    token_provider: InstallationTokenProvider,
) -> None:
    ready: bool | None = None
    for row in watch_store.rows(store, "pending"):
        if row["event_kind"] in {"CLAIM", "EVAL-DONE"}:
            logger.info(
                "watch event %s",
                result.event_line(
                    row,
                    store.get_claim(row["claim_id"]),
                    store.get_run(row["run_id"]) if row["run_id"] else None,
                ),
            )
            watch_store.update(store, row["id"], state="logged")
            continue
        now = datetime.now(UTC)
        if (
            watch_store.daily_count(store, local.schedule.timezone, now)
            >= shared.watch.daily_sessions
        ):
            with store._transaction():  # pyright: ignore[reportPrivateUsage]
                watch_store.update(
                    store,
                    row["id"],
                    state="budget-exhausted",
                    detail="daily session budget spent",
                    finished_at=now.isoformat(),
                )
                ended = watch_store.get(store, row["id"])
                assert ended is not None
                deliver.queue(
                    store,
                    ended,
                    "budget",
                    result.notice(
                        ended,
                        config_path,
                        budget=True,
                        claim=store.get_claim(ended["claim_id"]),
                        run=store.get_run(ended["run_id"]) if ended["run_id"] else None,
                    ),
                    "issue",
                )
            continue
        if ready is None and watch_store.running_count(store) >= shared.watch.max_sessions:
            continue
        if ready is None:
            try:
                checks = readiness.diagnostics(local, shared, token_provider)
                failures = [check for check in checks if not check.available]
                reason = "; ".join(f"{item.name}: {item.detail}" for item in failures)
            except Exception as error:
                reason = f"watch readiness: {error}"
            store.set_setting("runtime", "readiness:watch", {"reason": reason} if reason else {})
            ready = not reason
        if not ready:
            continue
        if (
            row["event_kind"] == "PR-READY"
            and row["pr_number"] is not None
            and watch_store.pr_running(store, row["repository"], row["pr_number"])
        ):
            continue
        if watch_store.running_count(store) >= shared.watch.max_sessions:
            continue
        profile = shared.watch.agents.get(row["event_kind"], shared.watch.agent)
        evidence, _ = session.paths(local, row["id"])
        if not watch_store.claim_launch(
            store, row["id"], now, shared.watch.timeout_minutes, profile, str(evidence)
        ):
            continue
        launched = watch_store.get(store, row["id"])
        assert launched is not None
        try:
            identity = session.start(store, launched, local, shared, config_path, token_provider)
            import json

            watch_store.update(store, row["id"], process_json=json.dumps(identity))
        except Exception as error:
            logger.exception("watch launch failed for %s", row["id"])
            with store._transaction():  # pyright: ignore[reportPrivateUsage]
                watch_store.update(
                    store,
                    row["id"],
                    state="launch-failed",
                    detail=str(error),
                    finished_at=datetime.now(UTC).isoformat(),
                )
                ended = watch_store.get(store, row["id"])
                assert ended is not None
                deliver.queue(
                    store,
                    ended,
                    "alert",
                    result.notice(
                        ended,
                        config_path,
                        budget=False,
                        claim=store.get_claim(ended["claim_id"]),
                        run=store.get_run(ended["run_id"]) if ended["run_id"] else None,
                    ),
                    "issue",
                )
            session.remove_clone(local, row["id"])
