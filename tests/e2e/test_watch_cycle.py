# pyright: reportPrivateUsage=false
"""Model-free watch journeys through the complete watch step."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from agent_factory.config import (
    CredentialsConfig,
    LimitsConfig,
    LocalConfig,
    RepositoryConfig,
    ScheduleConfig,
    SharedConfig,
    WatchConfig,
)
from agent_factory.github import IssueComment
from agent_factory.operations import Diagnostic
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import step
from agent_factory.watch import store as watch_store


class Comments:
    def __init__(self) -> None:
        self.records: list[IssueComment] = []

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return list(self.records)

    def create_comment(self, repository: str, number: int, body: str) -> str:
        identifier = str(len(self.records) + 1)
        self.records.append(IssueComment(identifier, body, "codagent-factory[bot]"))
        return identifier


def _local(tmp_path: Path) -> LocalConfig:
    return LocalConfig(
        tmp_path / "shared.toml",
        tmp_path,
        RepositoryConfig(tmp_path, tmp_path, tmp_path),
        ScheduleConfig.always(ZoneInfo("UTC"), 60),
        LimitsConfig(0, 1, 1, 1, 1),
        CredentialsConfig(tmp_path, tmp_path),
    )


def test_quiet_cycle_and_budget_zero_deliver_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = SharedConfig.from_file(Path("config/codagent.toml"))
        shared = replace(
            shared, watch=WatchConfig(True, "o/r", "claude:model:medium", daily_sessions=0)
        )
        client = Comments()

        def token() -> str:
            return "unused"

        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        assert watch_store.rows(store) == []
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "eval", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/evidence")
        store.finish_run(run.id, execution_status="failed", result={})
        store.set_setting("consumed-results", run.id, {"complete": True})
        old = datetime.now(UTC) - timedelta(minutes=8)
        cursor_time = (old - timedelta(minutes=1)).isoformat()
        store.set_setting(
            "watch", "cursor", {"enabled_at": cursor_time, "handled_up_to": cursor_time}
        )
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (old.isoformat(), run.id)
        )
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        rows = [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        assert len(rows) == 1
        assert rows[0]["state"] == "budget-exhausted"
        assert any(
            row["event_kind"] == "CLAIM" and row["state"] == "logged"
            for row in watch_store.rows(store)
        )
        assert len(client.records) == 1
        assert "No agent ran" in client.records[0].body
        assert "watch redispatch" in client.records[0].body
    finally:
        store.close()


def test_failure_session_completes_and_delivers_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    from typing import Any

    from agent_factory.watch import readiness, session

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium", grace_minutes=0),
        )
        client = Comments()
        claim = store.create_claim(ClaimDraft("o/r", 2, "I", "P", "eval", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/failure")
        store.finish_run(run.id, execution_status="failed", result={})
        store.set_setting("consumed-results", run.id, {"complete": True})
        old = datetime.now(UTC) - timedelta(minutes=1)
        cursor_time = (old - timedelta(minutes=1)).isoformat()
        store.set_setting(
            "watch", "cursor", {"enabled_at": cursor_time, "handled_up_to": cursor_time}
        )
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (old.isoformat(), run.id)
        )

        def watch_ready(*_args: object) -> list[Diagnostic]:
            return []

        monkeypatch.setattr(readiness, "diagnostics", watch_ready)

        def fake_start(
            _store: ClaimStore,
            row: dict[str, Any],
            _local: LocalConfig,
            _shared: SharedConfig,
            _config: Path,
            _token: object,
        ) -> dict[str, object]:
            evidence = Path(row["evidence_path"])
            evidence.mkdir(parents=True)
            (evidence / "exit.json").write_text('{"code":0}')
            (evidence / "watch-result.json").write_text(
                json.dumps(
                    {
                        "procedure": "triage",
                        "cause": "temporary outage",
                        "evidence": ["log line"],
                        "owner": "transient",
                        "retry": "automatic",
                        "actions": [],
                        "pull_request": None,
                        "paused_by_session": False,
                        "resumed_by_session": False,
                        "next_step": "observe retry",
                        "handoff": None,
                    }
                )
            )
            return {"pid": 999999, "start": "missing"}

        monkeypatch.setattr(session, "start", fake_start)

        def token() -> str:
            return "unused"

        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        failures = [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
        assert len(failures) == 1
        assert failures[0]["state"] == "completed"
        assert len(client.records) == 1
        assert "temporary outage" in client.records[0].body
        assert "Factory paused: no" in client.records[0].body
    finally:
        store.close()


def test_ready_pr_decisions_go_to_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    from typing import Any

    from agent_factory.watch import readiness, session

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium", operator="writer"),
        )
        client = Comments()
        claim = store.create_claim(ClaimDraft("o/r", 2, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/pr")
        pr = "https://github.com/o/r/pull/9"
        store.finish_run(
            run.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": pr}},
        )
        old = datetime.now(UTC) - timedelta(minutes=1)
        cursor_time = (old - timedelta(minutes=1)).isoformat()
        store.set_setting(
            "watch", "cursor", {"enabled_at": cursor_time, "handled_up_to": cursor_time}
        )
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (old.isoformat(), run.id)
        )

        def ready(*_args: object) -> list[Diagnostic]:
            return []

        monkeypatch.setattr(readiness, "diagnostics", ready)

        def fake_start(
            _store: ClaimStore,
            row: dict[str, Any],
            _local: LocalConfig,
            _shared: SharedConfig,
            _config: Path,
            _token: object,
        ) -> dict[str, object]:
            evidence = Path(row["evidence_path"])
            evidence.mkdir(parents=True)
            (evidence / "exit.json").write_text('{"code":0}')
            (evidence / "watch-result.json").write_text(
                json.dumps(
                    {
                        "procedure": "review",
                        "verdict": "commented",
                        "review_url": pr,
                        "issues_filed": [],
                        "decisions": [
                            {
                                "question": "Ship?",
                                "context": "Risk is low",
                                "options": [{"label": "yes", "consequence": "merge"}],
                                "recommendation": "yes",
                            }
                        ],
                    }
                )
            )
            return {"pid": 999999, "start": "missing"}

        monkeypatch.setattr(session, "start", fake_start)

        def token() -> str:
            return "unused"

        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        reviews = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert len(reviews) == 1
        assert reviews[0]["pr_number"] == 9
        assert reviews[0]["state"] == "completed"
        assert len(client.records) == 1
        assert "@writer" in client.records[0].body
        assert "Ship?" in client.records[0].body
    finally:
        store.close()


@pytest.mark.parametrize("url", [None, "https://example.com/not-a-pr"])
def test_ready_pr_without_parseable_url_is_logged_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str | None
) -> None:
    from agent_factory.watch import session

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium", daily_sessions=0),
        )
        claim = store.create_claim(ClaimDraft("o/r", 2, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/pr")
        store.finish_run(
            run.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": url}},
        )
        old = datetime.now(UTC) - timedelta(minutes=1)
        store.set_setting(
            "watch",
            "cursor",
            {
                "enabled_at": (old - timedelta(minutes=1)).isoformat(),
                "handled_up_to": old.isoformat(),
            },
        )
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (old.isoformat(), run.id)
        )

        def no_start(*_args: object) -> None:
            pytest.fail("session launched")

        monkeypatch.setattr(session, "start", no_start)
        client = Comments()
        step(store, client, shared, local, tmp_path / "local.toml", lambda: "unused")  # type: ignore[arg-type]
        ready = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert len(ready) == 1 and ready[0]["state"] == "logged"
        assert client.records == []
        assert watch_store.daily_count(store, local.schedule.timezone, datetime.now(UTC)) == 0
    finally:
        store.close()


def test_interrupted_host_result_after_failed_board_cycle_starts_one_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any

    from agent_factory.watch import readiness, session, supervise

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium", grace_minutes=0),
        )
        claim = store.create_claim(ClaimDraft("o/r", 8, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/pr")
        store.finish_run(run.id, execution_status="interrupted", result={})
        finished = datetime.now(UTC) - timedelta(minutes=5)
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (finished.isoformat(), run.id)
        )
        store.set_setting(
            "watch",
            "cursor",
            {
                "enabled_at": (finished - timedelta(minutes=1)).isoformat(),
                "handled_up_to": (finished - timedelta(minutes=1)).isoformat(),
            },
        )
        launches: list[str] = []

        def watch_ready(*_args: object) -> list[Diagnostic]:
            return []

        monkeypatch.setattr(readiness, "diagnostics", watch_ready)

        def fake_start(
            _store: ClaimStore,
            row: dict[str, Any],
            _local: LocalConfig,
            _shared: SharedConfig,
            _config: Path,
            _token: object,
        ) -> dict[str, object]:
            launches.append(row["id"])
            return {"pid": 999999, "start": "known"}

        monkeypatch.setattr(session, "start", fake_start)

        def alive(_identity: object) -> str:
            return "alive"

        monkeypatch.setattr(supervise, "process_identity_status", alive)
        client = Comments()

        def failed_cycle() -> None:
            try:
                raise RuntimeError("board unavailable before result consumption")
            finally:
                step(store, client, shared, local, tmp_path / "local.toml", lambda: "unused")  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="board unavailable"):
            failed_cycle()
        assert watch_store.cursor(store) is not None
        assert store.get_setting("consumed-results", run.id) is None
        assert launches == []

        store.normalize_terminal_result(
            run.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": "https://github.com/o/r/pull/8"}},
        )
        store.set_setting("consumed-results", run.id, {"complete": True})
        step(store, client, shared, local, tmp_path / "local.toml", lambda: "unused")  # type: ignore[arg-type]
        step(store, client, shared, local, tmp_path / "local.toml", lambda: "unused")  # type: ignore[arg-type]
        ready = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert len(ready) == 1
        assert ready[0]["state"] == "launched"
        assert launches == [ready[0]["id"]]
        assert not [row for row in watch_store.rows(store) if row["event_kind"] == "FAILURE"]
    finally:
        store.close()


def test_cycle_runs_watch_finally_after_board_failure(tmp_path: Path) -> None:
    from tests.e2e.test_factory_cycle import _cli, _setup

    config, _board, environment, _shared = _setup(tmp_path)
    marker = tmp_path / "watch-finally"
    before = f"""
from pathlib import Path
from agent_factory import watch
from agent_factory.github import GitHubClient
watch.step = lambda *args: Path({str(marker)!r}).write_text('ran')
def fail_board(*args):
    raise RuntimeError('board unavailable')
GitHubClient.validate_project = fail_board
"""
    _cli(config, environment, "tick", before_cli=before, expected_error="board unavailable")
    assert marker.read_text() == "ran"


def test_timeout_redispatch_disable_and_prune(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.watch import supervise

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(SharedConfig.from_file(Path("config/codagent.toml")), watch=WatchConfig())
        client = Comments()
        claim = store.create_claim(ClaimDraft("o/r", 4, "I", "P", "eval", "fp", {}))
        old = datetime.now(UTC) - timedelta(minutes=3)
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id=None,
            repository="o/r",
            issue_number=4,
            pr_number=None,
            pr_url=None,
            event_at=old.isoformat(),
            now=old.isoformat(),
        )
        row = watch_store.rows(store)[0]
        evidence = tmp_path / "artifacts" / "watch" / row["id"]
        evidence.mkdir(parents=True)
        assert watch_store.claim_launch(
            store, row["id"], old, 1, "claude:model:medium", str(evidence)
        )
        watch_store.update(store, row["id"], process_json='{"pid":999999,"start":"known"}')

        def alive(_identity: object) -> str:
            return "alive"

        def terminated(_identity: object) -> bool:
            return True

        monkeypatch.setattr(supervise, "process_identity_status", alive)
        monkeypatch.setattr(supervise, "terminate_owned_process", terminated)

        def token() -> str:
            return "unused"

        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        ended = watch_store.get(store, row["id"])
        assert ended is not None and ended["state"] == "timed-out"
        assert len(client.records) == 1
        assert "watch redispatch" in client.records[0].body
        new_id = watch_store.redispatch(store, row["id"])
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        assert watch_store.get(store, new_id)["state"] == "pending"  # type: ignore[index]
        assert watch_store.cursor(store) is None
        watch_store.update(
            store, row["id"], finished_at=(datetime.now(UTC) - timedelta(days=15)).isoformat()
        )
        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        assert not evidence.exists()
        assert watch_store.get(store, row["id"]) is not None
    finally:
        store.close()


def test_detection_error_preserves_cursor_and_still_delivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.watch import deliver, detect

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium"),
        )
        claim = store.create_claim(ClaimDraft("o/r", 3, "I", "P", "eval", "fp", {}))
        now = datetime.now(UTC).isoformat()
        store.set_setting("watch", "cursor", {"enabled_at": now, "handled_up_to": now})
        watch_store.insert(
            store,
            event_key="FAILURE:r",
            event_kind="FAILURE",
            claim_id=claim.id,
            run_id=None,
            repository="o/r",
            issue_number=3,
            pr_number=None,
            pr_url=None,
            event_at=now,
            now=now,
        )
        row = watch_store.rows(store)[0]
        deliver.queue(store, row, "alert", "earlier failure", "issue")
        client = Comments()

        def broken(*_args: object) -> None:
            raise RuntimeError("detection failed")

        monkeypatch.setattr(detect, "detect", broken)

        def token() -> str:
            return "unused"

        step(store, client, shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        assert watch_store.cursor(store) == {"enabled_at": now, "handled_up_to": now}
        assert len(client.records) == 1
        assert "earlier failure" in client.records[0].body
    finally:
        store.close()


def test_second_review_waits_for_same_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any

    from agent_factory.watch import readiness, session, supervise

    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        local = _local(tmp_path)
        shared = replace(
            SharedConfig.from_file(Path("config/codagent.toml")),
            watch=WatchConfig(True, "o/r", "claude:model:medium"),
        )
        claim = store.create_claim(ClaimDraft("o/r", 5, "I", "P", "fix", "fp", {}))
        pr = "https://github.com/o/r/pull/11"
        old = datetime.now(UTC) - timedelta(minutes=2)
        cursor_time = (old - timedelta(minutes=1)).isoformat()
        store.set_setting(
            "watch", "cursor", {"enabled_at": cursor_time, "handled_up_to": cursor_time}
        )

        def ready(*_args: object) -> list[Diagnostic]:
            return []

        def fake_start(
            _store: ClaimStore,
            _row: dict[str, Any],
            _local: LocalConfig,
            _shared: SharedConfig,
            _config: Path,
            _token: object,
        ) -> dict[str, object]:
            return {"pid": 999999, "start": "known"}

        def alive(_identity: object) -> str:
            return "alive"

        monkeypatch.setattr(readiness, "diagnostics", ready)
        monkeypatch.setattr(session, "start", fake_start)
        monkeypatch.setattr(supervise, "process_identity_status", alive)

        def token() -> str:
            return "unused"

        first = store.reserve_run(claim.id, "one", reason="initial", evidence_path="/tmp/pr")
        store.finish_run(
            first.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": pr}},
        )
        store._connection.execute(
            "UPDATE run SET finished_at=? WHERE id=?", (old.isoformat(), first.id)
        )
        step(store, Comments(), shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        first_rows = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert len(first_rows) == 1 and first_rows[0]["state"] == "launched"
        second = store.reserve_run(claim.id, "two", reason="review", evidence_path="/tmp/pr2")
        store.finish_run(
            second.id,
            execution_status="completed",
            result={"outcome": "pull-request", "pr": {"url": pr}},
        )
        step(store, Comments(), shared, local, tmp_path / "local.toml", token)  # type: ignore[arg-type]
        reviews = [row for row in watch_store.rows(store) if row["event_kind"] == "PR-READY"]
        assert [row["state"] for row in reviews] == ["launched", "pending"]
    finally:
        store.close()
