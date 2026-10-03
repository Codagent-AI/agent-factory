"""Best-effort notification of the Claude session that handed off an issue."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import GitHubClient, ProjectQueueItem
    from agent_factory.store import ClaimStore

logger = logging.getLogger(__name__)

# Submodules are imported inside the functions below, not at package import. The skills run
# `python -m agent_factory.notify.marker`, and an eager import of `marker` (through `deliver` and
# `detect`) here would make runpy warn that the module was already loaded.


def _safe(name: str, action: Callable[[], None]) -> None:
    try:
        action()
    except Exception:
        logger.exception("notify %s failed", name)


def begin(store: ClaimStore, shared: SharedConfig) -> None:
    from agent_factory.notify import store as records

    if shared.notify.enabled and records.cursor(store) is None:
        store.set_setting("notify", "cursor", {"enabled_at": datetime.now(UTC).isoformat()})


def step(
    store: ClaimStore,
    client: GitHubClient,
    shared: SharedConfig,
    local: LocalConfig,
    cards: list[ProjectQueueItem] | None,
) -> None:
    from agent_factory.notify import deliver, detect, supervise
    from agent_factory.notify import store as records

    _safe("supervise", lambda: supervise.supervise(store, local))
    if shared.notify.enabled:
        if cards is None:
            _safe("outage", lambda: records.restart_settling(store))
        elif records.cursor(store) is not None:
            bodies: dict[tuple[str, int], str] = {}
            _safe("detect", lambda: detect.detect(store, client, shared, cards, bodies))
            _safe("deliver", lambda: deliver.deliver(store, shared, local, bodies))
    else:
        _safe("disable", lambda: records.disable(store))
    _safe("prune", lambda: records.prune(store, local))
