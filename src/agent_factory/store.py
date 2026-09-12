"""Durable, small SQLite persistence for factory claims and attempts.

The store deliberately contains no GitHub or suite calls.  Every method performs
one short transaction, which keeps controller and supervisor ownership separate.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 4
NONTERMINAL_RUN_STATUSES = frozenset({"reserved", "running", "observing"})


class NonterminalRunError(RuntimeError):
    """A run is already reserving the factory's one execution slot."""


class RunTransitionError(RuntimeError):
    """A supervisor attempted a run transition from an invalid prior state."""


@dataclass(frozen=True)
class ClaimDraft:
    repository: str
    issue_number: int
    issue_id: str
    project_item_id: str
    kind: str
    request_fingerprint: str
    frozen_spec: Mapping[str, object]


@dataclass(frozen=True)
class Claim:
    id: str
    repository: str
    issue_number: int
    issue_id: str
    project_item_id: str
    kind: str
    request_fingerprint: str
    frozen_spec: dict[str, object]
    lifecycle: str
    outcome: dict[str, object]
    preparation: dict[str, object]
    reporting: dict[str, object]
    cleanup: dict[str, object]


@dataclass(frozen=True)
class Run:
    id: str
    claim_id: str
    unit_key: str
    attempt_number: int
    reason: str
    status: str
    kind: str
    evidence_path: str
    result: dict[str, object]
    launch_nonce: str
    supervisor: dict[str, object]
    plan: dict[str, object]
    progress: dict[str, object]
    cancellation_requested: bool
    started_at: str | None
    finished_at: str | None

    @property
    def process(self) -> dict[str, object]:
        """The recorded suite identity, kept separately from its watcher."""
        value = self.supervisor.get("process")
        return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class Event:
    key: str
    body: str
    comment_id: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeError("factory database contains an invalid JSON object")
    return cast(dict[str, object], parsed)


class ClaimStore:
    """SQLite claim history with explicit controller/supervisor write boundaries."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        version = cast(int, self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("factory database is newer than this controller")
        if version == SCHEMA_VERSION:
            return
        if version in {1, 2}:
            # Older state files did not necessarily materialize the JSON columns
            # later reserved for preparation and cleanup.  Add them in place so
            # existing claims remain schedulable after an upgrade.
            columns = {
                cast(str, row[1]) for row in self._connection.execute("PRAGMA table_info(claim)")
            }
            with self._transaction():
                if "preparation_json" not in columns:
                    self._connection.execute(
                        "ALTER TABLE claim ADD COLUMN preparation_json TEXT NOT NULL DEFAULT '{}'"
                    )
                if "cleanup_json" not in columns:
                    self._connection.execute(
                        "ALTER TABLE claim ADD COLUMN cleanup_json TEXT NOT NULL DEFAULT '{}'"
                    )
                self._connection.execute("PRAGMA user_version = 3")
            self._migrate()
            return
        if version == 3:
            backup = self.path.with_name(self.path.name + ".v3.bak")
            if not backup.exists():
                self._connection.execute("PRAGMA wal_checkpoint(FULL)")
                shutil.copy2(self.path, backup)
            self._connection.executescript(
                """
                    BEGIN IMMEDIATE;
                    ALTER TABLE run ADD COLUMN kind TEXT NOT NULL DEFAULT 'eval';
                    UPDATE run SET kind = (
                        SELECT kind FROM claim WHERE claim.id = run.claim_id
                    );
                    DROP INDEX one_nonterminal_run;
                    CREATE UNIQUE INDEX one_nonterminal_run_per_kind
                        ON run(kind) WHERE status IN ('reserved', 'running', 'observing');
                    INSERT OR REPLACE INTO settings(namespace, key, value_json, updated_at)
                        SELECT namespace, 'quota:codex', value_json, updated_at FROM settings
                        WHERE namespace = 'admission' AND key = 'quota';
                    DELETE FROM settings WHERE namespace = 'admission' AND key = 'quota';
                    PRAGMA user_version = 4;
                    COMMIT;
                    """
            )
            return
        self._connection.executescript(
            f"""
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
                    kind TEXT NOT NULL,
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
                CREATE UNIQUE INDEX one_nonterminal_run_per_kind
                    ON run(kind) WHERE status IN ('reserved', 'running', 'observing');
                CREATE TABLE settings (
                    namespace TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, key)
                );
                PRAGMA user_version = {SCHEMA_VERSION};
                COMMIT;
                """
        )

    @contextmanager
    def _transaction(self) -> Generator[None, None, None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def create_claim(self, draft: ClaimDraft) -> Claim:
        claim_id = str(uuid.uuid4())
        with self._transaction():
            self._insert_claim(draft, claim_id)
        claim = self.get_claim(claim_id)
        if claim is None:  # pragma: no cover - SQLite INSERT is synchronous
            raise RuntimeError("new claim was not persisted")
        return claim

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self._connection.execute("SELECT * FROM claim WHERE id = ?", (claim_id,)).fetchone()
        return _claim(row) if row is not None else None

    def claims_for_item(self, project_item_id: str) -> list[Claim]:
        rows = self._connection.execute(
            "SELECT * FROM claim WHERE project_item_id = ? ORDER BY created_at", (project_item_id,)
        ).fetchall()
        return [_claim(row) for row in rows]

    def all_claims(self) -> list[Claim]:
        """Return saved claims for read-only operational reporting."""
        rows = self._connection.execute("SELECT * FROM claim ORDER BY created_at").fetchall()
        return [_claim(row) for row in rows]

    def set_claim_lifecycle(
        self, claim_id: str, lifecycle: str, outcome: Mapping[str, object]
    ) -> None:
        with self._transaction():
            self._connection.execute(
                "UPDATE claim SET lifecycle = ?, outcome_json = ?, updated_at = ? WHERE id = ?",
                (lifecycle, _dump(outcome), _now(), claim_id),
            )

    def set_preparation(self, claim_id: str, preparation: Mapping[str, object]) -> None:
        """Persist owned preparation references before external work begins."""
        with self._transaction():
            self._connection.execute(
                "UPDATE claim SET preparation_json = ?, updated_at = ? WHERE id = ?",
                (_dump(preparation), _now(), claim_id),
            )

    def set_cleanup(self, claim_id: str, cleanup: Mapping[str, object]) -> None:
        """Persist worktree cleanup progress independently from suite evidence."""
        with self._transaction():
            self._connection.execute(
                "UPDATE claim SET cleanup_json = ?, updated_at = ? WHERE id = ?",
                (_dump(cleanup), _now(), claim_id),
            )

    def supersede_and_create(self, claim_id: str, draft: ClaimDraft) -> Claim:
        replacement_id = str(uuid.uuid4())
        with self._transaction():
            active = self._connection.execute(
                "SELECT 1 FROM run WHERE claim_id = ? "
                "AND status IN ('reserved', 'running', 'observing')",
                (claim_id,),
            ).fetchone()
            if active is not None:
                raise NonterminalRunError("cannot supersede a claim with active execution")
            self._connection.execute(
                "UPDATE claim SET lifecycle = 'superseded', updated_at = ? WHERE id = ?",
                (_now(), claim_id),
            )
            self._insert_claim(draft, replacement_id)
        replacement = self.get_claim(replacement_id)
        if replacement is None:  # pragma: no cover
            raise RuntimeError("replacement claim was not persisted")
        return replacement

    def _insert_claim(self, draft: ClaimDraft, claim_id: str) -> None:
        now = _now()
        self._connection.execute(
            """INSERT INTO claim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                claim_id,
                draft.repository,
                draft.issue_number,
                draft.issue_id,
                draft.project_item_id,
                draft.kind,
                draft.request_fingerprint,
                _dump(draft.frozen_spec),
                "preparing",
                "{}",
                "{}",
                "{}",
                "{}",
                now,
                now,
            ),
        )

    def reserve_run(self, claim_id: str, unit_key: str, *, reason: str, evidence_path: str) -> Run:
        run_id = str(uuid.uuid4())
        with self._transaction():
            claim_row = self._connection.execute(
                "SELECT kind FROM claim WHERE id = ?", (claim_id,)
            ).fetchone()
            if claim_row is None:
                raise KeyError(claim_id)
            kind = cast(str, claim_row["kind"])
            try:
                row = self._connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), -1) + 1 FROM run "
                    "WHERE claim_id = ? AND unit_key = ?",
                    (claim_id, unit_key),
                ).fetchone()
                attempt_number = cast(int, row[0])
                self._connection.execute(
                    """INSERT INTO run (id, claim_id, unit_key, attempt_number, reason, status,
                    kind, launch_nonce, supervisor_json, plan_json, evidence_path, progress_json,
                    cancellation_requested, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?, 'reserved', ?, ?, '{}', '{}', ?, '{}', 0, '{}', ?)""",
                    (
                        run_id,
                        claim_id,
                        unit_key,
                        attempt_number,
                        reason,
                        kind,
                        uuid.uuid4().hex,
                        evidence_path,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise NonterminalRunError(
                    "another nonterminal run already occupies the execution slot"
                ) from error
        run = self.get_run(run_id)
        if run is None:  # pragma: no cover
            raise RuntimeError("reserved run was not persisted")
        return run

    def get_run(self, run_id: str) -> Run | None:
        row = self._connection.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row is not None else None

    def nonterminal_runs(self, kind: str | None = None) -> list[Run]:
        if kind is None:
            rows = self._connection.execute(
                "SELECT * FROM run WHERE status IN ('reserved', 'running', 'observing') "
                "ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM run WHERE status IN ('reserved', 'running', 'observing') "
                "AND kind = ? ORDER BY created_at",
                (kind,),
            ).fetchall()
        return [_run(row) for row in rows]

    def runs_for_claim(self, claim_id: str) -> list[Run]:
        rows = self._connection.execute(
            "SELECT * FROM run WHERE claim_id = ? ORDER BY unit_key, attempt_number", (claim_id,)
        ).fetchall()
        return [_run(row) for row in rows]

    def mark_running(self, run_id: str, supervisor: Mapping[str, object]) -> None:
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET status = 'running', supervisor_json = ?, started_at = ? "
                "WHERE id = ? AND status = 'reserved'",
                (_dump(supervisor), _now(), run_id),
            )
            self._require_run_transition(cursor, run_id, "reserved")

    def configure_run(
        self, run_id: str, *, plan: Mapping[str, object], limits: Mapping[str, object]
    ) -> None:
        """Persist immutable launch inputs before an external supervisor is spawned."""
        saved = dict(plan)
        saved["limits"] = dict(limits)
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET plan_json = ? WHERE id = ? AND status = 'reserved'",
                (_dump(saved), run_id),
            )
            self._require_run_transition(cursor, run_id, "reserved")

    def begin_run(
        self,
        run_id: str,
        *,
        launch_nonce: str,
        supervisor: Mapping[str, object],
        process: Mapping[str, object],
    ) -> bool:
        """Claim a persisted reservation once, rejecting a stale replacement watcher."""
        values = dict(supervisor)
        values["process"] = dict(process)
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET status = 'running', supervisor_json = ?, started_at = ? "
                "WHERE id = ? AND status = 'reserved' AND launch_nonce = ?",
                (_dump(values), _now(), run_id, launch_nonce),
            )
            return cursor.rowcount == 1

    def update_supervisor(self, run_id: str, supervisor: Mapping[str, object]) -> None:
        """Record a replacement watcher without changing the owned child identity."""
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET supervisor_json = ? WHERE id = ? "
                "AND status IN ('running', 'observing')",
                (_dump(supervisor), run_id),
            )
            self._require_run_transition(cursor, run_id, "nonterminal")

    def update_progress(self, run_id: str, progress: Mapping[str, object]) -> None:
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET progress_json = ? WHERE id = ? "
                "AND status IN ('reserved', 'running', 'observing')",
                (_dump(progress), run_id),
            )
            self._require_run_transition(cursor, run_id, "nonterminal")

    def report_uncertainty(self, run_id: str, reason: str) -> None:
        """Keep the single-run slot while ownership or completion is ambiguous."""
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET status = 'observing', result_json = ? WHERE id = ? "
                "AND status IN ('reserved', 'running', 'observing')",
                (_dump({"reason": reason, "uncertain": True}), run_id),
            )
            self._require_run_transition(cursor, run_id, "nonterminal")

    def claim_watcher_launch(self, run_id: str, *, lease_seconds: float = 5.0) -> bool:
        """Atomically reserve a short watcher-launch lease for one replacement."""
        with self._transaction():
            row = self._connection.execute(
                "SELECT supervisor_json FROM run WHERE id = ? "
                "AND status IN ('running', 'observing')",
                (run_id,),
            ).fetchone()
            if row is None:
                return False
            supervisor = _load(cast(str, row["supervisor_json"]))
            active_until = supervisor.get("launching_until")
            if isinstance(active_until, int | float) and active_until > time.time():
                return False
            process = supervisor.get("process")
            lease: dict[str, object] = {"launching_until": time.time() + lease_seconds}
            if isinstance(process, Mapping):
                lease["process"] = dict(cast(Mapping[str, object], process))
            cursor = self._connection.execute(
                "UPDATE run SET supervisor_json = ? WHERE id = ? "
                "AND status IN ('running', 'observing')",
                (_dump(lease), run_id),
            )
            return cursor.rowcount == 1

    def release_watcher_launch(self, run_id: str) -> None:
        """Clear an unstarted watcher lease after a local spawn failure."""
        with self._transaction():
            row = self._connection.execute(
                "SELECT supervisor_json FROM run WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return
            supervisor = _load(cast(str, row["supervisor_json"]))
            if "launching_until" not in supervisor:
                return
            process = supervisor.get("process")
            replacement: dict[str, object] = {}
            if isinstance(process, Mapping):
                replacement["process"] = dict(cast(Mapping[str, object], process))
            self._connection.execute(
                "UPDATE run SET supervisor_json = ? WHERE id = ?",
                (_dump(replacement), run_id),
            )

    def finish_run(
        self, run_id: str, *, execution_status: str, result: Mapping[str, object]
    ) -> None:
        if execution_status in NONTERMINAL_RUN_STATUSES:
            raise ValueError("a terminal result must have a terminal execution status")
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET status = ?, result_json = ?, finished_at = ? WHERE id = ? "
                "AND status IN ('reserved', 'running', 'observing')",
                (execution_status, _dump(result), _now(), run_id),
            )
            self._require_run_transition(cursor, run_id, "nonterminal")

    def normalize_terminal_result(
        self, run_id: str, *, execution_status: str, result: Mapping[str, object]
    ) -> None:
        """Apply suite interpretation after the watcher durably establishes termination."""
        if execution_status in NONTERMINAL_RUN_STATUSES:
            raise ValueError("normalization must remain terminal")
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET status = ?, result_json = ? WHERE id = ? "
                "AND status NOT IN ('reserved', 'running', 'observing')",
                (execution_status, _dump(result), run_id),
            )
            self._require_run_transition(cursor, run_id, "terminal")

    def request_cancellation(self, run_id: str) -> None:
        with self._transaction():
            cursor = self._connection.execute(
                "UPDATE run SET cancellation_requested = 1 WHERE id = ? "
                "AND status IN ('reserved', 'running', 'observing')",
                (run_id,),
            )
            self._require_run_transition(cursor, run_id, "nonterminal")

    def recovery_attempts(self, claim_id: str, unit_key: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM run WHERE claim_id = ? AND unit_key = ? AND reason = 'recovery'",
            (claim_id, unit_key),
        ).fetchone()
        return cast(int, row[0])

    def set_hold(self, claim_id: str, name: str, value: Mapping[str, object]) -> None:
        self.set_setting("claim-hold", f"{claim_id}:{name}", value)

    def get_hold(self, claim_id: str, name: str) -> dict[str, object] | None:
        return self.get_setting("claim-hold", f"{claim_id}:{name}")

    def set_setting(self, namespace: str, key: str, value: Mapping[str, object]) -> None:
        with self._transaction():
            self._connection.execute(
                """INSERT INTO settings(namespace, key, value_json, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET value_json = excluded.value_json,
                updated_at = excluded.updated_at""",
                (namespace, key, _dump(value), _now()),
            )

    def get_setting(self, namespace: str, key: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT value_json FROM settings WHERE namespace = ? AND key = ?", (namespace, key)
        ).fetchone()
        return _load(cast(str, row[0])) if row is not None else None

    def get_settings_by_prefix(self, namespace: str, prefix: str) -> dict[str, dict[str, object]]:
        rows = self._connection.execute(
            "SELECT key, value_json FROM settings WHERE namespace = ? AND key LIKE ? ESCAPE '\\'",
            (namespace, prefix.replace("%", "\\%").replace("_", "\\_") + "%"),
        ).fetchall()
        return {cast(str, row["key"]): _load(cast(str, row["value_json"])) for row in rows}

    def set_paused(self, paused: bool) -> None:
        self.set_setting("control", "pause", {"paused": paused})

    def is_paused(self) -> bool:
        saved = self.get_setting("control", "pause")
        return bool(saved and saved.get("paused") is True)

    def record_event(self, claim_id: str, key: str, body: str) -> None:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        events = _events(claim.reporting)
        if key not in events:
            events[key] = {"body": body, "comment_id": None}
            reporting = dict(claim.reporting)
            reporting["events"] = events
            self._set_reporting(claim_id, reporting)

    def pending_events(self, claim_id: str) -> list[Event]:
        claim = self.get_claim(claim_id)
        if claim is None:
            return []
        result: list[Event] = []
        for key, item in _events(claim.reporting).items():
            comment_id = item.get("comment_id")
            if comment_id is None:
                body = item.get("body")
                if isinstance(body, str):
                    result.append(Event(key, body, None))
        return result

    def acknowledge_event(self, claim_id: str, key: str, comment_id: str) -> None:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        events = _events(claim.reporting)
        if key not in events:
            raise KeyError(key)
        events[key]["comment_id"] = comment_id
        reporting = dict(claim.reporting)
        reporting["events"] = events
        self._set_reporting(claim_id, reporting)

    def record_delivery_failure(self, claim_id: str, key: str, error: Exception) -> None:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        reporting = dict(claim.reporting)
        failures_raw = reporting.get("delivery_failures")
        failures = (
            dict(cast(Mapping[str, object], failures_raw))
            if isinstance(failures_raw, Mapping)
            else {}
        )
        prior = failures.get(key)
        prior_values: Mapping[str, object] = (
            cast(Mapping[str, object], prior) if isinstance(prior, Mapping) else {}
        )
        attempts = prior_values.get("attempts")
        failures[key] = {
            "attempts": attempts + 1 if isinstance(attempts, int) else 1,
            "error": f"{type(error).__name__}: {error}",
            "at": _now(),
        }
        reporting["delivery_failures"] = failures
        self._set_reporting(claim_id, reporting)

    def delivery_failures(self, claim_id: str) -> dict[str, dict[str, object]]:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        raw = claim.reporting.get("delivery_failures")
        if not isinstance(raw, Mapping):
            return {}
        values = cast(Mapping[str, object], raw)
        return {
            key: cast(dict[str, object], value)
            for key, value in values.items()
            if isinstance(value, Mapping)
        }

    def _set_reporting(self, claim_id: str, reporting: Mapping[str, object]) -> None:
        with self._transaction():
            self._connection.execute(
                "UPDATE claim SET reporting_json = ?, updated_at = ? WHERE id = ?",
                (_dump(reporting), _now(), claim_id),
            )

    def _require_run_transition(
        self, cursor: sqlite3.Cursor, run_id: str, expected_status: str
    ) -> None:
        if cursor.rowcount == 1:
            return
        exists = self._connection.execute("SELECT 1 FROM run WHERE id = ?", (run_id,)).fetchone()
        if exists is None:
            raise KeyError(f"run is missing: {run_id}")
        raise RunTransitionError(f"run {run_id} is not {expected_status}")


def _events(reporting: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw = reporting.get("events", {})
    if not isinstance(raw, Mapping):
        return {}
    values = cast(Mapping[str, object], raw)
    return {
        key: cast(dict[str, object], value)
        for key, value in values.items()
        if isinstance(value, dict)
    }


def _claim(row: sqlite3.Row) -> Claim:
    return Claim(
        id=cast(str, row["id"]),
        repository=cast(str, row["repository"]),
        issue_number=cast(int, row["issue_number"]),
        issue_id=cast(str, row["issue_id"]),
        project_item_id=cast(str, row["project_item_id"]),
        kind=cast(str, row["kind"]),
        request_fingerprint=cast(str, row["request_fingerprint"]),
        frozen_spec=_load(cast(str, row["frozen_spec_json"])),
        lifecycle=cast(str, row["lifecycle"]),
        outcome=_load(cast(str, row["outcome_json"])),
        preparation=_load(cast(str, row["preparation_json"])),
        reporting=_load(cast(str, row["reporting_json"])),
        cleanup=_load(cast(str, row["cleanup_json"])),
    )


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=cast(str, row["id"]),
        claim_id=cast(str, row["claim_id"]),
        unit_key=cast(str, row["unit_key"]),
        attempt_number=cast(int, row["attempt_number"]),
        reason=cast(str, row["reason"]),
        status=cast(str, row["status"]),
        kind=cast(str, row["kind"]),
        evidence_path=cast(str, row["evidence_path"]),
        result=_load(cast(str, row["result_json"])),
        launch_nonce=cast(str, row["launch_nonce"]),
        supervisor=_load(cast(str, row["supervisor_json"])),
        plan=_load(cast(str, row["plan_json"])),
        progress=_load(cast(str, row["progress_json"])),
        cancellation_requested=cast(int, row["cancellation_requested"]) == 1,
        started_at=cast(str | None, row["started_at"]),
        finished_at=cast(str | None, row["finished_at"]),
    )
