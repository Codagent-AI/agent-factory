from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from agent_factory.config import FixBranches, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.controller import Controller, RequestSnapshot
from agent_factory.github import IssueComment
from agent_factory.store import ClaimStore, Run
from agent_factory.work_kinds.fix.handler import FixHandler, attempt_evidence
from agent_factory.work_kinds.fix.outcome import read_outcome

CONTRACT = "factory-fix/1"

_SHARED_BASE = """\
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
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"

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
"""

_LOCAL_BASE = """\
shared_config = "/opt/agent-factory/config/codagent.toml"
storage_root = "~/.agent-factory"

[repositories]
agent_evals = "/srv/src/agent-evals"
agent_runner = "/srv/src/agent-runner"
agent_skills = "/srv/src/agent-skills"

[schedule]
timezone = "America/New_York"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 8
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "/etc/agent-factory/github-app.pem"
suite_environment = "/etc/agent-factory/suite.env"
"""


def _shared() -> SharedConfig:
    base = SharedConfig.from_toml(_SHARED_BASE)
    return dataclasses.replace(
        base,
        fix=FixConfig(
            targets=(FixTarget("example/work"),),
            branches=FixBranches(),
            defaults={
                "lead": "cursor:m:high",
                "implementor": "cursor:m:high",
                "tester": "cursor:m:high",
            },
            contract=CONTRACT,
        ),
    )


def _local(tmp_path: Path) -> LocalConfig:
    return LocalConfig.from_toml(_LOCAL_BASE)


def _snapshot() -> RequestSnapshot:
    return RequestSnapshot(
        repository="example/work",
        issue_number=212,
        issue_id="I212",
        project_item_id="P212",
        author="writer",
        author_permission="write",
        issue_type="Bug",
        labels=frozenset(),
        status="Ready",
        owner="factory",
        verdict=None,
        body="",
        closed=False,
    )


class Comments:
    def __init__(self) -> None:
        self.posted: list[str] = []

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        self.posted.append(body)
        return str(len(self.posted))


def _resolver(target: object) -> tuple[str, str, str]:
    return "a" * 40, "b" * 40, "c" * 40


def _accept_and_reserve(tmp_path: Path) -> tuple[Controller, ClaimStore, str, str]:
    store = ClaimStore(tmp_path / "state.sqlite3")
    handler = FixHandler(_shared(), _local(tmp_path), resolver=_resolver)
    controller = Controller(
        store, Comments(), {"fix": handler}, artifact_root=tmp_path / "artifacts"
    )
    claim = controller.accept(_snapshot(), resolve=_resolver)
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    return controller, store, claim.id, run.id


def _required_run(store: ClaimStore, run_id: str) -> Run:
    run = store.get_run(run_id)
    assert run is not None
    return run


def _write_outcome(run: Run, payload: dict[str, object]) -> None:
    path = attempt_evidence(run)
    path.mkdir(parents=True, exist_ok=True)
    (path / "fix-outcome.json").write_text(json.dumps(payload))


def test_pull_request_outcome_settles_with_pending_human_review(tmp_path: Path) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    _write_outcome(
        run,
        {
            "contract": CONTRACT,
            "outcome": "pull-request",
            "reasons": [],
            "pr": {"url": "https://github.com/example/work/pull/214", "number": 214},
        },
    )
    handler = FixHandler(_shared(), _local(tmp_path))
    result = handler.read_result(_required_run(store, run_id))
    controller.record_result(run_id, result)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "settled"
    assert claim.outcome["verdict"] == "pending-human-review"
    presentation = handler.presentation(claim)
    assert presentation.status == "Review"
    assert presentation.verdict == "pending-human-review"


def test_needs_input_outcome_stays_running_with_label(tmp_path: Path) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    _write_outcome(
        run,
        {"contract": CONTRACT, "outcome": "needs-input", "reasons": ["missing repro steps"]},
    )
    handler = FixHandler(_shared(), _local(tmp_path))
    handler.attach_store(store)
    result = handler.read_result(_required_run(store, run_id))
    controller.record_result(run_id, result)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "blocked"
    presentation = handler.presentation(claim)
    assert presentation.status == "Running"
    assert presentation.verdict is None
    assert presentation.labels.get("needs-input") is True
    events = [event.body for event in store.pending_events(claim_id)]
    assert any("missing repro steps" in body for body in events)


def test_failed_outcome_settles_with_pr_link_retained(tmp_path: Path) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    _write_outcome(
        run,
        {
            "contract": CONTRACT,
            "outcome": "failed",
            "reasons": ["tests still fail"],
            "pr": {"url": "https://github.com/example/work/pull/215", "number": 215},
        },
    )
    handler = FixHandler(_shared(), _local(tmp_path))
    result = handler.read_result(_required_run(store, run_id))
    controller.record_result(run_id, result)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "settled"
    assert claim.outcome["verdict"] == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not json",
        json.dumps({"contract": "factory-fix/999", "outcome": "pull-request"}),
        json.dumps({"contract": CONTRACT, "outcome": "unexpected"}),
    ],
)
def test_missing_malformed_or_wrong_contract_is_a_technical_failure_then_infra_error(
    tmp_path: Path, payload: str | None
) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    if payload is not None:
        attempt_evidence(run).mkdir(parents=True, exist_ok=True)
        (attempt_evidence(run) / "fix-outcome.json").write_text(payload)
    store.finish_run(run_id, execution_status="failed", result={})
    handler = FixHandler(_shared(), _local(tmp_path))
    result = handler.read_result(_required_run(store, run_id))
    controller.record_result(run_id, result)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "waiting"

    run2 = controller.reserve_next(claim_id, readiness=lambda: None)
    assert run2 is not None
    assert run2.reason == "recovery"
    if payload is not None:
        attempt_evidence(run2).mkdir(parents=True, exist_ok=True)
        (attempt_evidence(run2) / "fix-outcome.json").write_text(payload)
    store.finish_run(run2.id, execution_status="failed", result={})
    result2 = handler.read_result(_required_run(store, run2.id))
    controller.record_result(run2.id, result2)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "settled"
    assert claim.outcome["verdict"] == "infra-error"


def test_invalid_utf8_outcome_file_is_a_technical_failure(tmp_path: Path) -> None:
    (tmp_path / "fix-outcome.json").write_bytes(b'{"contract": "\xff\xfe", "outcome": "failed"}')

    assert read_outcome(tmp_path, CONTRACT) is None


def test_valid_pull_request_file_with_nonzero_exit_still_settles_as_pr(tmp_path: Path) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    _write_outcome(
        run,
        {
            "contract": CONTRACT,
            "outcome": "pull-request",
            "pr": {"url": "https://github.com/example/work/pull/216", "number": 216},
        },
    )
    store.finish_run(run_id, execution_status="failed", result={"exit_code": 1})
    handler = FixHandler(_shared(), _local(tmp_path))
    result = handler.read_result(_required_run(store, run_id))
    assert result.execution_status == "completed"
    controller.record_result(run_id, result)

    claim = store.get_claim(claim_id)
    assert claim is not None
    assert claim.lifecycle == "settled"
    assert claim.outcome["verdict"] == "pending-human-review"


def test_events_are_not_duplicated_on_repeated_consumption(tmp_path: Path) -> None:
    controller, store, claim_id, run_id = _accept_and_reserve(tmp_path)
    run = store.get_run(run_id)
    assert run is not None
    _write_outcome(
        run,
        {"contract": CONTRACT, "outcome": "pull-request", "pr": {"url": "u", "number": 1}},
    )
    handler = FixHandler(_shared(), _local(tmp_path))
    result = handler.read_result(_required_run(store, run_id))
    controller.record_result(run_id, result)
    controller.deliver_reports(claim_id)
    before = len(store.pending_events(claim_id))
    controller.record_result(run_id, result)
    after = len(store.pending_events(claim_id))
    assert before == 0
    assert after == 0
