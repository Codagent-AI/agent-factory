from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agent_factory.config import LocalConfig
from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore


def _settled_claim_with_pr(store: ClaimStore) -> str:
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/ev")
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
    store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/tmp/ev")

    text = status(store)

    assert "eval slot: example/evals#7 rep-1 (reserved)" in text
    assert "fix slot: free" in text


def test_status_shows_blocked_fix_claim_with_decline_reason(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 5, "I5", "P5", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/ev")
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
    first_run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(first_run.id, execution_status="completed", result={"outcome": "needs-input"})
    store.record_event(claim.id, f"{first_run.id}:needs-input", "Needs input.\n\n- first decline")
    store.set_claim_lifecycle(claim.id, "active", {})
    second_run = store.reserve_run(claim.id, "fix", reason="unblock", evidence_path="/tmp/ev2")
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
