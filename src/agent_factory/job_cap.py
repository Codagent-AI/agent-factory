"""Factory-wide attempt cap episodes, holds, and operator notices."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime

from agent_factory.config import JobCapConfig
from agent_factory.controller import ReportingClient, advisory_lock
from agent_factory.github import GitHubApiError
from agent_factory.store import ClaimStore, JobCapState

logger = logging.getLogger(__name__)


def open_episode(store: ClaimStore, state: JobCapState, now: datetime) -> str:
    with store._transaction():  # pyright: ignore[reportPrivateUsage]
        saved = store.get_setting("job-cap", "episode")
        if saved is not None and isinstance(saved.get("id"), str):
            return str(saved["id"])
        episode = now.isoformat()
        store._connection.execute(  # pyright: ignore[reportPrivateUsage]
            "INSERT INTO settings(namespace, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
            ("job-cap", "episode", json.dumps({"id": episode, "opened_at": episode}), episode),
        )
    logger.info(
        "factory job cap reached: %s/%s attempts in the last %s h; earliest clear %s",
        state.count,
        state.attempts,
        state.window_hours,
        state.clears_at,
    )
    return episode


def observe(store: ClaimStore, state: JobCapState, now: datetime) -> str | None:
    if state.reached:
        return open_episode(store, state, now)
    with store._transaction():  # pyright: ignore[reportPrivateUsage]
        saved = store.get_setting("job-cap", "episode")
        if saved is None:
            return None
        episode = saved.get("id")
        store._connection.execute(  # pyright: ignore[reportPrivateUsage]
            "DELETE FROM settings WHERE namespace='job-cap' AND key='episode'"
        )
        for key, receipt in store.get_settings_by_prefix("job-cap-card", "").items():
            if receipt.get("episode") == episode:
                store._connection.execute(  # pyright: ignore[reportPrivateUsage]
                    "DELETE FROM settings WHERE namespace='job-cap-card' AND key=?", (key,)
                )
    logger.info("factory job cap clear: %s/%s attempts", state.count, state.attempts)
    return None


def notice_body(state: JobCapState) -> str:
    return (
        "Waiting: the factory job cap is reached "
        f"({state.count} of {state.attempts} attempts started in the last "
        f"{state.window_hours} hours). Earliest clear time: {state.clears_at}; "
        "`agent-factory status` shows the current value. To start held work sooner, "
        "run `agent-factory --config <local.toml> job-cap reset`, or raise "
        "`[job_cap] attempts` through a committed configuration change."
    )


def hold_claim(store: ClaimStore, claim_id: str, state: JobCapState, now: datetime) -> None:
    episode = open_episode(store, state, now)
    store.set_hold(claim_id, "job-cap", {"episode": episode})
    store.record_event(claim_id, f"job-cap:{episode}", notice_body(state))


def notify_card(
    store: ClaimStore,
    client: ReportingClient,
    bot_login: str,
    repository: str,
    issue: int,
    episode: str,
    state: JobCapState,
) -> None:
    key = f"{repository}:{issue}"
    with advisory_lock(store.path, f"job-cap-card-{hashlib.sha256(key.encode()).hexdigest()[:16]}"):
        receipt = store.get_setting("job-cap-card", key)
        if receipt is not None and receipt.get("episode") == episode and receipt.get("comment_id"):
            return
        base: dict[str, object] = {"episode": episode, "repository": repository, "issue": issue}
        marker = (
            f"<!-- agent-factory:job-cap:{hashlib.sha256(episode.encode()).hexdigest()[:16]} -->"
        )
        try:
            existing = next(
                (
                    comment
                    for comment in client.list_comment_records(repository, issue)
                    if comment.author == bot_login and marker in comment.body
                ),
                None,
            )
            comment_id = (
                existing.id
                if existing
                else client.create_comment(repository, issue, f"{marker}\n{notice_body(state)}")
            )
            store.set_setting(
                "job-cap-card", key, {**base, "comment_id": comment_id or "acknowledged"}
            )
        except (GitHubApiError, OSError) as error:
            store.set_setting("job-cap-card", key, {**base, "failure": str(error)})


def status_lines(store: ClaimStore, cap: JobCapConfig, now: datetime) -> list[str]:
    state = store.job_cap_state(now, cap)
    lines = [f"job cap: {state.count}/{state.attempts} attempts in the last {state.window_hours} h"]
    if not state.reached:
        return lines
    lines.append(
        f"job cap: reached; earliest clear {state.clears_at}; "
        "reset: agent-factory --config <local.toml> job-cap reset"
    )
    episode = store.get_setting("job-cap", "episode")
    if episode is not None:
        for receipt in store.get_settings_by_prefix("job-cap-card", "").values():
            if receipt.get("episode") == episode.get("id"):
                lines.append(f"job cap waiting: {receipt['repository']}#{receipt['issue']}")
    return lines
