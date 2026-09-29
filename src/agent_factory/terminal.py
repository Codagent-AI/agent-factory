"""Store-driven release and retention for claims that have stopped running."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol, cast

from agent_factory import retention
from agent_factory.config import LocalConfig
from agent_factory.controller import Controller
from agent_factory.github import GitHubNotFoundError
from agent_factory.store import NONTERMINAL_RUN_STATUSES, TERMINAL_LIFECYCLES, Claim, ClaimStore
from agent_factory.work_kinds.base import WorkKindHandler, claim_is_idle, mapping
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


def backfill_terminal_times(store: ClaimStore) -> None:
    """Record a terminal time for legacy terminal claims that have none."""
    for claim in store.terminal_claims():
        if "terminal_at" not in claim.cleanup and not (
            claim.lifecycle == "settled"
            and any(
                run.status in NONTERMINAL_RUN_STATUSES
                or not store.get_setting("consumed-results", run.id)
                for run in store.runs_for_claim(claim.id)
            )
        ):
            terminal_time(store, claim)


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


def sync_pending(
    store: ClaimStore,
    claim: Claim,
    client: PullRequestReader,
    cache: dict[str, bool] | None = None,
) -> bool:
    if cache is not None and claim.id in cache:
        return cache[claim.id]
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
    except GitHubNotFoundError:
        pending = False
        error_text = None
    except Exception as error:
        logger.warning("PR state for %s#%s unreadable: %s", claim.repository, pr[0], error)
        pending = True
        error_text = str(error)
    else:
        pending = getattr(state, "merged_at", None) is not None
        error_text = None
    existing = claim.cleanup.get("sync_check_error")
    if error_text != existing:
        cleanup = dict((store.get_claim(claim.id) or claim).cleanup)
        if error_text is None:
            cleanup.pop("sync_check_error", None)
        else:
            cleanup["sync_check_error"] = error_text
        store.set_cleanup(claim.id, cleanup)
    if cache is not None:
        cache[claim.id] = pending
    return pending


def idle_and_reported(store: ClaimStore, claim: Claim) -> bool:
    """No attempt can still use the claim's files, and all of its reporting is delivered."""
    return (
        claim_is_idle(store, claim)
        and not machines_held(store, claim)
        and not store.pending_events(claim.id)
        and not claim.reporting.get("delivery_failures")
    )


def quiescent(
    store: ClaimStore,
    claim: Claim,
    client: PullRequestReader,
    cache: dict[str, bool] | None = None,
) -> bool:
    return (
        claim.lifecycle in TERMINAL_LIFECYCLES
        and idle_and_reported(store, claim)
        and not sync_pending(store, claim, client, cache)
    )


def release_due(
    claim: Claim, board_status: str | None, since: datetime, now: datetime, days: int
) -> bool:
    return claim.lifecycle in {"cancelled", "superseded"} or (
        claim.lifecycle == "settled"
        and (
            (
                board_status == "Done"
                and bool(claim.cleanup.get("done_observed_at"))
                and claim.cleanup.get("review_observed") is not True
            )
            or (board_status != "Done" and now - since >= timedelta(days=days))
        )
    )


def review_command_published(claim: Claim) -> bool:
    return any(
        key.endswith(":review-command") and bool(mapping(value).get("comment_id"))
        for key, value in mapping(claim.reporting.get("events")).items()
    )


def sweep(
    store: ClaimStore,
    controller: Controller,
    client: PullRequestReader,
    handlers: Mapping[str, WorkKindHandler],
    local: LocalConfig,
    seen: Mapping[str, str],
    now: datetime,
    sync_cache: dict[str, bool] | None = None,
) -> None:
    from agent_factory.fly.registry_cleanup import image_deleted, reconcile_claim_image

    sync_cache = sync_cache if sync_cache is not None else {}
    registry_cache: dict[str, dict[str, str]] = {}
    registry_client = None
    for saved in store.terminal_claims():
        if saved.cleanup.get("sweep_complete") is True:
            continue
        try:
            claim = store.get_claim(saved.id) or saved
            if not claim_is_idle(store, claim):
                continue
            since = terminal_time(store, claim)
            store.clear_delivered_failures(saved.id)
            claim = store.get_claim(saved.id) or claim
            handler = handlers.get(claim.kind)
            if handler is None:
                continue
            status = seen.get(claim.id)
            due = release_due(claim, status, since, now, local.limits.unreviewed_retention_days)
            if due and claim.cleanup.get("complete") is not True:
                if (
                    claim.kind == "eval"
                    and claim.lifecycle == "settled"
                    and status != "Done"
                    and review_command_published(claim)
                    and "review-expired" not in mapping(claim.reporting.get("events"))
                ):
                    message = handler.expiry_message(claim)
                    store.record_event(claim.id, "review-expired", message)
                    claim = store.get_claim(claim.id) or claim
                if store.pending_events(claim.id):
                    controller.deliver_reports(claim.id)
                    claim = store.get_claim(claim.id) or claim
                if quiescent(store, claim, client, sync_cache):
                    handler.release(claim)
                    claim = store.get_claim(claim.id) or claim
            retention.prune_due(
                store,
                local,
                claim,
                status,
                now,
                handler=handler,
                client=client,
                sync_cache=sync_cache,
            )
            if claim.kind == "eval":
                if registry_client is None and local.fly is not None:
                    from agent_factory.fly.api import FlyMachinesClient

                    registry_client = FlyMachinesClient(local.fly.app, local.fly.token_file)
                reconcile_claim_image(store, claim, registry_client, now, registry_cache)
            claim = store.get_claim(saved.id) or claim
            pruned = bool(mapping(claim.cleanup.get("retention")).get("pruned_at"))
            images_done = all(
                image_deleted(item) for item in mapping(claim.cleanup.get("registry")).values()
            )
            if claim.cleanup.get("complete") is True and pruned and images_done:
                store.set_cleanup(claim.id, {**claim.cleanup, "sweep_complete": True})
        except Exception:
            logger.exception("terminal cleanup failed for claim %s", saved.id)
