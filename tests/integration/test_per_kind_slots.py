"""INT-001: schema v3->v4 migration and per-kind execution slots."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import cast

import pytest

from agent_factory.store import ClaimDraft, ClaimStore, NonterminalRunError


def _write_v3_database(path: Path) -> str:
    """Build a v3 database with settled/waiting claims, progress, a pause, and a quota hold."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE claim (
            id TEXT PRIMARY KEY,
            repository TEXT NOT NULL,
            issue_number INTEGER NOT NULL,
            issue_id TEXT NOT NULL,
            project_item_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            frozen_spec_json TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            outcome_json TEXT NOT NULL,
            preparation_json TEXT NOT NULL,
            reporting_json TEXT NOT NULL,
            cleanup_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE run (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL REFERENCES claim(id),
            unit_key TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL,
            launch_nonce TEXT NOT NULL,
            supervisor_json TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            progress_json TEXT NOT NULL,
            cancellation_requested INTEGER NOT NULL DEFAULT 0,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(claim_id, unit_key, attempt_number)
        );
        CREATE UNIQUE INDEX one_nonterminal_run
            ON run((CASE WHEN status IN ('reserved', 'running', 'observing') THEN 1 END))
            WHERE status IN ('reserved', 'running', 'observing');
        CREATE TABLE settings (
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(namespace, key)
        );
        PRAGMA user_version = 3;
        COMMIT;
        """
    )
    settled_id = "claim-settled"
    waiting_id = "claim-waiting"
    connection.execute(
        "INSERT INTO claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            settled_id,
            "example/evals",
            1,
            "I1",
            "P1",
            "eval",
            "fp-1",
            "{}",
            "settled",
            "{}",
            "{}",
            "{}",
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO claim VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            waiting_id,
            "example/evals",
            2,
            "I2",
            "P2",
            "eval",
            "fp-2",
            "{}",
            "waiting",
            "{}",
            "{}",
            '{"events": {"retry": {"body": "retrying", "comment_id": null}}}',
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "run-settled",
            settled_id,
            "rep-1",
            0,
            "initial",
            "completed",
            "nonce-1",
            "{}",
            "{}",
            "/evidence/settled",
            "{}",
            0,
            '{"score": 61}',
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "run-recovered",
            waiting_id,
            "rep-1",
            0,
            "initial",
            "failed",
            "nonce-2",
            "{}",
            "{}",
            "/evidence/waiting-0",
            "{}",
            0,
            '{"failure": {"owner": "harness"}}',
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "run-recovery",
            waiting_id,
            "rep-1",
            1,
            "recovery",
            "failed",
            "nonce-3",
            "{}",
            "{}",
            "/evidence/waiting-0",
            "{}",
            0,
            '{"failure": {"owner": "harness"}}',
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
        ),
    )
    connection.execute(
        "INSERT INTO settings VALUES "
        "('control','pause','{\"paused\": true}','2026-01-01T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO settings VALUES "
        "('admission','quota','{\"until\": \"2026-06-01T00:00:00+00:00\"}',"
        "'2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()
    return waiting_id


def test_v3_database_migrates_to_per_kind_slots_in_one_transaction(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    waiting_id = _write_v3_database(database)
    backup = tmp_path / "state.sqlite3.v3.bak"

    store = ClaimStore(database)

    raw = sqlite3.connect(database)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
    raw.close()
    assert backup.exists()
    backup_raw = sqlite3.connect(backup)
    assert backup_raw.execute("PRAGMA user_version").fetchone()[0] == 3
    backup_raw.close()

    claims = store.all_claims()
    assert {claim.id for claim in claims} == {"claim-settled", waiting_id}
    settled = store.get_claim("claim-settled")
    assert settled is not None and settled.lifecycle == "settled"
    waiting = store.get_claim(waiting_id)
    assert waiting is not None
    events = cast(dict[str, dict[str, object]], waiting.reporting["events"])
    assert events["retry"]["body"] == "retrying"
    assert store.recovery_attempts(waiting_id, "rep-1") == 1

    runs = store.runs_for_claim("claim-settled")
    assert runs[0].result == {"score": 61}
    assert store.is_paused() is True
    assert store.get_setting("admission", "quota:codex") == {
        "until": "2026-06-01T00:00:00+00:00"
    }
    assert store.get_setting("admission", "quota") is None

    for run in store.runs_for_claim("claim-settled") + store.runs_for_claim(waiting_id):
        assert run.kind == "eval"

    store.close()


def test_migration_failure_leaves_database_at_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory import store as store_module

    database = tmp_path / "state.sqlite3"
    _write_v3_database(database)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("simulated backup failure mid-migration")

    monkeypatch.setattr(store_module.shutil, "copy2", boom)

    with pytest.raises(OSError, match="simulated backup failure"):
        ClaimStore(database)

    raw = sqlite3.connect(database)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 3
    raw.close()
    assert not (tmp_path / "state.sqlite3.v3.bak").exists()


def test_newer_schema_version_is_refused(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    ClaimStore(database).close()
    raw = sqlite3.connect(database)
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()

    with pytest.raises(RuntimeError, match="newer"):
        ClaimStore(database)


def test_fresh_database_creates_per_kind_index_directly(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    raw = sqlite3.connect(tmp_path / "state.sqlite3")
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 4
    index_names = {
        row[0]
        for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }
    assert "one_nonterminal_run_per_kind" in index_names
    assert "one_nonterminal_run" not in index_names
    raw.close()
    store.close()


def frozen_spec() -> dict[str, object]:
    return {"version": 1, "settings": {"repetitions": 1}}


def test_reserve_run_enforces_one_nonterminal_run_per_kind(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    eval_claim = store.create_claim(
        ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp-1", frozen_spec())
    )
    other_claim = store.create_claim(
        ClaimDraft("example/evals", 2, "I2", "P2", "other", "fp-2", frozen_spec())
    )

    eval_run = store.reserve_run(eval_claim.id, "rep-1", reason="initial", evidence_path="/e1")
    other_run = store.reserve_run(other_claim.id, "fix", reason="initial", evidence_path="/e2")
    assert eval_run.kind == "eval"
    assert other_run.kind == "other"

    with pytest.raises(NonterminalRunError):
        store.reserve_run(eval_claim.id, "rep-2", reason="initial", evidence_path="/e3")
    with pytest.raises(NonterminalRunError):
        store.reserve_run(other_claim.id, "fix", reason="recovery", evidence_path="/e4")

    assert {run.id for run in store.nonterminal_runs(kind="eval")} == {eval_run.id}
    assert {run.id for run in store.nonterminal_runs(kind="other")} == {other_run.id}
    assert {run.id for run in store.nonterminal_runs()} == {eval_run.id, other_run.id}

    store.finish_run(other_run.id, execution_status="blocked", result={"reason": "blocked"})
    assert store.nonterminal_runs(kind="other") == []
    freed = store.reserve_run(other_claim.id, "fix", reason="unblock", evidence_path="/e5")
    assert freed.kind == "other"
    store.close()
