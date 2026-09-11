from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore, NonterminalRunError
from agent_factory.work_kinds.eval import EvalDefaults, parse_request


def defaults() -> EvalDefaults:
    return EvalDefaults(
        agent_runner_ref="main",
        agent_skills_ref="main",
        roles={
            "lead": "codex:gpt-5.6-sol:high",
            "implementor": "codex:gpt-5.6-sol:high",
            "tester": "codex:gpt-5.6-sol:high",
        },
        skip_validator=False,
        repetitions=3,
    )


def frozen_spec() -> dict[str, object]:
    request = parse_request(
        "before\n```eval\nagent_runner_ref = 'release'\nrepetitions = 2\n```\nafter",
        defaults(),
    )
    return request.freeze(
        runner_sha="a" * 40,
        skills_sha="b" * 40,
        harness_sha="c" * 40,
        suite="and-scene",
    ).payload


def test_eval_request_parser_uses_only_fenced_toml_and_canonical_fingerprint() -> None:
    first = parse_request(
        "notes\n```eval\nrepetitions = 2\nlead = 'codex:gpt-5.6-sol:high'\n```\nmore notes",
        defaults(),
    )
    second = parse_request(
        "different prose\n```eval\nlead = 'codex:gpt-5.6-sol:high'\nrepetitions = 2\n```",
        defaults(),
    )

    assert first.fingerprint == second.fingerprint
    assert first.settings["repetitions"] == 2
    with pytest.raises(ValueError, match="positive integer"):
        parse_request("```eval\nrepetitions = true\n```", defaults())


def test_shipped_eval_template_is_a_valid_production_request() -> None:
    fixture = Path("tests/fixtures/eval-request.md")

    assert Path("tests/fixtures/eval-request.source-revision").read_text(encoding="utf-8").strip()
    request = parse_request(fixture.read_text(encoding="utf-8"), defaults())

    assert request.settings["repetitions"] == 3


@pytest.mark.parametrize("role", ["lead", "implementor", "tester"])
def test_eval_request_rejects_legacy_profile_aliases(role: str) -> None:
    with pytest.raises(ValueError, match=f"unsupported eval setting: {role}_profile"):
        parse_request(f"```eval\n{role}_profile = 'codex:gpt-5.6-sol:high'\n```", defaults())


@pytest.mark.parametrize("role", ["lead", "implementor", "tester"])
def test_eval_request_applies_canonical_role_override(role: str) -> None:
    profile = "codex:gpt-6-astra:high"
    request = parse_request(f"```eval\n{role} = '{profile}'\n```", defaults())

    assert request.settings["roles"] == {**defaults().roles, role: profile}


def test_sqlite_claim_history_retry_budget_and_holds_survive_restart(tmp_path: Path) -> None:
    database = tmp_path / "factory.sqlite3"
    claim = ClaimStore(database).create_claim(
        ClaimDraft(
            repository="example/evals",
            issue_number=42,
            issue_id="I_42",
            project_item_id="P_42",
            kind="eval",
            request_fingerprint="request-v1",
            frozen_spec=frozen_spec(),
        )
    )
    store = ClaimStore(database)
    first = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/evidence/1")
    store.finish_run(
        first.id,
        execution_status="completed",
        result={"product_verdict": "ready-for-human-review", "score": 61},
    )
    interrupted = store.reserve_run(
        claim.id, "rep-2", reason="initial", evidence_path="/evidence/2"
    )
    store.finish_run(
        interrupted.id, execution_status="failed", result={"failure": {"owner": "harness"}}
    )
    retry = store.reserve_run(claim.id, "rep-2", reason="recovery", evidence_path="/evidence/2")
    assert retry.attempt_number == 1
    assert store.recovery_attempts(claim.id, "rep-2") == 1
    with pytest.raises(NonterminalRunError):
        store.reserve_run(claim.id, "rep-2", reason="initial", evidence_path="/evidence/2")
    store.finish_run(retry.id, execution_status="failed", result={"failure": {"owner": "harness"}})
    reset = datetime.now(UTC) + timedelta(hours=5)
    store.set_hold(claim.id, "quota", {"until": reset.isoformat()})
    store.record_event(claim.id, "rep-2:attempt-1:retry", "retrying repetition 2")

    reopened = ClaimStore(database)
    loaded = reopened.get_claim(claim.id)
    assert loaded is not None
    assert loaded.frozen_spec == frozen_spec()
    assert [run.unit_key for run in reopened.runs_for_claim(claim.id)] == [
        "rep-1",
        "rep-2",
        "rep-2",
    ]
    assert reopened.recovery_attempts(claim.id, "rep-2") == 1
    assert reopened.get_hold(claim.id, "quota") == {"until": reset.isoformat()}
    assert reopened.pending_events(claim.id)[0].key == "rep-2:attempt-1:retry"


def test_fresh_claim_supersedes_only_idle_unfinished_history(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "factory.sqlite3")
    old = store.create_claim(
        ClaimDraft("example/evals", 42, "I_42", "P_42", "eval", "old", frozen_spec())
    )
    replacement = store.supersede_and_create(
        old.id,
        ClaimDraft("example/evals", 42, "I_42", "P_42", "eval", "new", frozen_spec()),
    )

    assert store.get_claim(old.id).lifecycle == "superseded"  # type: ignore[union-attr]
    assert replacement.id != old.id


def test_store_rejects_missing_or_stale_run_transitions(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "factory.sqlite3")

    with pytest.raises(KeyError, match="missing"):
        store.mark_running("missing", {})
    with pytest.raises(KeyError, match="missing"):
        store.finish_run("missing", execution_status="completed", result={})
