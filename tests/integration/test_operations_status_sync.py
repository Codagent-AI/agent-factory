from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_factory.config import LocalConfig
from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore


def _settled_claim_with_pr(store: ClaimStore) -> str:
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"pr": {"url": "https://github.com/example/work/pull/1", "number": 1}},
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    return claim.id


def test_status_prints_pending_sync_with_last_failure_reason(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    store.set_claim_sync(claim_id, {"attempted": True, "blocked_reason": "uncommitted changes"})

    text = status(store)

    assert "pending sync: uncommitted changes" in text


def test_status_prints_pending_sync_before_any_attempt(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    _settled_claim_with_pr(store)

    text = status(store)

    assert "pending sync: awaiting merge" in text


def test_status_omits_completed_sync(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    store.set_claim_sync(claim_id, {"attempted": True, "completed": True})

    text = status(store)

    assert "pending sync" not in text


def test_status_omits_sync_for_a_settled_claim_without_a_pr(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "failed"})

    text = status(store)

    assert "pending sync" not in text


def test_status_shows_eval_slot_holder_and_fix_slot_free(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 7, "I7", "P7", "eval", "fp", {}))
    store.reserve_run(claim.id, "rep-1", lane="low", reason="initial", evidence_path="/tmp/ev")

    text = status(store)

    assert "eval slot: busy (low)" in text
    assert "eval lane low: example/evals#7 rep-1 (reserved)" in text
    assert "fix slot: free" in text
    assert "host attempts: 0" in text


def test_status_counts_saved_host_backends_and_unplanned_runs(tmp_path: Path) -> None:
    state = tmp_path / "state.sqlite3"
    config = tmp_path / "local.toml"
    config.write_text(
        f'''shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[schedule]
timezone = "UTC"
poll_minutes = 5
start_hour = 0
stop_hour = 0
[limits]
minimum_free_gib = 0
inactivity_seconds = 1
execution_seconds = 1
total_seconds = 1
codex_reset_fallback_seconds = 1
[credentials]
github_app_key = "{tmp_path / "key"}"
suite_environment = "{tmp_path / "environment"}"
[fix]
execution = "docker"
'''
    )
    store = ClaimStore(state)
    fix = store.create_claim(ClaimDraft("example/work", 8, "I8", "P8", "fix", "fp", {}))
    host = store.reserve_run(fix.id, "fix", lane="low", reason="initial", evidence_path="/tmp/host")
    store.configure_run(host.id, plan={"ownership_hints": {"backend": "host"}}, limits={})
    store.mark_running(host.id, {})
    feature = store.create_claim(ClaimDraft("example/work", 9, "I9", "P9", "feature", "fp", {}))
    pending = store.reserve_run(
        feature.id, "feature", lane="low", reason="initial", evidence_path="/tmp/pending"
    )
    eval_claim = store.create_claim(ClaimDraft("example/evals", 10, "I10", "P10", "eval", "fp", {}))
    fly = store.reserve_run(
        eval_claim.id, "rep-1", lane="low", reason="initial", evidence_path="/tmp/eval"
    )
    store.configure_run(fly.id, plan={"ownership_hints": {"backend": "fly"}}, limits={})
    store.mark_running(fly.id, {})
    store.close()
    command = [
        sys.executable,
        "-m",
        "agent_factory.cli",
        "--config",
        str(config),
        "--state",
        str(state),
        "status",
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert "host attempts: 2" in first
    assert "eval lane low: example/evals#10 rep-1 (running)" in first
    assert "fix lane low: example/work#8 fix (running)" in first
    assert "feature lane low: example/work#9 feature (reserved)" in first
    store = ClaimStore(state)
    store.finish_run(host.id, execution_status="completed", result={})
    store.finish_run(pending.id, execution_status="completed", result={})
    store.close()
    second = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert "host attempts: 0" in second
    assert "eval lane low: example/evals#10 rep-1 (running)" in second
    assert "fix slot: free" in second
    assert "feature slot: free" in second


def test_status_shows_blocked_fix_claim_with_decline_reason(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 5, "I5", "P5", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(run.id, execution_status="completed", result={"outcome": "needs-input"})
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": datetime.now(UTC).isoformat()})
    store.record_event(claim.id, f"{run.id}:needs-input", "Needs input.\n\n- reproduction missing")

    text = status(store)

    assert "example/work#5" in text
    assert "reproduction missing" in text


def test_status_shows_the_most_recent_decline_reason_after_multiple_declines(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 5, "I5", "P5", "fix", "fp", {}))
    first_run = store.reserve_run(
        claim.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(first_run.id, execution_status="completed", result={"outcome": "needs-input"})
    store.record_event(claim.id, f"{first_run.id}:needs-input", "Needs input.\n\n- first decline")
    store.set_claim_lifecycle(claim.id, "active", {})
    second_run = store.reserve_run(
        claim.id, "fix", lane="low", reason="unblock", evidence_path="/tmp/ev2"
    )
    store.finish_run(second_run.id, execution_status="completed", result={"outcome": "needs-input"})
    store.record_event(claim.id, f"{second_run.id}:needs-input", "Needs input.\n\n- second decline")
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": datetime.now(UTC).isoformat()})

    text = status(store)

    assert "second decline" in text
    assert "first decline" not in text


def _local_with_shared(tmp_path: Path, eval_provider: str, fix_provider: str) -> LocalConfig:
    shared_path = tmp_path / "shared.toml"
    shared_path.write_text(
        f"""\
[github]
organization = "Example Org"
bot_login = "example-factory[bot]"
app_id = "123"
installation_id = "456"

[project]
id = "PVT_example"
number = 7

[fields.status]
id = "status-field"
[fields.status.options]
backlog = "backlog-option"
ready = "ready-option"
running = "running-option"
review = "review-option"
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"
human = "human-option"

[fields.refs]
id = "refs-field"

[fields.verdict]
id = "verdict-field"
[fields.verdict.options]
pending-human-review = "pending-option"
failed = "failed-option"
quota-deferred = "quota-option"
infra-error = "infra-option"

[routing]
eval_source = "example/evals"
general_sources = ["example/evals", "example/work"]
eval_label = "run-eval"
eval_type = "Eval"

[eval]
harness_ref = "main"
suite = "and-scene"
repetitions = 3
[eval.defaults]
lead = "{eval_provider}:model:high"

[fix]
contract = "factory-fix/1"
[fix.defaults]
lead = "{fix_provider}:model:high"
""",
        encoding="utf-8",
    )
    local_path = tmp_path / "local.toml"
    local_path.write_text(
        f"""\
shared_config = "{shared_path}"
storage_root = "{tmp_path / "factory"}"

[repositories]
agent_evals = "{tmp_path / "missing-evals"}"
agent_runner = "{tmp_path / "missing-runner"}"
agent_skills = "{tmp_path / "missing-skills"}"

[schedule]
timezone = "UTC"
poll_minutes = 5
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 1
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "{tmp_path / "missing-app.pem"}"
suite_environment = "{tmp_path / "missing-suite.env"}"
""",
        encoding="utf-8",
    )
    return LocalConfig.from_file(local_path)


def test_status_hides_settled_and_superseded_claims_by_default_and_shows_the_rest(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")

    running = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp1", {}))
    store.reserve_run(running.id, "rep-1", lane="low", reason="initial", evidence_path="/tmp/ev")

    blocked = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp2", {}))
    run = store.reserve_run(
        blocked.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(run.id, execution_status="completed", result={"outcome": "needs-input"})
    store.set_claim_lifecycle(blocked.id, "blocked", {})

    review_incomplete = store.create_claim(
        ClaimDraft("example/work", 3, "I3", "P3", "fix", "fp3", {})
    )
    run = store.reserve_run(
        review_incomplete.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(review_incomplete.id, "settled", {"verdict": "pending-human-review"})
    store.set_cleanup(review_incomplete.id, {"review_observed": True, "complete": False})

    done_pending_report = store.create_claim(
        ClaimDraft("example/work", 4, "I4", "P4", "fix", "fp4", {})
    )
    run = store.reserve_run(
        done_pending_report.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(done_pending_report.id, "settled", {"verdict": "failed"})
    store.set_cleanup(done_pending_report.id, {"review_observed": True, "complete": True})
    store.record_event(done_pending_report.id, "handoff", "pending")

    done_pending_sync = store.create_claim(
        ClaimDraft("example/work", 5, "I5", "P5", "fix", "fp5", {})
    )
    run = store.reserve_run(
        done_pending_sync.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"pr": {"url": "https://github.com/example/work/pull/9", "number": 9}},
    )
    store.set_claim_lifecycle(done_pending_sync.id, "settled", {"verdict": "pending-human-review"})
    store.set_cleanup(done_pending_sync.id, {"review_observed": True, "complete": True})

    fully_settled = store.create_claim(ClaimDraft("example/work", 6, "I6", "P6", "fix", "fp6", {}))
    run = store.reserve_run(
        fully_settled.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(fully_settled.id, "settled", {"verdict": "failed"})
    store.set_cleanup(fully_settled.id, {"review_observed": True, "complete": True})

    superseded_original = store.create_claim(
        ClaimDraft("example/work", 7, "I7", "P7", "fix", "fp7", {})
    )
    run = store.reserve_run(
        superseded_original.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev"
    )
    store.finish_run(run.id, execution_status="completed", result={})
    replacement = store.supersede_and_create(
        superseded_original.id, ClaimDraft("example/work", 7, "I7", "P7", "fix", "fp7b", {})
    )
    run = store.reserve_run(
        replacement.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev2"
    )
    store.finish_run(run.id, execution_status="completed", result={})
    store.set_claim_lifecycle(replacement.id, "settled", {"verdict": "failed"})
    store.set_cleanup(replacement.id, {"review_observed": True, "complete": True})

    default_text = status(store)
    assert "example/evals#1" in default_text
    assert "example/work#2" in default_text
    assert "example/work#3" in default_text
    assert "example/work#4" in default_text
    assert "example/work#5" in default_text
    assert "example/work#6" not in default_text
    assert "example/work#7" not in default_text
    assert "hidden: 3" in default_text

    all_text = status(store, include_all=True)
    for repo_line in (
        "example/evals#1",
        "example/work#2",
        "example/work#3",
        "example/work#4",
        "example/work#5",
        "example/work#6",
        "example/work#7",
    ):
        assert repo_line in all_text


def test_status_shows_quota_hold_does_not_block_fix_when_fix_uses_another_provider(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    store.set_setting("admission", "quota:codex", {"until": "2099-01-01T00:00:00+00:00"})
    config = _local_with_shared(tmp_path, eval_provider="codex", fix_provider="cursor")

    text = status(store, config)

    line = next(line for line in text.splitlines() if line.startswith("quota hold: codex"))
    assert "blocks: eval" in line
    assert "fix" not in line.split("blocks:")[1]


def test_status_shows_a_cancelled_claim_whose_release_failed(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 8, "I8", "P8", "fix", "fp8", {}))
    run = store.reserve_run(claim.id, "fix", lane="low", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(run.id, execution_status="cancelled", result={})
    store.set_claim_lifecycle(claim.id, "cancelled", {"verdict": "cancelled"})
    store.set_cleanup(claim.id, {"complete": False, "last_error": {"clone": "busy"}})

    text = status(store)

    assert "example/work#8" in text
    assert "cleanup errors" in text


def test_status_shows_terminal_registry_and_expiry_failures(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    failed = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "fp", {}))
    store.set_claim_lifecycle(failed.id, "superseded", {})
    store.set_cleanup(
        failed.id,
        {
            "registry": {"sha256:" + "a" * 64: {"tag": "claim-abc", "error": "HTTP 500"}},
            "complete": True,
        },
    )
    expiry = store.create_claim(ClaimDraft("example/evals", 2, "I2", "P2", "eval", "fp", {}))
    store.set_claim_lifecycle(expiry.id, "settled", {})
    store.record_event(expiry.id, "review-expired", "expired")
    clean = store.create_claim(ClaimDraft("example/evals", 3, "I3", "P3", "eval", "fp", {}))
    store.set_claim_lifecycle(clean.id, "superseded", {})
    store.set_cleanup(clean.id, {"complete": True})

    plain = status(store)
    full = status(store, include_all=True)
    assert "registry image claim-abc@sha256:aaaaaaaaaaaa" in plain
    assert "HTTP 500" in plain
    assert "review-expired" in plain
    assert "example/evals#3" not in plain
    assert "example/evals#3" in full


def test_cli_status_lists_only_pending_terminal_cleanup_without_writes(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    store = ClaimStore(db)
    for number, lifecycle, cleanup in (
        (
            1,
            "superseded",
            {
                "complete": True,
                "registry": {"sha256:" + "a" * 64: {"tag": "claim-one", "error": "HTTP 500"}},
            },
        ),
        (
            2,
            "superseded",
            {
                "complete": True,
                "registry": {
                    "sha256:" + "b" * 64: {"tag": "claim-two", "skipped": "shared with base"}
                },
            },
        ),
        (3, "settled", {"complete": False}),
        (4, "cancelled", {"complete": False, "last_error": {"clone": "permission denied"}}),
        (5, "superseded", {"complete": True}),
        (6, "settled", {"complete": True}),
    ):
        claim = store.create_claim(
            ClaimDraft("example/evals", number, f"I{number}", f"P{number}", "eval", "fp", {})
        )
        store.set_claim_lifecycle(claim.id, lifecycle, {})
        store.set_cleanup(claim.id, cleanup)
        if number == 3:
            store.record_event(claim.id, "review-expired", "expired")
            store.record_delivery_failure(claim.id, "review-expired", RuntimeError("HTTP 503"))
    store.close()
    before = db.read_bytes()
    command = [sys.executable, "-m", "agent_factory.cli", "--state", str(db), "status"]
    plain = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    full = subprocess.run([*command, "--all"], capture_output=True, text=True, check=True).stdout
    for number in (1, 2, 3, 4):
        assert f"example/evals#{number}" in plain
    for number in (5, 6):
        assert f"example/evals#{number}" not in plain
    for number in range(1, 7):
        assert f"example/evals#{number}" in full
    assert "claim-one@sha256:aaaaaaaaaaaa" in plain
    assert "claim-two@sha256:bbbbbbbbbbbb" in plain
    assert "shared with base" in plain
    assert "review expiry not delivered" in plain
    assert "permission denied" in plain
    assert db.read_bytes() == before
