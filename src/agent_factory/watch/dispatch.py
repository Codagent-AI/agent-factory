"""Apply the daily budget and concurrency cap before starting sessions."""

from __future__ import annotations

import json
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


def _readiness_failure(
    store: ClaimStore,
    local: LocalConfig,
    shared: SharedConfig,
    token_provider: InstallationTokenProvider,
) -> str:
    """Run the watch doctor group and record why launches wait; empty when it passes."""
    try:
        failures = [
            check
            for check in readiness.diagnostics(local, shared, token_provider)
            if not check.available
        ]
        reason = "; ".join(f"{item.name}: {item.detail}" for item in failures)
    except Exception as error:
        reason = f"watch readiness: {error}"
    store.set_setting("runtime", "readiness:watch", {"reason": reason} if reason else {})
    return reason


def dispatch(
    store: ClaimStore,
    local: LocalConfig,
    shared: SharedConfig,
    config_path: Path,
    token_provider: InstallationTokenProvider,
) -> None:
    watch = shared.watch
    started_today = watch_store.daily_count(store, local.schedule.timezone, datetime.now(UTC))
    readiness_failure: str | None = None  # computed once, only when a row could launch
    for row in watch_store.rows(store, "pending"):
        if row["event_kind"] not in {"PR-READY", "FAILURE"}:
            # A CLAIM or EVAL-DONE row queued by an earlier release: nothing handles it now.
            watch_store.update(store, row["id"], state="logged")
            continue
        if row["event_kind"] == "PR-READY" and row["pr_number"] is None:
            run = store.get_run(row["run_id"]) if row["run_id"] else None
            logger.info(
                "watch event %s: no parseable pull request URL", result.event_line(row, run)
            )
            watch_store.update(store, row["id"], state="logged")
            continue
        # The budget comes first so a spent budget is reported even when nothing could launch.
        if started_today >= watch.daily_sessions:
            deliver.end(
                store, row["id"], "budget-exhausted", "daily session budget spent", config_path
            )
            continue
        if watch_store.running_count(store) >= watch.max_sessions:
            continue
        if (
            row["event_kind"] == "PR-READY"
            and row["pr_number"] is not None
            and watch_store.pr_running(store, row["repository"], row["pr_number"])
        ):
            continue
        if readiness_failure is None:
            readiness_failure = _readiness_failure(store, local, shared, token_provider)
        if readiness_failure:
            continue
        profile = watch.agents.get(row["event_kind"], watch.agent)
        evidence, _ = session.paths(local, row["id"])
        now = datetime.now(UTC)
        if not watch_store.claim_launch(
            store, row["id"], now, watch.timeout_minutes, profile, str(evidence)
        ):
            continue
        started_today += 1
        launched = watch_store.get(store, row["id"])
        assert launched is not None
        try:
            identity = session.start(store, launched, local, shared, config_path, token_provider)
            watch_store.update(store, row["id"], process_json=json.dumps(identity))
        except Exception as error:
            logger.exception("watch launch failed for %s", row["id"])
            deliver.end(store, row["id"], "launch-failed", str(error), config_path)
            session.remove_clone(local, row["id"])
