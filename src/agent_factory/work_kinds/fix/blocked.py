"""Re-admission scan for blocked fix claims: an eligible comment or a drag to Ready."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import hold_active
from agent_factory.github import (
    WRITER_PERMISSIONS,
    GitHubApiError,
    GitHubClient,
    IssueComment,
    ProjectQueueItem,
)
from agent_factory.store import Claim, ClaimStore, NonterminalRunError, Run
from agent_factory.suites.and_scene import ReadinessError, WorktreeError
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.fix.handler import FixHandler


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def eligible_comments(
    comments: Sequence[IssueComment],
    *,
    since: str | None,
    bot_login: str,
    permission: Callable[[str], str | None],
) -> list[IssueComment]:
    """Keep writer comments newer than the decline; drop the bot's own and everyone else's."""
    since_time = _parse_timestamp(since) if since is not None else None
    if since is not None and since_time is None:
        # An unparsable decline timestamp must not silently disable the recency gate;
        # fail closed rather than risk treating every comment as eligible.
        return []
    result: list[IssueComment] = []
    for comment in comments:
        if comment.author == bot_login:
            continue
        if since_time is not None:
            comment_time = _parse_timestamp(comment.created_at)
            # A missing or unparsable timestamp cannot be proven newer than the decline;
            # treat it as ineligible rather than trusting stale or malformed input.
            if comment_time is None or comment_time <= since_time:
                continue
        if permission(comment.author) not in WRITER_PERMISSIONS:
            continue
        result.append(comment)
    return result


def process_blocked_claim(
    store: ClaimStore,
    client: GitHubClient,
    handler: FixHandler,
    shared: SharedConfig,
    local: LocalConfig,
    card: ProjectQueueItem,
    claim: Claim,
    *,
    bot_login: str,
    artifact_root: Path,
    now: datetime,
    memory_available: bool = True,
) -> tuple[Run, Preparation] | None:
    """Re-admit one blocked fix claim if eligible; return the reserved run and its clones.

    Without memory headroom the eligible claim is still reconciled against an earlier
    attempt's branch or pull request, but no clones are cut and no run is reserved.
    """
    if claim.lifecycle != "blocked":
        return None
    # Cheap SQLite gates first; the paginated comment listing only runs when an
    # unblock could actually be admitted this cycle.
    if (
        store.is_paused()
        or store.nonterminal_runs(kind="fix")
        or not handler.window(local).allows_admission(now)
    ):
        return None
    quota = store.get_hold(claim.id, "quota")
    if quota is not None and hold_active(quota, now):
        return None
    # Normal admission scopes provider quota holds to the providers a claim uses;
    # an unblock attempt must honor the same holds instead of bypassing them.
    provider_holds = store.get_settings_by_prefix("admission", "quota:")
    for provider in handler.providers(claim):
        hold = provider_holds.get(f"quota:{provider}")
        if hold is not None and hold_active(hold, now):
            return None
    since = claim.outcome.get("declined_at")
    since_value = since if isinstance(since, str) else None
    permission_cache: dict[str, str | None] = {}

    def _permission(login: str) -> str | None:
        if login not in permission_cache:
            try:
                permission_cache[login] = client.get_permission(claim.repository, login)
            except (GitHubApiError, OSError):
                # One author's lookup failing (API hiccup, renamed/deleted user) must not
                # abort the whole pass; treat that author as ineligible and keep scanning.
                permission_cache[login] = None
        return permission_cache[login]

    comments = client.list_comment_records(claim.repository, claim.issue_number)
    eligible = eligible_comments(
        comments, since=since_value, bot_login=bot_login, permission=_permission
    )
    if handler.gesture(claim, card, eligible) != "unblock":
        return None
    store.set_preparation(
        claim.id,
        {
            **claim.preparation,
            "issue": {
                "repository": claim.repository,
                "number": claim.issue_number,
                "comments": [{"author": c.author, "body": c.body} for c in eligible],
            },
        },
    )
    # Reconcile side effects and cut fresh clones before any attempt is reserved, exactly
    # as first admission does; a launch problem leaves the claim blocked for the next poll.
    preparation: Preparation | None = None
    try:
        if memory_available:
            preparation = handler.prepare(claim)
        else:
            handler.reconcile(claim)
    except (ReadinessError, WorktreeError) as error:
        store.record_event(claim.id, f"unblock-readiness:{error}", f"Cannot re-admit yet: {error}")
        return None
    refreshed = store.get_claim(claim.id)
    if refreshed is None or refreshed.lifecycle != "blocked":
        # Reconciliation found an earlier attempt's pull request and settled the claim.
        client.set_attention_label(claim.repository, claim.issue_number, False)
        return None
    if preparation is None:
        # Still blocked and eligible; the memory gate defers the relaunch to a later poll.
        return None
    try:
        run = store.reserve_run(
            claim.id,
            "fix",
            reason="unblock",
            evidence_path=str(artifact_root / f"{claim.id}-fix-unblock"),
        )
    except NonterminalRunError:
        _discard_clones(preparation)
        return None
    store.set_claim_lifecycle(claim.id, "active", {})
    client.set_attention_label(claim.repository, claim.issue_number, False)
    store.record_event(
        claim.id, "unblock", "Re-admitted after eligible input; starting a new attempt."
    )
    return run, preparation


def _discard_clones(preparation: Preparation) -> None:
    """Remove clones cut for an attempt that lost the slot race; nothing tracks them."""
    clones = preparation.payload.get("clones")
    if not isinstance(clones, Mapping):
        return
    for path in cast(Mapping[str, object], clones).values():
        if isinstance(path, str):
            shutil.rmtree(path, ignore_errors=True)
