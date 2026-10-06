# pyright: reportPrivateUsage=false
"""Apply deterministic gates to rated pull requests in the resident cycle."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agent_factory.github import GitHubMergeRejectedError
from agent_factory.watch import deliver, result
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.config import SharedConfig
    from agent_factory.github import GitHubClient, ReviewActivity
    from agent_factory.store import Claim, ClaimStore

logger = logging.getLogger(__name__)
_PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/.*)?$")
_WRITERS = {"admin", "maintain", "write"}
# A review in one of these states sets its author's standing; a plain comment does not.
_DECISIVE = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
# GitHub answers a merge request synchronously, so an open pull request this long
# after the request means the merge did not happen.
_UNCONFIRMED = timedelta(minutes=5)


def _finish(
    store: ClaimStore,
    row: dict[str, Any],
    merge: dict[str, Any],
    state: str,
    reason: str,
    merge_sha: str | None = None,
) -> None:
    merge.update(
        state=state,
        reason=reason,
        merged_at=datetime.now(UTC).isoformat() if state == "merged" else None,
        merge_sha=merge_sha,
    )
    with store._transaction():
        watch_store.update(store, row["id"], merge_json=json.dumps(merge))
        deliver.queue(
            store,
            row,
            "risk",
            result.verdict(watch_store.json_field(row, "result_json"), merge),
            target="pr",
        )


def _wait_or_finish(
    store: ClaimStore, row: dict[str, Any], merge: dict[str, Any], reason: str, now: datetime
) -> None:
    if now >= datetime.fromisoformat(merge["checked_at"]) + timedelta(minutes=60):
        _finish(store, row, merge, "not-merged", f"{reason} after 60 minutes")
    elif merge.get("reason") != reason:
        merge["reason"] = reason
        watch_store.update(store, row["id"], merge_json=json.dumps(merge))


def _waiting_feedback(
    store: ClaimStore,
    client: GitHubClient,
    claim: Claim,
    activity: ReviewActivity,
    shared: SharedConfig,
    now: datetime,
) -> str | None:
    """Return "pending" when writer feedback would start a review round, "unknown"
    when a commenter's permission cannot be read, else None."""
    from agent_factory.work_kinds.pull_request.review import (
        eligible_review_activity,
        has_eligible_review,
        review_checkpoint,
    )

    cache: dict[str, str | None] = {}
    unknown = False

    def permission(login: str) -> str | None:
        nonlocal unknown
        if login not in cache:
            try:
                cache[login] = client.get_permission(claim.repository, login)
            except Exception:
                cache[login] = None
        if cache[login] is None:
            unknown = True
        return cache[login]

    eligible = eligible_review_activity(
        activity,
        since=review_checkpoint(store, claim, now),
        bot_login=shared.bot_login,
        permission=permission,
    )
    if has_eligible_review(eligible):
        return "pending"
    return "unknown" if unknown else None


def step(store: ClaimStore, client: GitHubClient, shared: SharedConfig) -> None:
    now = datetime.now(UTC)
    for row in watch_store.rows(store, "completed"):
        merge = watch_store.json_field(row, "merge_json")
        if merge.get("state") != "waiting":
            continue
        repository, number, head = row["repository"], row["pr_number"], merge["head_sha"]
        if (not shared.watch.enabled or not shared.watch.auto_merge) and not merge.get(
            "request_sent_at"
        ):
            _finish(store, row, merge, "not-merged", "auto-merge off")
            continue
        try:
            pull = client.get_pull_request_details(repository, number)
            if (
                pull.state == "MERGED"
                and pull.merge_commit_sha
                and client.merge_has_parent(repository, pull.merge_commit_sha, head)
            ):
                _finish(store, row, merge, "merged", "merged automatically", pull.merge_commit_sha)
                continue
        except Exception:
            logger.exception("watch merge read failed for %s", row["id"])
            if merge.get("request_sent_at"):
                # The request may have succeeded. A read failure is not evidence
                # that GitHub rejected it, even after the usual wait deadline.
                merge["reason"] = "merge outcome uncertain: GitHub read unavailable"
                watch_store.update(store, row["id"], merge_json=json.dumps(merge))
            else:
                _wait_or_finish(store, row, merge, "GitHub read unavailable", now)
            continue
        if merge.get("request_sent_at"):
            if pull.state == "MERGED":
                if pull.merge_commit_sha:
                    _finish(store, row, merge, "not-merged", "merged with a different head")
                elif merge.get("reason") != "merge outcome uncertain: merge commit unavailable":
                    merge["reason"] = "merge outcome uncertain: merge commit unavailable"
                    watch_store.update(store, row["id"], merge_json=json.dumps(merge))
            elif pull.state == "CLOSED":
                _finish(store, row, merge, "not-merged", "pull request closed without merge")
            elif now >= datetime.fromisoformat(merge["request_sent_at"]) + _UNCONFIRMED:
                _finish(store, row, merge, "not-merged", "merge request did not complete")
            else:
                reason = "merge outcome uncertain: " + str(
                    merge.get("request_error") or "no confirmation"
                )
                if merge.get("reason") != reason:
                    merge["reason"] = reason
                    watch_store.update(store, row["id"], merge_json=json.dumps(merge))
            continue
        if store.is_paused():
            _wait_or_finish(store, row, merge, "factory paused", now)
            continue
        claim = store.get_claim(row["claim_id"])
        if claim is None or claim.lifecycle in {"blocked", "cancelled", "superseded"}:
            state = claim.lifecycle if claim else "missing"
            _finish(store, row, merge, "not-merged", f"claim is {state}")
            continue
        if claim.lifecycle != "settled":
            # A review round may be running; its push will move the rated head.
            _wait_or_finish(store, row, merge, f"claim is {claim.lifecycle}", now)
            continue
        match = _PR_URL.fullmatch(row["pr_url"] or "")
        if (
            not match
            or f"{match[1]}/{match[2]}".lower() != repository.lower()
            or int(match[3]) != number
        ):
            _finish(store, row, merge, "not-merged", "pull request URL mismatch")
            continue
        targets = {target.repository.lower(): target.branch for target in shared.fix.targets}
        if repository.lower() not in targets or pull.base_ref != targets.get(repository.lower()):
            _finish(store, row, merge, "not-merged", "repository or base is not a fix target")
            continue
        if pull.state != "OPEN":
            _finish(store, row, merge, "not-merged", "pull request closed")
            continue
        if pull.draft:
            _finish(store, row, merge, "not-merged", "pull request is a draft")
            continue
        if pull.mergeable is False:
            _finish(store, row, merge, "not-merged", "pull request has a merge conflict")
            continue
        if pull.head_sha != head:
            _finish(store, row, merge, "not-merged", "rated head moved")
            continue
        if pull.mergeable is None:
            _wait_or_finish(store, row, merge, "mergeability pending", now)
            continue
        try:
            checks = client.commit_checks(repository, head)
            required = client.required_checks(repository, pull.base_ref)
            activity = client.list_review_activity(repository, number)
        except Exception:
            logger.exception("watch merge checks failed for %s", row["id"])
            _wait_or_finish(store, row, merge, "GitHub read unavailable", now)
            continue
        if checks.failed:
            _finish(store, row, merge, "not-merged", "failed checks: " + ", ".join(checks.failed))
        elif not required:
            _finish(store, row, merge, "not-merged", f"no required checks on {pull.base_ref}")
        elif not checks.reported:
            _wait_or_finish(store, row, merge, "no checks reported", now)
        elif checks.pending:
            _wait_or_finish(store, row, merge, "checks pending: " + ", ".join(checks.pending), now)
        elif missing := [name for name in required if name not in checks.successful]:
            _wait_or_finish(
                store, row, merge, "required checks not reported: " + ", ".join(missing), now
            )
        elif any(not thread.is_resolved for thread in activity.threads):
            _finish(store, row, merge, "not-merged", "unresolved review thread")
        elif feedback := _waiting_feedback(store, client, claim, activity, shared, now):
            if feedback == "unknown":
                _wait_or_finish(store, row, merge, "commenter permission unavailable", now)
            else:
                _finish(store, row, merge, "not-merged", "writer feedback awaits a review round")
        else:
            latest: dict[str, Any] = {}
            for review in activity.reviews:
                if review.state in _DECISIVE and (
                    review.author not in latest
                    or review.created_at > latest[review.author].created_at
                ):
                    latest[review.author] = review
            requesting = [
                author for author, review in latest.items() if review.state == "CHANGES_REQUESTED"
            ]
            permissions = {
                author: client.get_permission(repository, author) for author in requesting
            }
            if any(permission in _WRITERS for permission in permissions.values()):
                _finish(store, row, merge, "not-merged", "writer requested changes")
                continue
            if unknown := [author for author, value in permissions.items() if value is None]:
                _wait_or_finish(
                    store,
                    row,
                    merge,
                    "reviewer permission unavailable: " + ", ".join(sorted(unknown)),
                    now,
                )
                continue
            # Persist the attempt before the network call. A restart must never
            # send a second merge request when the first outcome is unknown.
            merge["request_sent_at"] = datetime.now(UTC).isoformat()
            merge["reason"] = "merge request outcome uncertain"
            watch_store.update(store, row["id"], merge_json=json.dumps(merge))
            try:
                merge_sha = client.merge_pull_request(repository, number, head)
            except GitHubMergeRejectedError as error:
                _finish(store, row, merge, "not-merged", f"GitHub rejected merge: {error}")
            except Exception as error:
                # A lost response may follow an accepted merge. Read once before
                # recording a terminal rejection; never send another merge request.
                accepted_sha: str | None = None
                try:
                    current = client.get_pull_request_details(repository, number)
                    if (
                        current.state == "MERGED"
                        and current.merge_commit_sha
                        and client.merge_has_parent(repository, current.merge_commit_sha, head)
                    ):
                        accepted_sha = current.merge_commit_sha
                except Exception:
                    pass
                if accepted_sha:
                    _finish(store, row, merge, "merged", "merged automatically", accepted_sha)
                else:
                    merge["request_error"] = str(error)
                    merge["reason"] = f"merge outcome uncertain: {error}"
                    watch_store.update(store, row["id"], merge_json=json.dumps(merge))
            else:
                _finish(store, row, merge, "merged", "merged automatically", merge_sha)
