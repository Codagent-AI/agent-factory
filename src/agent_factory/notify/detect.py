# pyright: reportPrivateUsage=false
"""Detect durable stops from claim history and observed Project state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from agent_factory.github import IssuePresence, ProjectQueueItem
from agent_factory.notify import store as records
from agent_factory.watch import store as watch_store
from agent_factory.watch.detect import _FAILURES, _is_failure, _nested_url
from agent_factory.work_kinds.base import card_status

if TYPE_CHECKING:
    from agent_factory.config import SharedConfig
    from agent_factory.github import GitHubClient
    from agent_factory.store import Claim, ClaimStore, Run


def classify(
    claim: Claim,
    run: Run,
    card: ProjectQueueItem | IssuePresence,
    shared: SharedConfig | None = None,
) -> str | None:
    if claim.outcome.get("waiting_review"):
        return None
    if isinstance(card, ProjectQueueItem):
        source = card.source
        on_project = True
        owned = (
            (card.fields.get(shared.project.owner.id) == shared.project.owner.option("factory"))
            if shared
            else False
        )
        queued = (card_status(shared, card) == "Ready") if shared else False
    else:
        source = card
        on_project = card.on_project
        owned = False
        queued = False
    if source.state.lower() == "open" and owned and queued and "needs-input" not in source.labels:
        return None
    if claim.lifecycle in {"cancelled", "superseded"}:
        return "cancelled"
    outcome = run.result.get("outcome") or claim.outcome.get("outcome")
    if claim.lifecycle in {"settled", "blocked"}:
        if run.status in _FAILURES or outcome == "failed":
            return "failed"
        if outcome == "needs-input":
            return "needs-input"
        if outcome == "pull-request":
            return "pull-request"
    if claim.lifecycle == "settled" and claim.kind == "eval":
        return "settled"
    if not on_project or source.state.lower() != "open" or not owned:
        return "not-queued"
    return None


def _watch_gate(
    store: ClaimStore, run: Run, kind: str, shared: SharedConfig, now: datetime
) -> tuple[bool, str]:
    if not shared.watch.enabled:
        return True, ""
    expected = (
        _is_failure({"status": run.status, "kind": run.kind}, run.result) or kind == "pull-request"
    )
    if not expected:
        return True, ""
    dispatches = [r for r in watch_store.rows(store) if r["run_id"] == run.id]
    if any(r["state"] in watch_store.ENDED | {"logged"} for r in dispatches):
        return True, ""
    if any(r["state"] == "launched" for r in dispatches):
        return False, ""
    if run.finished_at and now - datetime.fromisoformat(run.finished_at) < timedelta(
        minutes=shared.notify.watch_wait_minutes
    ):
        return False, ""
    return True, "watch dispatch waiting" if any(
        r["state"] == "pending" for r in dispatches
    ) else "watch event missed"


def detect(
    store: ClaimStore,
    client: GitHubClient,
    shared: SharedConfig,
    cards: list[ProjectQueueItem],
    bodies: dict[tuple[str, int], str],
    now: datetime | None = None,
) -> None:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cursor = records.cursor(store)
    if cursor is None:
        return
    enabled = datetime.fromisoformat(str(cursor["enabled_at"]))
    card_by_issue = {(card.source.repository, card.source.number): card for card in cards}
    known: dict[tuple[str, int], IssuePresence | None] = {}
    candidates: list[tuple[Claim, Run]] = []
    latest: dict[tuple[str, int], Claim] = {}
    creation = {
        row["id"]: row["created_at"]
        for row in store._connection.execute("SELECT id,created_at FROM claim")
    }
    run_creation = {
        row["id"]: row["created_at"]
        for row in store._connection.execute("SELECT id,created_at FROM run")
    }
    for claim in store.all_claims():
        key = (claim.repository, claim.issue_number)
        if key not in latest or creation[claim.id] > creation[latest[key].id]:
            latest[key] = claim
    for claim in latest.values():
        runs = store.runs_for_claim(claim.id)
        if not runs or any(run.finished_at is None for run in runs):
            continue
        run = max(runs, key=lambda r: run_creation[r.id])
        finished = datetime.fromisoformat(run.finished_at or "")
        if enabled < finished <= now and finished >= now - timedelta(days=7):
            candidates.append((claim, run))
    candidate_ids = {(claim.id, run.id) for claim, run in candidates}
    for row in records.rows(store, "settling"):
        if (row["claim_id"], row["run_id"]) not in candidate_ids:
            store._connection.execute(
                "DELETE FROM notify_stop WHERE id=? AND state='settling'", (row["id"],)
            )
    for claim, run in candidates:
        key = (claim.repository, claim.issue_number)
        previous = [
            r for r in records.rows(store) if r["claim_id"] == claim.id and r["run_id"] == run.id
        ]
        card: ProjectQueueItem | IssuePresence | None = card_by_issue.get(key)
        if card is None:
            likely_kind = classify(
                claim, run, IssuePresence("closed", frozenset(), "", False), shared
            )
            if likely_kind and any(
                r["stop_kind"] == likely_kind and r["state"] in {"ended", "launched"}
                for r in previous
            ):
                continue
            if key not in known:
                try:
                    known[key] = client.get_issue_presence(*key, shared.project.id)
                except Exception:
                    known[key] = None
            card = known[key]
            if card is None or card.on_project:
                for row in previous:
                    if row["state"] == "settling":
                        records.update(store, row["id"], "settling", restart_settle=1)
                continue
        kind = classify(claim, run, card, shared)
        # A settling row whose cause no longer holds is dropped, so only the current stop
        # can reach delivery.
        for row in previous:
            if row["state"] == "settling" and row["stop_kind"] != kind:
                store._connection.execute("DELETE FROM notify_stop WHERE id=?", (row["id"],))
        if kind is None:
            continue
        if any(r["stop_kind"] == kind and r["state"] in {"launched", "ended"} for r in previous):
            continue
        pr_url = _nested_url(run.result) or _nested_url(claim.outcome)
        records.insert(
            store,
            claim.id,
            run.id,
            claim.repository,
            claim.issue_number,
            claim.kind,
            kind,
            now,
            pr_url,
        )
        row = next(
            r
            for r in records.rows(store, "settling")
            if r["claim_id"] == claim.id and r["run_id"] == run.id and r["stop_kind"] == kind
        )
        if row["restart_settle"]:
            records.update(
                store, row["id"], "settling", restart_settle=0, stopped_since=now.isoformat()
            )
            row["stopped_since"] = now.isoformat()
        gate, note = _watch_gate(store, run, kind, shared, now)
        if gate and now - datetime.fromisoformat(row["stopped_since"]) >= timedelta(
            seconds=shared.notify.settle_seconds
        ):
            records.update(store, row["id"], "settling", watch_note=note)
            # Only a settled stop gets a body; delivery skips every row without one.
            bodies[key] = card.source.body if isinstance(card, ProjectQueueItem) else card.body
