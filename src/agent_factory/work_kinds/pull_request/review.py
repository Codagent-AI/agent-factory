"""PR review intake: eligibility filtering and review-round admission."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig
from agent_factory.controller import hold_active
from agent_factory.github import (
    WRITER_PERMISSIONS,
    GitHubApiError,
    GitHubClient,
    IssueComment,
    ReviewActivity,
)
from agent_factory.store import Claim, ClaimStore, NonterminalRunError, Run
from agent_factory.suites.and_scene import ReadinessError, WorktreeError
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler


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
    handler: PullRequestHandler,
    claim: Claim,
    *,
    bot_login: str,
    artifact_root: Path,
    now: datetime,
    local: LocalConfig,
    readiness: Callable[[], bool],
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
        raw_pr = handler.prior_pull_request(claim)
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
        claim.id, claim.lifecycle, {**claim.outcome, "pr": pr, "waiting_review": eligible}
    )
    claim = store.get_claim(claim.id) or claim
    if (
        store.is_paused()
        or store.nonterminal_runs(kind=handler.kind)
        or not memory_available
        or not handler.window(local).allows_admission(now)
        or not readiness()
    ):
        return None
    # Review rounds honor the same claim and provider quota holds as normal admission.
    quota = store.get_hold(claim.id, "quota")
    if quota is not None and hold_active(quota, now):
        return None
    provider_holds = store.get_settings_by_prefix("admission", "quota:")
    for provider in handler.providers(claim):
        hold = provider_holds.get(f"quota:{provider}")
        if hold is not None and hold_active(hold, now):
            return None
    branch = pr.get("branch")
    if not isinstance(branch, str):
        return None
    # The reviewer or an earlier round may have pushed since the PR record was saved.
    try:
        head = client.get_branch(claim.repository, branch)
    except (GitHubApiError, OSError):
        return None
    if head is None:
        return None
    pr = {**pr, "head_sha": head.sha}
    stored_issue = claim.preparation.get("issue")
    issue: Mapping[str, object] = (
        cast(Mapping[str, object], stored_issue) if isinstance(stored_issue, Mapping) else {}
    )
    title, body = issue.get("title"), issue.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        try:
            source = client.get_source_item(claim.repository, claim.issue_number)
        except (GitHubApiError, OSError):
            return None
        title, body = source.title, source.body
    review: dict[str, object] = {
        "repository": claim.repository,
        "number": claim.issue_number,
        "title": title,
        "body": body,
        "claim_id": claim.id,
        "kind": claim.kind,
        "pull_request": pr,
        "branch": branch,
        "head_sha": head.sha,
        **eligible,
    }
    try:
        preparation = handler.prepare_review(claim, review)
    except (ReadinessError, WorktreeError) as error:
        # A launch problem leaves the claim in Review for the next poll, as unblock does.
        store.record_event(
            claim.id, f"review-readiness:{error}", f"Cannot start the review round yet: {error}"
        )
        return None
    try:
        run = store.reserve_run(
            claim.id,
            handler.definition.unit_key,
            reason="review",
            evidence_path=str(artifact_root / f"{claim.id}-{handler.definition.unit_key}-review"),
        )
    except (NonterminalRunError, OSError):
        return None
    stamp = now.isoformat()
    store.set_claim_lifecycle(
        claim.id,
        "active",
        {
            **{k: v for k, v in claim.outcome.items() if k != "waiting_review"},
            "pr": pr,
            "review_checkpoint": stamp,
            "pre_review_verdict": claim.outcome.get("verdict", "pending-human-review"),
        },
    )
    # The run is already reserved; a label hiccup must not strand it unlaunched.
    with contextlib.suppress(GitHubApiError, OSError):
        client.set_attention_label(claim.repository, claim.issue_number, False)
    if handler.kind == "feature":
        ids = [
            str(item["id"])
            for group in ("reviews", "comments")
            for item in eligible[group]
            if "id" in item
        ]
        ids.extend(
            str(comment["id"])
            for thread in eligible["threads"]
            for comment in cast(list[dict[str, object]], thread.get("comments", []))
            if "id" in comment
        )
        body = (
            f"Feature review round started for {pr.get('url')}. "
            f"Addressing feedback: {', '.join(ids)}."
        )
    else:
        body = "Review round started for writer feedback."
    store.record_event(claim.id, f"review:{run.id}", body)
    return run, preparation
