"""INT-008: fix limits and the fix window reach the supervisor and the recovery policy."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_factory.config import FixBranches, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.controller import Controller, ExecutionPlan, RequestSnapshot
from agent_factory.github import IssueComment
from agent_factory.store import ClaimStore, Run
from agent_factory.supervisor import launch_supervisor
from agent_factory.work_kinds.fix.handler import FixHandler

_SHARED = """\
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

_LOCAL = """\
shared_config = "/opt/agent-factory/config/codagent.toml"
storage_root = "~/.agent-factory"

[repositories]
agent_evals = "/srv/src/agent-evals"
agent_runner = "/srv/src/agent-runner"
agent_skills = "/srv/src/agent-skills"

[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[fix.limits]
inactivity_seconds = 1
execution_seconds = 2
total_seconds = 3

[credentials]
github_app_key = "/etc/agent-factory/github-app.pem"
suite_environment = "/etc/agent-factory/suite.env"
"""


class _Comments:
    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        return "1"


def _shared() -> SharedConfig:
    return dataclasses.replace(
        SharedConfig.from_toml(_SHARED),
        fix=FixConfig(
            targets=(FixTarget("example/work"),),
            branches=FixBranches(),
            defaults={
                "lead": "codex:m:high",
                "implementor": "codex:m:high",
                "tester": "codex:m:high",
            },
        ),
    )


def _snapshot() -> RequestSnapshot:
    return RequestSnapshot(
        "example/work", 212, "I212", "P212", "writer", "write", "Bug", frozenset(), "Ready",
        "factory", None, "", False,
    )  # fmt: skip


def _resolver(target: object) -> tuple[str, str, str]:
    return "a" * 40, "b" * 40, "c" * 40


def _program(tmp_path: Path, behaviour: str) -> Path:
    program = tmp_path / f"{behaviour}.py"
    program.write_text(
        "import pathlib, sys, time\n"
        "artifact = pathlib.Path(sys.argv[1]); artifact.mkdir(parents=True, exist_ok=True)\n"
        "log = artifact / 'factory-suite.log'\n"
        f"silent = {behaviour == 'silent'!r}\n"
        "for _ in range(400):\n"
        "    if not silent:\n"
        "        with log.open('a') as stream: stream.write('tick\\n')\n"
        "    time.sleep(.05)\n",
        encoding="utf-8",
    )
    return program


def _plan(program: Path, artifact: Path) -> ExecutionPlan:
    return ExecutionPlan(
        (sys.executable, str(program), str(artifact)),
        str(program.parent),
        {},
        (),
        (str(artifact / "factory-suite.log"),),
        {"artifact_path": str(artifact)},
        False,
    )


def test_fix_window_admits_outside_the_eval_window() -> None:
    local = LocalConfig.from_toml(_LOCAL)
    handler = FixHandler(_shared(), local)
    evening = datetime(2026, 1, 1, 18, 0, tzinfo=UTC)
    assert local.schedule.allows_admission(evening) is False
    assert handler.window(local).allows_admission(evening) is True


def test_run_records_fix_limits_not_eval_limits(tmp_path: Path) -> None:
    local = LocalConfig.from_toml(_LOCAL)
    handler = FixHandler(_shared(), local, resolver=_resolver)
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(store, _Comments(), {"fix": handler}, artifact_root=tmp_path / "a")
    claim = controller.accept(_snapshot(), resolve=_resolver)
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    limits = handler.limits(local)
    assert (limits.inactivity_seconds, limits.execution_seconds, limits.total_seconds) == (1, 2, 3)
    store.configure_run(run.id, plan={}, limits=dataclasses.asdict(limits))
    saved = store.get_run(run.id)
    assert saved is not None
    assert saved.plan["limits"] == {
        "inactivity_seconds": 1,
        "execution_seconds": 2,
        "total_seconds": 3,
    }
    store.close()


@pytest.mark.parametrize(
    ("behaviour", "limits", "expected"),
    [
        ("silent", (0.6, 10, 10), "inactivity"),
        ("progressing", (10, 0.8, 10), "execution"),
        ("progressing", (10, 10, 0.8), "total"),
    ],
)
def test_each_fix_limit_stops_the_attempt_and_names_itself(
    tmp_path: Path, behaviour: str, limits: tuple[float, float, float], expected: str
) -> None:
    local = LocalConfig.from_toml(_LOCAL)
    handler = FixHandler(_shared(), local, resolver=_resolver)
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(store, _Comments(), {"fix": handler}, artifact_root=tmp_path / "a")
    claim = controller.accept(_snapshot(), resolve=_resolver)
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    artifact = Path(run.evidence_path) / "attempt-1"
    fix_limits = replace(
        handler.limits(local),
        inactivity_seconds=limits[0],
        execution_seconds=limits[1],
        total_seconds=limits[2],
    )
    watcher = launch_supervisor(
        tmp_path / "state.sqlite3",
        run.id,
        _plan(_program(tmp_path, behaviour), artifact),
        fix_limits,
    )
    watcher.wait(timeout=15)
    finished = store.get_run(run.id)
    assert finished is not None and finished.status == "timed_out"
    assert finished.result["timeout"] == expected
    if behaviour == "progressing":
        assert (artifact / "factory-suite.log").exists(), "evidence is preserved"
    store.close()


def test_timeouts_consume_the_single_recovery_retry_exactly_once(tmp_path: Path) -> None:
    local = LocalConfig.from_toml(_LOCAL)
    handler = FixHandler(_shared(), local, resolver=_resolver)
    store = ClaimStore(tmp_path / "state.sqlite3")
    controller = Controller(store, _Comments(), {"fix": handler}, artifact_root=tmp_path / "a")
    claim = controller.accept(_snapshot(), resolve=_resolver)
    assert claim is not None

    def timed_out_attempt() -> Run:
        run = controller.reserve_next(claim.id, readiness=lambda: None)
        assert run is not None
        store.configure_run(run.id, plan={}, limits={})
        store.finish_run(run.id, execution_status="timed_out", result={"timeout": "inactivity"})
        finished = store.get_run(run.id)
        assert finished is not None
        controller.record_result(run.id, handler.read_result(finished))
        return finished

    first = timed_out_attempt()
    assert first.reason == "initial"
    waiting = store.get_claim(claim.id)
    assert waiting is not None and waiting.lifecycle == "waiting"
    assert any("inactivity limit exceeded" in e.body for e in store.pending_events(claim.id))
    second = timed_out_attempt()
    assert second.reason == "recovery"
    settled = store.get_claim(claim.id)
    assert settled is not None and settled.lifecycle == "settled"
    assert settled.outcome["verdict"] == "infra-error"
    assert controller.reserve_next(claim.id, readiness=lambda: None) is None
    assert len(store.runs_for_claim(claim.id)) == 2
    store.close()
