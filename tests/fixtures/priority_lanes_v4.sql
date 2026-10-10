-- Verbatim v4 schema from origin/main (user_version substitution only).

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
                PRAGMA user_version = 4;
                COMMIT;
                