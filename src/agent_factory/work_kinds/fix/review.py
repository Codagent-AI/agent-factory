"""PR review intake: eligibility filtering and review-round admission."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig
from agent_factory.github import (
    WRITER_PERMISSIONS,
    GitHubApiError,
    GitHubClient,
    IssueComment,
    ReviewActivity,
)
from agent_factory.store import Claim, ClaimStore, NonterminalRunError, Run
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.fix.handler import FixHandler


def _timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def eligible_review_activity(
    activity: ReviewActivity,
    *,
    since: str | None,
    bot_login: str,
    permission: Callable[[str], str | None],
) -> dict[str, list[dict[str, object]]]:
    checkpoint = _timestamp(since) if since else None
    if since and checkpoint is None:
        return {"reviews": [], "threads": [], "comments": []}

    def valid(c: IssueComment) -> bool:
        made = _timestamp(c.created_at)
        return (
            bool(c.body.strip())
            and c.author != bot_login
            and (checkpoint is None or made is not None and made > checkpoint)
            and permission(c.author) in WRITER_PERMISSIONS
        )

    def item(c: IssueComment) -> dict[str, object]:
        return {"id": c.id, "author": c.author, "body": c.body, "created_at": c.created_at}

    threads: list[dict[str, object]] = []
    for thread in activity.threads:
        comments = [item(c) for c in thread.comments if valid(c)]
        if comments and not thread.is_resolved:
            threads.append(
                {"id": thread.id, "path": thread.path, "line": thread.line, "comments": comments}
            )
    return {
        "reviews": [item(c) for c in activity.reviews if valid(c)],
        "threads": threads,
        "comments": [item(c) for c in activity.comments if valid(c)],
    }


def has_eligible_review(activity: dict[str, list[dict[str, object]]]) -> bool:
    return any(activity.values())


def process_review_claim(
    store: ClaimStore,
    client: GitHubClient,
    handler: FixHandler,
    claim: Claim,
    *,
    bot_login: str,
    artifact_root: Path,
    now: datetime,
    local: LocalConfig,
    memory_available: bool = True,
) -> tuple[Run, Preparation] | None:
    if (
        claim.lifecycle not in {"settled", "blocked"}
        or claim.lifecycle == "blocked"
        and claim.outcome.get("blocked_by") != "review"
    ):
        return None
    raw_pr = claim.outcome.get("pr")
    if not isinstance(raw_pr, dict):
        # Claims settled before the PR record was kept on the outcome still
        # carry it on their latest run result.
        raw_pr = handler._prior_pull_request(claim)  # pyright: ignore[reportPrivateUsage]
    if not isinstance(raw_pr, dict):
        return None
    pr = cast(dict[str, object], raw_pr)
    number = pr.get("number")
    if not isinstance(number, int):
        return None
    try:
        if client.get_pull_request(claim.repository, number).state.lower() != "open":
            return None
        cache: dict[str, str | None] = {}

        def permission(login: str) -> str | None:
            if login not in cache:
                try:
                    cache[login] = client.get_permission(claim.repository, login)
                except (GitHubApiError, OSError):
                    cache[login] = None
            return cache[login]

        activity = client.list_review_activity(claim.repository, number)
    except (GitHubApiError, OSError):
        return None
    checkpoint = claim.outcome.get("review_checkpoint")
    if not isinstance(checkpoint, str):
        finished = [r.finished_at for r in store.runs_for_claim(claim.id) if r.finished_at]
        checkpoint = max(finished) if finished else now.isoformat()
    eligible = eligible_review_activity(
        activity, since=checkpoint, bot_login=bot_login, permission=permission
    )
    if not has_eligible_review(eligible):
        return None
    store.set_claim_lifecycle(
        claim.id, claim.lifecycle, {**claim.outcome, "waiting_review": eligible}
    )
    if (
        store.is_paused()
        or store.nonterminal_runs(kind="fix")
        or not memory_available
        or not handler.window(local).allows_admission(now)
    ):
        return None
    review: dict[str, object] = {
        "repository": claim.repository,
        "number": claim.issue_number,
        "claim_id": claim.id,
        "pull_request": pr,
        "branch": pr.get("branch"),
        "head_sha": pr.get("head_sha"),
        **eligible,
    }
    if not isinstance(review["branch"], str) or not isinstance(review["head_sha"], str):
        return None
    try:
        preparation = handler.prepare_review(claim, review)
        run = store.reserve_run(
            claim.id,
            "fix",
            reason="review",
            evidence_path=str(artifact_root / f"{claim.id}-fix-review"),
        )
    except (NonterminalRunError, OSError):
        return None
    stamp = now.isoformat()
    store.set_claim_lifecycle(
        claim.id,
        "active",
        {
            **claim.outcome,
            "review_checkpoint": stamp,
            "pre_review_verdict": claim.outcome.get("verdict", "pending-human-review"),
        },
    )
    client.set_attention_label(claim.repository, claim.issue_number, False)
    store.record_event(claim.id, f"review:{run.id}", "Review round started for writer feedback.")
    return run, preparation
