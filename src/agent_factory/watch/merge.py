# pyright: reportPrivateUsage=false
"""Apply deterministic gates to rated pull requests in the resident cycle."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from agent_factory.watch import deliver, result
from agent_factory.watch import store as watch_store

if TYPE_CHECKING:
    from agent_factory.config import SharedConfig
    from agent_factory.github import GitHubClient
    from agent_factory.store import ClaimStore

logger = logging.getLogger(__name__)
_PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)(?:/.*)?$")
_WRITERS = {"admin", "maintain", "write"}


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


def step(store: ClaimStore, client: GitHubClient, shared: SharedConfig) -> None:
    now = datetime.now(UTC)
    for row in watch_store.rows(store, "completed"):
        merge = watch_store.json_field(row, "merge_json")
        if merge.get("state") != "waiting":
            continue
        repository, number, head = row["repository"], row["pr_number"], merge["head_sha"]
        try:
            pull = client.get_pull_request(repository, number)
            if (
                pull.state == "MERGED"
                and pull.merge_commit_sha
                and client.merge_has_parent(repository, pull.merge_commit_sha, head)
            ):
                _finish(store, row, merge, "merged", "merged automatically", pull.merge_commit_sha)
                continue
        except Exception:
            logger.exception("watch merge read failed for %s", row["id"])
            _wait_or_finish(store, row, merge, "GitHub read unavailable", now)
            continue
        if not shared.watch.enabled or not shared.watch.auto_merge:
            _finish(store, row, merge, "not-merged", "auto-merge off")
            continue
        if store.is_paused():
            _wait_or_finish(store, row, merge, "factory paused", now)
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
        if pull.state != "OPEN" or pull.draft or pull.mergeable is False:
            _finish(store, row, merge, "not-merged", "pull request closed, draft, or conflicting")
            continue
        if pull.head_sha != head:
            _finish(store, row, merge, "not-merged", "rated head moved")
            continue
        if pull.mergeable is None:
            _wait_or_finish(store, row, merge, "mergeability pending", now)
            continue
        try:
            checks = client.commit_checks(repository, head)
            activity = client.list_review_activity(repository, number)
            permissions = {
                review.author: client.get_permission(repository, review.author)
                for review in activity.reviews
            }
        except Exception:
            logger.exception("watch merge checks failed for %s", row["id"])
            _wait_or_finish(store, row, merge, "GitHub read unavailable", now)
            continue
        if checks.failed:
            _finish(store, row, merge, "not-merged", "failed checks: " + ", ".join(checks.failed))
        elif not checks.reported:
            _wait_or_finish(store, row, merge, "no checks reported", now)
        elif checks.pending:
            _wait_or_finish(store, row, merge, "checks pending: " + ", ".join(checks.pending), now)
        elif any(not thread.is_resolved for thread in activity.threads):
            _finish(store, row, merge, "not-merged", "unresolved review thread")
        else:
            latest: dict[str, Any] = {}
            for review in activity.reviews:
                if permissions[review.author] in _WRITERS and (
                    review.author not in latest
                    or review.created_at > latest[review.author].created_at
                ):
                    latest[review.author] = review
            if any(review.state == "CHANGES_REQUESTED" for review in latest.values()):
                _finish(store, row, merge, "not-merged", "writer requested changes")
                continue
            try:
                merge_sha = client.merge_pull_request(repository, number, head)
            except Exception as error:
                # A lost response may follow an accepted merge. Read once before
                # recording a terminal rejection; never send another merge request.
                accepted_sha: str | None = None
                try:
                    current = client.get_pull_request(repository, number)
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
                    _finish(store, row, merge, "not-merged", f"GitHub rejected merge: {error}")
            else:
                _finish(store, row, merge, "merged", "merged automatically", merge_sha)
