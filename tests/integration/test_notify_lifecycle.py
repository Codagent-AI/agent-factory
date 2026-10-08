# pyright: reportPrivateUsage=false
"""Enablement, repeated stops, explanation links, disabling, and pruning on the real store."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, NotifyConfig, SharedConfig, WatchConfig
from agent_factory.github import GitHubClient, ProjectQueueItem
from agent_factory.notify import begin, detect, step
from agent_factory.notify import store as records
from agent_factory.notify.deliver import _details, render
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore
from tests.integration.test_fix_config import _LOCAL_BASE


class NoReads:
    def get_issue_presence(self, repository: str, number: int, project_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError("snapshot issue must not be fetched")


def _shared(enabled: bool = True) -> SharedConfig:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    return replace(
        shared,
        notify=NotifyConfig(enabled=enabled, agent="claude:fake:low", settle_seconds=0),
        watch=WatchConfig(),
    )


def _card(shared: SharedConfig, status: str) -> ProjectQueueItem:
    return ProjectQueueItem(
        "P",
        "I",
        {
            shared.project.owner.id: shared.project.owner.option("factory"),
            shared.project.status.id: shared.project.status.option(status),
        },
        SourceItem("I", "o/r", 12, "author", frozenset(), "Bug", "open", "body"),
    )


def _enable_at(store: ClaimStore, when: datetime) -> None:
    store.set_setting("notify", "cursor", {"enabled_at": when.isoformat()})


def _settle_pr(store: ClaimStore, claim_id: str, reason: str) -> str:
    run = store.reserve_run(claim_id, "one", reason=reason, evidence_path="/tmp/evidence")
    store.finish_run(run.id, execution_status="completed", result={"outcome": "pull-request"})
    store.set_claim_lifecycle(claim_id, "settled", {"outcome": "pull-request"})
    return run.id


def test_work_from_before_enablement_and_a_disabled_period_is_skipped(tmp_path: Path) -> None:
    shared = _shared()
    store = ClaimStore(tmp_path / "state.sqlite3")
    client = cast(GitHubClient, NoReads())
    try:
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        _settle_pr(store, claim.id, "initial")
        now = datetime.now(UTC) + timedelta(seconds=1)
        _enable_at(store, now)
        bodies: dict[tuple[str, int], str] = {}
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        assert records.rows(store) == []
        assert bodies == {}
        # Disabling clears the cursor; re-enabling starts a new one at the current time.
        records.disable(store)
        assert records.cursor(store) is None
        begin(store, shared)
        cursor = records.cursor(store)
        assert cursor is not None
        later = datetime.fromisoformat(str(cursor["enabled_at"])) + timedelta(seconds=1)
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, later)
        assert records.rows(store) == []
    finally:
        store.close()


def test_a_second_run_after_a_review_round_is_a_new_stop(tmp_path: Path) -> None:
    shared = _shared()
    store = ClaimStore(tmp_path / "state.sqlite3")
    client = cast(GitHubClient, NoReads())
    try:
        _enable_at(store, datetime.now(UTC) - timedelta(minutes=1))
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        first = _settle_pr(store, claim.id, "initial")
        bodies: dict[tuple[str, int], str] = {}
        now = datetime.now(UTC) + timedelta(seconds=1)
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        [row] = records.rows(store)
        assert row["run_id"] == first
        assert bodies == {("o/r", 12): "body"}
        records.end(store, row["id"], "settling", "sent")
        bodies.clear()
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        assert bodies == {}
        assert len(records.rows(store)) == 1
        store.set_claim_lifecycle(claim.id, "active", {})
        second = _settle_pr(store, claim.id, "review")
        now = datetime.now(UTC) + timedelta(seconds=1)
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        settling = records.rows(store, "settling")
        assert [r["run_id"] for r in settling] == [second]
        assert bodies == {("o/r", 12): "body"}
    finally:
        store.close()


def test_a_stale_settling_stop_never_reaches_delivery(tmp_path: Path) -> None:
    """A row whose cause changed is dropped even when the current kind was already notified."""
    shared = _shared()
    store = ClaimStore(tmp_path / "state.sqlite3")
    client = cast(GitHubClient, NoReads())
    try:
        _enable_at(store, datetime.now(UTC) - timedelta(minutes=1))
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        run = _settle_pr(store, claim.id, "initial")
        now = datetime.now(UTC) + timedelta(seconds=1)
        records.insert(store, claim.id, run, "o/r", 12, "fix", "pull-request", now)
        [notified] = records.rows(store)
        records.end(store, notified["id"], "settling", "sent")
        records.insert(store, claim.id, run, "o/r", 12, "fix", "not-queued", now)
        bodies: dict[tuple[str, int], str] = {}
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        assert records.rows(store, "settling") == []
        assert bodies == {}
    finally:
        store.close()


def test_details_link_only_the_runs_own_reporting_comment(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "feature", "fp", {}))
        old = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/e")
        store.finish_run(old.id, execution_status="completed", result={"outcome": "needs-input"})
        run = store.reserve_run(claim.id, "one", reason="unblock", evidence_path="/tmp/e")
        store.finish_run(run.id, execution_status="completed", result={"outcome": "needs-input"})
        store._set_reporting(
            claim.id,
            {
                "events": {
                    f"{old.id}:needs-input": {"comment_id": 100},
                    f"{run.id}:needs-input": {"comment_id": 200},
                    f"{run.id}:watch-triage": {"comment_id": 300},
                }
            },
        )
        claim = store.get_claim(claim.id)
        current = store.get_run(run.id)
        assert claim is not None and current is not None
        link = _details(claim, current, "needs-input")
        assert link == "https://github.com/o/r/issues/12#issuecomment-200"
        message = render({"stop_kind": "needs-input"}, claim, None, link)
        assert message.splitlines()[-1] == f"Details: {link}"
        assert _details(claim, current, "pull-request") is None
        store._set_reporting(claim.id, {"events": {f"{run.id}:watch-triage": {"comment_id": 3}}})
        claim = store.get_claim(claim.id)
        assert claim is not None
        assert _details(claim, current, "needs-input") is None
    finally:
        store.close()


def test_disable_keeps_supervising_and_prune_keeps_eight_days(tmp_path: Path) -> None:
    local = replace(
        LocalConfig.from_toml(_LOCAL_BASE),
        storage_root=tmp_path / "factory",
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        _enable_at(store, datetime.now(UTC) - timedelta(minutes=1))
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        run = _settle_pr(store, claim.id, "initial")
        now = datetime.now(UTC)
        records.insert(store, claim.id, run, "o/r", 12, "fix", "pull-request", now)
        [row] = records.rows(store)
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        (evidence / "exit.json").write_text('{"code": 0}')
        (evidence / "stdout.json").write_text(
            json.dumps(
                {"is_error": False, "structured_output": {"outcome": "sent", "detail": "ok"}}
            )
        )
        assert records.update(
            store,
            row["id"],
            "settling",
            state="launched",
            launched_at=now.isoformat(),
            deadline_at=(now + timedelta(minutes=5)).isoformat(),
            evidence_path=str(evidence),
        )
        records.insert(store, claim.id, "other-run", "o/r", 12, "fix", "failed", now)
        client = cast(GitHubClient, NoReads())
        step(store, client, _shared(enabled=False), local, [_card(_shared(), "review")])
        assert records.cursor(store) is None
        [ended] = records.rows(store)
        assert ended["state"] == "ended" and ended["outcome"] == "sent"
        retention = max(local.limits.evidence_retention_days, 8)
        finished = now - timedelta(days=retention) + timedelta(minutes=1)
        store._connection.execute(
            "UPDATE notify_stop SET finished_at=? WHERE id=?", (finished.isoformat(), row["id"])
        )
        records.prune(store, local)
        assert len(records.rows(store)) == 1 and evidence.is_dir()
        store._connection.execute(
            "UPDATE notify_stop SET finished_at=? WHERE id=?",
            ((finished - timedelta(minutes=2)).isoformat(), row["id"]),
        )
        records.prune(store, local)
        assert records.rows(store) == [] and not evidence.exists()
    finally:
        store.close()
