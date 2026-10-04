# pyright: reportPrivateUsage=false
"""INT-004: stop classification and deduplication on the real store."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.config import NotifyConfig, SharedConfig, WatchConfig
from agent_factory.github import GitHubClient, IssuePresence, ProjectQueueItem
from agent_factory.notify import begin, detect
from agent_factory.notify import store as records
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import store as watch_store


class NoReads:
    def get_issue_presence(self, repository: str, number: int, project_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError("snapshot issue must not be fetched")


def _card(shared: SharedConfig, status: str, body: str = "") -> ProjectQueueItem:
    return ProjectQueueItem(
        "P",
        "I",
        {
            shared.project.owner.id: shared.project.owner.option("factory"),
            shared.project.status.id: shared.project.status.option(status),
        },
        SourceItem("I", "o/r", 12, "author", frozenset(), "Bug", "open", body),
    )


def test_settle_queued_outage_and_one_stop(tmp_path: Path) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(
        shared,
        notify=NotifyConfig(enabled=True, agent="claude:fake:low", settle_seconds=60),
        watch=WatchConfig(),
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    client = cast(GitHubClient, NoReads())
    try:
        begin(store, shared)
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        store.finish_run(run.id, execution_status="completed", result={"outcome": "pull-request"})
        store.set_claim_lifecycle(claim.id, "settled", {"outcome": "pull-request"})
        now = datetime.now(UTC)
        bodies: dict[tuple[str, int], str] = {}
        detect.detect(store, client, shared, [_card(shared, "ready")], bodies, now)
        assert records.rows(store) == []
        detect.detect(store, client, shared, [_card(shared, "review")], bodies, now)
        row = records.rows(store)[0]
        assert row["stop_kind"] == "pull-request"
        assert bodies == {}
        records.restart_settling(store)
        detect.detect(
            store, client, shared, [_card(shared, "review")], bodies, now + timedelta(minutes=2)
        )
        assert records.rows(store)[0]["stopped_since"] == (now + timedelta(minutes=2)).isoformat()
        assert bodies == {}
        detect.detect(
            store, client, shared, [_card(shared, "review")], bodies, now + timedelta(minutes=4)
        )
        assert bodies == {("o/r", 12): ""}
        records.end(store, row["id"], "settling", "unmarked")
        detect.detect(
            store, client, shared, [_card(shared, "review")], bodies, now + timedelta(minutes=5)
        )
        assert len(records.rows(store)) == 1
    finally:
        store.close()


def test_absent_issue_must_be_verified_and_outage_restarts_settle(tmp_path: Path) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(
        shared,
        notify=NotifyConfig(enabled=True, agent="claude:fake:low", settle_seconds=60),
        watch=WatchConfig(),
    )
    store = ClaimStore(tmp_path / "state.sqlite3")

    class Presence:
        def __init__(self) -> None:
            self.result: IssuePresence | None = None
            self.calls = 0

        def get_issue_presence(
            self, repository: str, number: int, project_id: str
        ) -> IssuePresence | None:
            self.calls += 1
            if self.result is None:
                raise RuntimeError("GitHub unavailable")
            return self.result

    client = Presence()
    try:
        begin(store, shared)
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        store.finish_run(run.id, execution_status="completed", result={})
        now = datetime.now(UTC)
        bodies: dict[tuple[str, int], str] = {}
        detect.detect(store, cast(GitHubClient, client), shared, [], bodies, now)
        assert not records.rows(store)
        client.result = IssuePresence("OPEN", frozenset(), "marked", True)
        detect.detect(store, cast(GitHubClient, client), shared, [], bodies, now)
        assert not records.rows(store)
        client.result = IssuePresence("OPEN", frozenset(), "marked", False)
        detect.detect(store, cast(GitHubClient, client), shared, [], bodies, now)
        row = records.rows(store)[0]
        assert row["stop_kind"] == "not-queued"
        client.result = None
        detect.detect(
            store, cast(GitHubClient, client), shared, [], bodies, now + timedelta(minutes=2)
        )
        assert records.rows(store)[0]["restart_settle"] == 1
        client.result = IssuePresence("OPEN", frozenset(), "marked", False)
        detect.detect(
            store, cast(GitHubClient, client), shared, [], bodies, now + timedelta(minutes=4)
        )
        assert bodies == {}
        detect.detect(
            store, cast(GitHubClient, client), shared, [], bodies, now + timedelta(minutes=6)
        )
        assert bodies == {("o/r", 12): "marked"}
        records.end(store, row["id"], "settling", "unmarked")
        calls = client.calls
        detect.detect(
            store, cast(GitHubClient, client), shared, [], bodies, now + timedelta(minutes=7)
        )
        assert client.calls == calls
    finally:
        store.close()


def test_watch_gate_waits_then_records_pending_note(tmp_path: Path) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(
        shared,
        notify=NotifyConfig(
            enabled=True, agent="claude:fake:low", settle_seconds=0, watch_wait_minutes=2
        ),
        watch=replace(shared.watch, enabled=True),
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        begin(store, shared)
        claim = store.create_claim(ClaimDraft("o/r", 12, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        store.finish_run(run.id, execution_status="failed", result={})
        store.set_claim_lifecycle(claim.id, "settled", {"outcome": "failed"})
        now = datetime.now(UTC)
        watch_store.insert(
            store,
            event_key=f"FAILURE:{run.id}",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id=run.id,
            repository="o/r",
            issue_number=12,
            pr_number=None,
            pr_url=None,
            event_at=now.isoformat(),
            now=now.isoformat(),
        )
        bodies: dict[tuple[str, int], str] = {}
        detect.detect(
            store,
            cast(GitHubClient, NoReads()),
            shared,
            [_card(shared, "review", "marked")],
            bodies,
            now,
        )
        assert bodies == {}
        detect.detect(
            store,
            cast(GitHubClient, NoReads()),
            shared,
            [_card(shared, "review", "marked")],
            bodies,
            now + timedelta(minutes=3),
        )
        assert bodies == {("o/r", 12): "marked"}
        assert records.rows(store)[0]["watch_note"] == "watch dispatch waiting"
    finally:
        store.close()
