"""Store-driven release and retention for claims that have stopped running."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol, cast

from agent_factory import retention
from agent_factory.config import LocalConfig
from agent_factory.controller import Controller
from agent_factory.store import NONTERMINAL_RUN_STATUSES, TERMINAL_LIFECYCLES, Claim, ClaimStore
from agent_factory.work_kinds.base import WorkKindHandler
from agent_factory.work_kinds.pull_request.kinds import registered
from agent_factory.work_kinds.pull_request.sync import find_pr, sync_state

logger = logging.getLogger(__name__)


class PullRequestReader(Protocol):
    def get_pull_request(self, repository: str, number: int) -> object: ...


def terminal_time(store: ClaimStore, claim: Claim) -> datetime:
    value = claim.cleanup.get("terminal_at")
    if not isinstance(value, str):
        value = claim.updated_at
        store.set_cleanup(
            claim.id, {**claim.cleanup, "terminal_at": value, "terminal_at_backfilled": True}
        )
    return datetime.fromisoformat(value)


def machines_held(store: ClaimStore, claim: Claim) -> bool:
    runs = store.runs_for_claim(claim.id)
    if any(store.get_setting("runtime", f"fly:machine:{run.id}") is not None for run in runs):
        return True
    failures = store.get_setting("runtime", "fly:cleanup-failed") or {}
    items = failures.get("machines")
    run_ids = {run.id for run in runs}
    machine_ids = {
        machine_id
        for run in runs
        if isinstance((machine := run.progress.get("machine")), Mapping)
        if isinstance((machine_id := cast(Mapping[str, object], machine).get("id")), str)
    }
    return isinstance(items, list) and any(
        isinstance(item, Mapping)
        and (
            cast(Mapping[str, object], item).get("run_id") in run_ids
            or cast(Mapping[str, object], item).get("machine_id") in machine_ids
        )
        for item in cast(list[object], items)
    )


def sync_pending(store: ClaimStore, claim: Claim, client: PullRequestReader) -> bool:
    if claim.lifecycle != "settled" or sync_state(claim).get("completed"):
        return False
    definition = next((item for item in registered() if item.kind == claim.kind), None)
    if definition is None:
        return False
    pr = find_pr(store, claim, definition)
    if pr is None:
        return False
    try:
        state = client.get_pull_request(claim.repository, pr[0])
    except Exception:
        return True
    return getattr(state, "merged_at", None) is not None


def quiescent(store: ClaimStore, claim: Claim, client: PullRequestReader) -> bool:
    return (
        claim.lifecycle in TERMINAL_LIFECYCLES
        and not any(
            run.status in NONTERMINAL_RUN_STATUSES for run in store.runs_for_claim(claim.id)
        )
        and not machines_held(store, claim)
        and not store.pending_events(claim.id)
        and not claim.reporting.get("delivery_failures")
        and not sync_pending(store, claim, client)
    )


def sweep(
    store: ClaimStore,
    controller: Controller,
    client: PullRequestReader,
    handlers: Mapping[str, WorkKindHandler],
    local: LocalConfig,
    seen: Mapping[str, str],
    now: datetime,
) -> None:
    from agent_factory.fly.registry_cleanup import reconcile_claim_image

    registry_cache: dict[str, dict[str, str]] = {}
    registry_client = None
    for saved in store.terminal_claims():
        try:
            claim = store.get_claim(saved.id) or saved
            if any(
                run.status in NONTERMINAL_RUN_STATUSES for run in store.runs_for_claim(claim.id)
            ):
                continue
            since = terminal_time(store, claim)
            store.clear_delivered_failures(saved.id)
            claim = store.get_claim(saved.id) or claim
            if claim.cleanup.get("sweep_complete") is True:
                continue
            handler = handlers.get(claim.kind)
            if handler is None:
                continue
            status = seen.get(claim.id)
            due = claim.lifecycle in {"cancelled", "superseded"} or (
                status != "Done"
                and now - since >= timedelta(days=local.limits.unreviewed_retention_days)
            )
            if due and claim.cleanup.get("complete") is not True:
                if claim.kind == "eval" and claim.lifecycle == "settled":
                    events = claim.reporting.get("events")
                    event_values: Mapping[str, object] = (
                        cast(Mapping[str, object], events) if isinstance(events, Mapping) else {}
                    )
                    published = any(
                        key.endswith(":review-command")
                        and isinstance(value, Mapping)
                        and bool(cast(Mapping[str, object], value).get("comment_id"))
                        for key, value in event_values.items()
                    )
                    if published and "review-expired" not in event_values:
                        message = handler.expiry_message(claim)
                        store.record_event(claim.id, "review-expired", message)
                        claim = store.get_claim(claim.id) or claim
                if store.pending_events(claim.id):
                    controller.deliver_reports(claim.id)
                    claim = store.get_claim(claim.id) or claim
                if quiescent(store, claim, client):
                    handler.release(claim)
                    claim = store.get_claim(claim.id) or claim
            retention.prune_due(store, local, claim, status, now, handler=handler, client=client)
            if claim.kind == "eval":
                if registry_client is None and local.fly is not None:
                    from agent_factory.fly.api import FlyMachinesClient

                    registry_client = FlyMachinesClient(local.fly.app, local.fly.token_file)
                reconcile_claim_image(store, claim, registry_client, now, registry_cache)
            claim = store.get_claim(saved.id) or claim
            retention_state = claim.cleanup.get("retention")
            registry_state = claim.cleanup.get("registry")
            pruned = isinstance(retention_state, Mapping) and bool(
                cast(Mapping[str, object], retention_state).get("pruned_at")
            )
            images_done = not isinstance(registry_state, Mapping) or all(
                isinstance(item, Mapping)
                and bool(cast(Mapping[str, object], item).get("deleted_at"))
                for item in cast(Mapping[str, object], registry_state).values()
            )
            if claim.cleanup.get("complete") is True and pruned and images_done:
                store.set_cleanup(claim.id, {**claim.cleanup, "sweep_complete": True})
        except Exception:
            logger.exception("terminal cleanup failed for claim %s", saved.id)
