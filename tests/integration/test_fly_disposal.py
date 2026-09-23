"""INT-006: disposal and reconciliation through the controller cycle.

Finished attempts are consumed by ``runtime._consume_results`` exactly as a tick
does, so classification, the disposal decision, and the store records are all the
real ones. The Machines API is the local fake and ``flyctl`` is the guest shim.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from agent_factory import runtime
from agent_factory.config import LocalConfig
from agent_factory.controller import AttemptResult, Controller
from agent_factory.fly.backend import FlyMachineBackend
from agent_factory.github import IssueComment
from agent_factory.operations import status
from agent_factory.store import Claim, ClaimDraft, ClaimStore, Run
from agent_factory.suites.and_scene import AndSceneAdapter
from agent_factory.work_kinds.eval import EvalDefaults
from agent_factory.work_kinds.eval.handler import EvalHandler
from tests.fixtures.fly.api import FakeMachinesApi
from tests.fixtures.fly.flyctl import write_guest_flyctl

TOTAL_SECONDS = 3600
GRACE_SECONDS = 900
ROLES = {"lead": "codex:x:medium", "implementor": "codex:x:medium", "tester": "codex:x:medium"}


class RecordedResults(EvalHandler):
    """The real handler with the suite's result reading replaced by a fixture."""

    def __init__(self, defaults: EvalDefaults, adapter: AndSceneAdapter) -> None:
        super().__init__(defaults, adapter=adapter)
        self.results: dict[str, AttemptResult] = {}

    def read_result(self, run: Run) -> AttemptResult:
        return self.results[run.id]


class NoGitHub:
    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str | None:
        return None


class Cycle:
    def __init__(
        self, tmp_path: Path, api: FakeMachinesApi, *, start_hour: int = 0, stop_hour: int = 0
    ) -> None:
        self.api = api
        self.token = tmp_path / "token"
        self.token.write_text("deploy-token\n", encoding="utf-8")
        self.token.chmod(0o600)
        self.guest_root = tmp_path / "guest"
        (self.guest_root / "var/lib/factory").mkdir(parents=True)
        self.bin = tmp_path / "bin"
        write_guest_flyctl(self.bin, self.guest_root, tmp_path / "flyctl.log", tmp_path / "none")
        environment = tmp_path / "suite.env"
        environment.write_text("CANDIDATE_TOKEN=x\n", encoding="utf-8")
        self.local = LocalConfig.from_toml(
            f'''\
shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path / "factory"}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = {start_hour}
stop_hour = {stop_hour}
[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 600
total_seconds = {TOTAL_SECONDS}
codex_reset_fallback_seconds = 18000
[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{environment}"
[eval]
execution = "fly"
[fly]
app = "app"
image = "registry.fly.io/app:base"
token_file = "{self.token}"
collection_grace_seconds = {GRACE_SECONDS}
'''
        )
        self.artifacts = tmp_path / "artifacts"
        self.store = ClaimStore(tmp_path / "state.sqlite3")
        self.handler = RecordedResults(
            EvalDefaults("main", "main", ROLES, False, 1, execution="fly"),
            AndSceneAdapter(environment_file=environment, execution="fly", fly=self.local.fly),
        )
        self.controller = Controller(
            self.store, NoGitHub(), {"eval": self.handler}, artifact_root=self.artifacts
        )
        self.machines = 0

    def close(self) -> None:
        self.store.close()

    def claim(self, repetitions: int = 1) -> Claim:
        return self.store.create_claim(
            ClaimDraft(
                "example/evals",
                1,
                "I1",
                "P1",
                "eval",
                "fp",
                {"settings": {"repetitions": repetitions, "roles": ROLES}},
            )
        )

    def finished_run(
        self,
        claim: Claim,
        unit_key: str,
        result: AttemptResult,
        *,
        reason: str = "initial",
        execution_status: str | None = None,
    ) -> tuple[Run, str]:
        """A terminal attempt whose Machine the fake API still holds, as after collection."""
        artifact = self.artifacts / f"{claim.id}-{unit_key}"
        run = self.store.reserve_run(claim.id, unit_key, reason=reason, evidence_path=str(artifact))
        self.machines += 1
        machine_id = f"machine-{self.machines}"
        nonce = f"nonce-{self.machines}"
        factory = artifact / ".factory"
        factory.mkdir(parents=True)
        (factory / "machine.json").write_text(
            json.dumps({"app": "app", "id": machine_id, "run_id": run.id, "nonce": nonce})
        )
        (factory / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run.id,
                    "claim_id": claim.id,
                    "unit_key": unit_key,
                    "nonce": nonce,
                    "fly": {"app": "app", "token_file": str(self.token)},
                }
            )
        )
        self.api.machines[machine_id] = {
            "id": machine_id,
            "state": "started",
            "config": {
                "image": "registry.fly.io/app@sha256:abc",
                "init": {"exec": ["bash", "-c", "guest"]},
                "env": {"FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 100)},
                "metadata": {
                    "factory-owner": "agent-factory",
                    "run_id": run.id,
                    "claim_id": claim.id,
                    "unit_key": unit_key,
                    "nonce": nonce,
                    "deadline_epoch": str(int(time.time()) + 100),
                },
            },
        }
        self.store.configure_run(
            run.id,
            plan={
                "argv": ["run.sh"],
                "working_directory": str(artifact),
                "allowed_environment": {},
                "credential_paths": [],
                "progress_sources": [],
                "ownership_hints": {"artifact_path": str(artifact), "backend": "fly-machine"},
                "resume": False,
            },
            limits={},
        )
        self.store.mark_running(run.id, {})
        self.store.finish_run(
            run.id,
            execution_status=execution_status or result.execution_status,
            result=dict(result.result),
        )
        self.handler.results[run.id] = result
        return cast(Run, self.store.get_run(run.id)), machine_id

    def consume(self) -> None:
        runtime._consume_results(  # pyright: ignore[reportPrivateUsage]
            self.store, self.controller, self.local
        )

    def reconcile(self) -> list[str]:
        assert self.local.fly is not None
        return FlyMachineBackend(
            app=self.local.fly.app, token_file=self.local.fly.token_file, local=self.local
        ).reconcile(self.store)

    def record(self, run: Run) -> dict[str, object] | None:
        return self.store.get_setting("runtime", f"fly:machine:{run.id}")

    def requests(self, method: str, suffix: str = "") -> list[str]:
        return [
            str(r["path"])
            for r in self.api.requests
            if r["method"] == method and str(r["path"]).endswith(suffix)
        ]

    def tagged(self, machine_id: str, *, deadline: int | None, state: str = "started") -> None:
        metadata: dict[str, str] = {"factory-owner": "agent-factory"}
        if deadline is not None:
            metadata["deadline_epoch"] = str(deadline)
        self.api.machines[machine_id] = {
            "id": machine_id,
            "state": state,
            "config": {"metadata": metadata},
        }


@pytest.fixture
def cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cycle]:
    with FakeMachinesApi() as api:
        monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", api.base_url)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        harness = Cycle(tmp_path, api)
        try:
            yield harness
        finally:
            harness.close()


@pytest.fixture
def windowed_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cycle]:
    """Admission is open 09:00-17:00 UTC only."""
    with FakeMachinesApi() as api:
        monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", api.base_url)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        harness = Cycle(tmp_path, api, start_hour=9, stop_hour=17)
        try:
            yield harness
        finally:
            harness.close()


def _reviewable() -> AttemptResult:
    return AttemptResult("completed", "unavailable", {"evaluation_status": "pending-human-review"})


def _quota(until: datetime) -> AttemptResult:
    return AttemptResult(
        "failed",
        None,
        {"evaluation_status": "failed", "failure": {"reason": "Codex usage limit reached"}},
        quota_until=until,
    )


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


# -- disposal by classification ---------------------------------------------


def test_settled_result_destroys_its_machine_and_clears_the_record(cycle: Cycle) -> None:
    claim = cycle.claim()
    run, machine_id = cycle.finished_run(claim, "rep-1", _reviewable())

    cycle.consume()

    assert machine_id not in cycle.api.machines
    assert cycle.requests("DELETE") == [f"/v1/apps/app/machines/{machine_id}?force=true"]
    assert cycle.record(run) is None
    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") is None
    saved = cycle.store.get_claim(claim.id)
    assert saved is not None and saved.lifecycle == "settled"


def test_exhausted_recovery_destroys_its_machine(cycle: Cycle) -> None:
    claim = cycle.claim()
    technical = AttemptResult("failed", None, {"reason": "suite crashed"})
    run, machine_id = cycle.finished_run(claim, "rep-1", technical, reason="recovery")

    cycle.consume()

    assert machine_id not in cycle.api.machines
    assert cycle.record(run) is None
    saved = cycle.store.get_claim(claim.id)
    assert saved is not None and saved.outcome.get("verdict") == "infra-error"


def test_technical_failure_with_a_retry_left_keeps_the_machine(cycle: Cycle) -> None:
    claim = cycle.claim()
    technical = AttemptResult("failed", None, {"reason": "suite crashed"})
    run, machine_id = cycle.finished_run(claim, "rep-1", technical)

    cycle.consume()

    assert cycle.api.machines[machine_id]["state"] == "started"
    assert cycle.requests("DELETE") == [] and cycle.requests("POST", "/stop") == []
    record = cycle.record(run)
    assert record is not None
    assert record["decision"] == "keep" and record["claim_id"] == claim.id
    assert cycle.store.recovery_attempts(claim.id, "rep-1") == 0


@pytest.mark.parametrize(("reason", "kept"), [("initial", False), ("recovery", True)])
def test_presuite_failure_destroys_only_a_machine_the_attempt_created(
    cycle: Cycle, reason: str, kept: bool
) -> None:
    claim = cycle.claim()
    presuite = AttemptResult(
        "failed",
        None,
        {"reason": "Fly launcher failed", "failure_stage": "pre-suite", "stage": "delivery"},
    )
    run, machine_id = cycle.finished_run(claim, "rep-1", presuite, reason=reason)

    cycle.consume()

    # A recovery attempt resumes in the retained Machine, which holds its checkpoint.
    assert (machine_id in cycle.api.machines) is kept
    record = cycle.record(run)
    if kept:
        assert cycle.requests("DELETE") == []
        assert record is not None and record["decision"] == "keep"
    else:
        assert record is None
    saved = cycle.store.get_claim(claim.id)
    assert saved is not None and saved.lifecycle == "waiting"


def test_cancelled_attempt_is_left_to_the_supervisor_not_the_cycle(cycle: Cycle) -> None:
    claim = cycle.claim()
    cancelled = AttemptResult("cancelled", None, {"reason": "cancelled"})
    _run, machine_id = cycle.finished_run(claim, "rep-1", cancelled)

    cycle.consume()

    # The supervisor destroys after its verified terminate; consumption must not repeat it.
    assert machine_id in cycle.api.machines
    assert cycle.requests("DELETE") == []


def test_quota_result_stops_the_machine_with_the_hold_deadline_and_records_it(
    cycle: Cycle,
) -> None:
    until = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    claim = cycle.claim()
    run, machine_id = cycle.finished_run(claim, "rep-1", _quota(until))

    cycle.consume()

    expected_deadline = _epoch(until) + TOTAL_SECONDS + GRACE_SECONDS
    machine = cycle.api.machines[machine_id]
    assert machine["state"] == "stopped"
    metadata = cast(dict[str, dict[str, str]], machine["config"])["metadata"]
    assert metadata["deadline_epoch"] == str(expected_deadline)
    # The persisted file wins over the env deadline the guest booted with.
    guest_deadline = (cycle.guest_root / "var/lib/factory/deadline").read_text().strip()
    assert guest_deadline == str(expected_deadline)
    assert cycle.requests("POST", "/stop") == [f"/v1/apps/app/machines/{machine_id}/stop"]
    assert cycle.requests("DELETE") == []
    assert cycle.record(run) == {
        "machine_id": machine_id,
        "run_id": run.id,
        "claim_id": claim.id,
        "decision": "stop",
        "deadline_epoch": expected_deadline,
        "state": "stopped",
    }
    assert cycle.store.get_hold(claim.id, "quota") == {"until": until.isoformat()}


def test_quota_deadline_waits_for_the_next_admission_window_when_the_hold_ends_outside_it(
    windowed_cycle: Cycle,
) -> None:
    until = datetime(2031, 3, 10, 3, 30, tzinfo=UTC)  # the window opens at 09:00
    claim = windowed_cycle.claim()
    run, machine_id = windowed_cycle.finished_run(claim, "rep-1", _quota(until))

    windowed_cycle.consume()

    opening = datetime(2031, 3, 10, 9, 0, tzinfo=UTC)
    expected_deadline = _epoch(opening) + TOTAL_SECONDS + GRACE_SECONDS
    record = windowed_cycle.record(run)
    assert record is not None and record["deadline_epoch"] == expected_deadline
    metadata = cast(dict[str, dict[str, str]], windowed_cycle.api.machines[machine_id]["config"])
    assert metadata["metadata"]["deadline_epoch"] == str(expected_deadline)


def test_stopped_machine_deadline_is_refreshed_when_the_hold_moves(cycle: Cycle) -> None:
    until = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    claim = cycle.claim()
    run, machine_id = cycle.finished_run(claim, "rep-1", _quota(until))
    cycle.consume()
    cycle.api.requests.clear()

    # A later cycle with an unchanged hold touches nothing.
    cycle.reconcile()
    assert cycle.requests("POST") == []

    moved = until + timedelta(hours=3)
    cycle.store.set_hold(claim.id, "quota", {"until": moved.isoformat()})
    cycle.reconcile()

    expected_deadline = _epoch(moved) + TOTAL_SECONDS + GRACE_SECONDS
    record = cycle.record(run)
    assert record is not None
    assert record["deadline_epoch"] == expected_deadline and record["state"] == "stopped"
    update = next(r for r in cycle.api.requests if r["method"] == "POST")
    assert update["path"] == f"/v1/apps/app/machines/{machine_id}"
    body = cast(dict[str, object], update["body"])
    assert body["skip_launch"] is True
    config = cast(dict[str, dict[str, str]], body["config"])
    assert config["env"]["FACTORY_DEADLINE_EPOCH"] == str(expected_deadline)
    assert config["metadata"]["deadline_epoch"] == str(expected_deadline)
    assert config["init"], "the merged update carries the observed config"
    assert cycle.requests("POST", "/start") == [] and cycle.requests("DELETE") == []


# -- reconciliation ----------------------------------------------------------


def test_reconciliation_destroys_only_expired_tagged_machines_and_reports_unknown_ones(
    cycle: Cycle,
) -> None:
    now = int(time.time())
    cycle.tagged("machine-expired", deadline=now - 1)
    cycle.tagged("machine-unknown", deadline=now + 3600)
    cycle.api.machines["machine-untagged"] = {
        "id": "machine-untagged",
        "state": "started",
        "config": {"metadata": {"deadline_epoch": str(now - 1)}},
    }

    destroyed = cycle.reconcile()

    assert destroyed == ["machine-expired"]
    assert set(cycle.api.machines) == {"machine-unknown", "machine-untagged"}
    assert cycle.requests("DELETE") == ["/v1/apps/app/machines/machine-expired?force=true"]
    assert cycle.requests("POST") == []
    assert cycle.store.get_setting("runtime", "fly:unknown") == {
        "machines": [
            {
                "machine_id": "machine-unknown",
                "deadline_epoch": now + 3600,
                "reason": "not recorded by local store",
            }
        ]
    }
    assert "blocking condition: Machine machine-unknown: not recorded by local store" in status(
        cycle.store, cycle.local
    )


def test_reconciliation_counts_a_live_attempts_machine_as_known(cycle: Cycle) -> None:
    # A running attempt records its Machine in the run's progress, not in a disposal
    # record; that Machine is the store's own and must not be reported as unknown.
    now = int(time.time())
    claim = cycle.claim()
    run = cycle.store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="unused")
    cycle.store.mark_running(run.id, {})
    cycle.store.update_progress(
        run.id, {"machine": {"id": "machine-live", "state": "alive", "deadline_epoch": now + 3600}}
    )
    cycle.tagged("machine-live", deadline=now + 3600)

    assert cycle.reconcile() == []

    assert cycle.api.machines["machine-live"]["state"] == "started"
    assert cycle.store.get_setting("runtime", "fly:unknown") == {}
    assert not any(
        "machine-live" in line and line.startswith("blocking condition")
        for line in status(cycle.store, cycle.local).splitlines()
    )


def test_reconciliation_leaves_a_held_stopped_machine_within_its_deadline_alone(
    cycle: Cycle,
) -> None:
    until = (datetime.now(UTC) + timedelta(hours=2)).replace(second=0, microsecond=0)
    claim = cycle.claim()
    run, machine_id = cycle.finished_run(claim, "rep-1", _quota(until))
    cycle.consume()
    cycle.api.requests.clear()

    assert cycle.reconcile() == []

    assert cycle.api.machines[machine_id]["state"] == "stopped"
    assert cycle.requests("DELETE") == [] and cycle.requests("POST") == []
    assert cycle.store.get_setting("runtime", "fly:unknown") == {}
    assert cycle.record(run) is not None


def test_failed_destroy_is_recorded_and_surfaced_by_status(cycle: Cycle) -> None:
    claim = cycle.claim()
    _run, machine_id = cycle.finished_run(claim, "rep-1", _reviewable())
    cycle.api.delete_failures.append(500)

    cycle.consume()

    assert machine_id in cycle.api.machines
    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") == {
        "machines": [{"machine_id": machine_id, "reason": "destroy was not verified"}]
    }
    assert f"blocking condition: Machine {machine_id}: destroy was not verified" in status(
        cycle.store, cycle.local
    )
    # The result itself was consumed; the failure is a containment finding, not a retry.
    saved = cycle.store.get_claim(claim.id)
    assert saved is not None and saved.lifecycle == "settled"


def test_failed_destroy_keeps_the_record_and_reconciliation_retries_it(cycle: Cycle) -> None:
    claim = cycle.claim()
    run, machine_id = cycle.finished_run(claim, "rep-1", _reviewable())
    cycle.api.delete_failures.append(500)

    cycle.consume()

    record = cycle.record(run)
    assert record is not None and record["decision"] == "destroy"
    assert machine_id in cycle.api.machines

    assert cycle.reconcile() == [machine_id]

    assert machine_id not in cycle.api.machines
    assert cycle.record(run) is None
    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") == {}
    assert cycle.store.get_setting("runtime", "fly:unknown") == {}


def test_reconciliation_clears_a_cleanup_failure_once_the_machine_is_destroyed(
    cycle: Cycle,
) -> None:
    now = int(time.time())
    cycle.tagged("machine-stuck", deadline=now - 1)
    cycle.api.delete_failures.append(500)
    assert cycle.reconcile() == []
    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") == {
        "machines": [{"machine_id": "machine-stuck", "reason": "destroy was not verified"}]
    }

    assert cycle.reconcile() == ["machine-stuck"]

    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") == {}
    assert "blocking condition" not in status(cycle.store, cycle.local)


# -- aggregate verdict -------------------------------------------------------


def _run(unit_key: str, status_value: str, result: Mapping[str, object]) -> Run:
    return Run(
        f"run-{unit_key}",
        "claim",
        unit_key,
        0,
        "initial",
        status_value,
        "eval",
        "",
        dict(result),
        "",
        {},
        {},
        {},
        False,
        None,
        None,
    )


LOST = {"reason": "machine lost", "failure": {"owner": "factory", "code": "machine-lost"}}
REVIEWABLE = {"product_verdict": "unavailable", "evaluation_status": "pending-human-review"}


def _three_repetition_claim() -> Claim:
    return Claim(
        "claim",
        "repo",
        1,
        "issue",
        "item",
        "eval",
        "fp",
        {"settings": {"repetitions": 3}},
        "active",
        {},
        {},
        {},
        {},
    )


def _handler() -> EvalHandler:
    return EvalHandler(EvalDefaults("main", "main", ROLES, False, 3))


def test_one_lost_repetition_among_reviewable_ones_is_pending_human_review() -> None:
    outcome = _handler().settle(
        _three_repetition_claim(),
        [
            _run("rep-1", "completed", REVIEWABLE),
            _run("rep-2", "failed", LOST),
            _run("rep-3", "completed", REVIEWABLE),
        ],
    )

    assert outcome is not None
    assert outcome.verdict == "pending-human-review"
    assert "- rep-2 was lost to factory infrastructure: machine lost." in outcome.event_body
    assert "rep-1 was lost" not in outcome.event_body
    assert "rep-3 was lost" not in outcome.event_body


def test_every_repetition_lost_is_infra_error_with_each_loss_explained() -> None:
    outcome = _handler().settle(
        _three_repetition_claim(),
        [_run(f"rep-{n}", "failed", LOST) for n in (1, 2, 3)],
    )

    assert outcome is not None
    assert outcome.verdict == "infra-error"
    for unit in ("rep-1", "rep-2", "rep-3"):
        assert f"- {unit} was lost to factory infrastructure: machine lost." in outcome.event_body


def test_a_failed_machine_listing_clears_once_listing_succeeds(cycle: Cycle) -> None:
    # One transient list failure must not leave a permanent blocking condition.
    cycle.api.list_failures.append(500)
    assert cycle.reconcile() == []
    assert "blocking condition: Machine list:" in status(cycle.store, cycle.local)

    assert cycle.reconcile() == []

    assert cycle.store.get_setting("runtime", "fly:cleanup-failed") == {}
    assert "blocking condition" not in status(cycle.store, cycle.local)
