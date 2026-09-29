"""One resilient service-driven watch step per factory cycle."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agent_factory.watch import deliver as delivery
from agent_factory.watch import detect, dispatch, supervise
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import GitHubClient, InstallationTokenProvider
    from agent_factory.store import ClaimStore

logger = logging.getLogger(__name__)


def _safe(name: str, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:
        logger.exception("watch %s failed", name)


def _prune(store: ClaimStore, local: LocalConfig) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=local.limits.evidence_retention_days)
    for row in watch_store.rows(store):
        if row["state"] in {"pending", "launched"} or not row["finished_at"]:
            continue
        if datetime.fromisoformat(row["finished_at"]) < cutoff and row["evidence_path"]:
            shutil.rmtree(Path(row["evidence_path"]), ignore_errors=True)


def step(
    store: ClaimStore,
    client: GitHubClient,
    shared: SharedConfig,
    local: LocalConfig,
    config_path: Path,
    token_provider: InstallationTokenProvider,
) -> None:
    _safe("supervise", lambda: supervise.supervise(store, local, config_path))
    if shared.watch.enabled:
        _safe("detect", lambda: detect.detect(store, shared.watch.grace_minutes))
        _safe(
            "dispatch", lambda: dispatch.dispatch(store, local, shared, config_path, token_provider)
        )
    else:
        _safe("disable", lambda: watch_store.clear_cursor(store))
    _safe("deliver", lambda: delivery.deliver(store, client, shared.bot_login))
    _safe("prune", lambda: _prune(store, local))
