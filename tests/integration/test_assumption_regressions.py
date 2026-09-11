from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_factory import operations, supervisor
from agent_factory.config import SharedConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.github import GitHubApiError, GitHubClient
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import candidate_environment


class Responses:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads

    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str:
        return json.dumps(self.payloads.pop(0))


def test_doctor_rejects_stale_project_mapping_even_when_project_is_readable() -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    with (
        patch.object(GitHubClient, "list_project_items", return_value=[]),
        patch.object(
            GitHubClient,
            "validate_project",
            create=True,
            side_effect=GitHubApiError("configured field no longer exists"),
        ),
    ):
        diagnostic = operations._github_access(shared, Path("/unused"))  # pyright: ignore[reportPrivateUsage]
    assert not diagnostic.available


def test_project_validation_checks_option_membership_not_only_project_access() -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    client = GitHubClient(
        Responses(
            [
                {
                    "data": {
                        "node": {
                            "fields": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            ]
        ),
        lambda: "test-token",
    )
    with pytest.raises(GitHubApiError, match="field"):
        client.validate_project(shared.project)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


def test_dotenv_matches_literal_runner_parser(tmp_path: Path) -> None:
    path = tmp_path / "suite.env"
    path.write_text('export GH_TOKEN = "quoted\\value"\nOTHER=two words # literal\n')
    assert candidate_environment(path) == {
        "GH_TOKEN": ' "quoted\\value"',
        "OTHER": "two words # literal",
    }


def test_wall_clock_jump_does_not_timeout_a_live_attempt(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("org/repo", 1, "I", "P", "eval", "x", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(run.id, {})
    plan = ExecutionPlan(("unused",), str(tmp_path), {}, (), (), {}, False)
    result: dict[str, object] = {"evaluation_status": "complete", "product_verdict": "fail"}
    # Only the wall clock jumps by a day; the live monotonic duration is unchanged.
    with (
        patch.object(supervisor.time, "time", side_effect=[1000, 1000, 87400, 87400, 87400]),
        patch.object(
            supervisor.time,
            "monotonic",
            side_effect=[10, 10, 10.1, 10.2, 10.3],
        ),
        patch.object(supervisor.time, "sleep"),
        patch.object(
            supervisor,
            "_identity_status",
            side_effect=["alive", "missing"],
        ),
        patch.object(supervisor, "_load_result", return_value=supervisor.ResultRead(result)),
        patch.object(
            supervisor,
            "_terminate",
            return_value=True,
        ) as terminate,
    ):
        supervisor._observe(store, run.id, plan, supervisor.SupervisionLimits(30, 60, 90), {})  # pyright: ignore[reportPrivateUsage]
    saved = store.get_run(run.id)
    assert saved is not None and saved.status == "completed"
    terminate.assert_not_called()
    store.close()


def test_config_keeps_explicit_bot_identity_and_role_defaults() -> None:
    text = (
        Path("config/codagent.toml")
        .read_text()
        .replace(
            'organization = "Codagent-AI"',
            'organization = "Example"',
        )
        .replace(
            'bot_login = "codagent-factory[bot]"',
            'bot_login = "example-worker[bot]"',
        )
    )
    shared = SharedConfig.from_toml(text)
    assert shared.bot_login == "example-worker[bot]"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


def test_controller_reservation_obeys_process_shared_advisory_lock(tmp_path: Path) -> None:
    import fcntl
    import subprocess
    import sys

    database = tmp_path / "state.sqlite3"
    ClaimStore(database).close()
    locks = tmp_path / "locks"
    locks.mkdir()
    # A separate process holding admission.lock must serialize reservation readiness.
    program = """
import sys
from pathlib import Path
from agent_factory.controller import Controller
from agent_factory.store import ClaimStore, ClaimDraft
from agent_factory.work_kinds.eval import EvalDefaults
class Comments:
 def list_comment_records(self,*args): return []
 def create_comment(self,*args): return '1'
s=ClaimStore(Path(sys.argv[1]))
c=s.create_claim(ClaimDraft('org/repo',1,'I','P','eval','x',{'settings':{'repetitions':1}}))
d=EvalDefaults('main','main',{},False,1)
controller=Controller(s,Comments(),d,harness_sha='a'*40)
controller.reserve_next(c.id,readiness=lambda: Path(sys.argv[2]).touch())
"""
    marker = tmp_path / "entered"
    with (locks / "admission.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        child = subprocess.Popen([sys.executable, "-c", program, str(database), str(marker)])
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                child.wait(timeout=0.3)
            assert not marker.exists()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            child.wait(timeout=5)
    assert child.returncode == 0 and marker.exists()


def test_suite_readiness_covers_cursor_roles_and_codex_judge() -> None:
    from agent_factory.suites.and_scene import AndSceneAdapter

    commands = AndSceneAdapter.authentication_commands(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        {
            "lead": "cursor:cursor-grok-4.6-high:high",
            "implementor": "codex:model:high",
            "tester": "cursor:composer-2.5:high",
        }
    )
    assert commands == [("codex", "login", "status"), ("cursor", "agent", "--help")]


def test_native_issue_type_reads_current_rest_type_object() -> None:
    client = GitHubClient(
        Responses(
            [
                {
                    "node_id": "I1",
                    "number": 1,
                    "user": {"login": "writer"},
                    "labels": [],
                    "state": "open",
                    "body": "",
                    "type": {"name": "Eval"},
                }
            ]
        ),
        lambda: "test-token",
    )
    assert client.get_source_item("example/evals", 1).issue_type == "Eval"


def test_replacement_watcher_uses_persisted_elapsed_time_after_clock_adjustment(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("org/repo", 1, "I", "P", "eval", "x", {}))
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(run.id, {})
    store.update_progress(
        run.id,
        {
            "started_at": 1000,
            "last_progress_at": 1001,
            "persisted_at": 87400,
            "elapsed_seconds": 1,
            "idle_seconds": 0,
        },
    )
    plan = ExecutionPlan(("unused",), str(tmp_path), {}, (), (), {}, False)
    with (
        patch.object(supervisor.time, "time", return_value=87400),
        patch.object(supervisor.time, "monotonic", side_effect=[10, 10, 10.1]),
        patch.object(supervisor.time, "sleep"),
        patch.object(supervisor, "_identity_status", side_effect=["alive", "missing"]),
        patch.object(
            supervisor,
            "_load_result",
            return_value=supervisor.ResultRead({"evaluation_status": "complete"}),
        ),
        patch.object(supervisor, "_terminate", return_value=True) as terminate,
    ):
        supervisor._observe(store, run.id, plan, supervisor.SupervisionLimits(30, 60, 90), {})  # pyright: ignore[reportPrivateUsage]
    assert store.get_run(run.id).status == "completed"  # pyright: ignore[reportOptionalMemberAccess]
    terminate.assert_not_called()
    store.close()


def test_container_termination_refuses_wrong_artifact_mount() -> None:
    recorded = {"id": "container-id", "image": "sha256:image", "artifact_path": "/expected"}
    observed = {
        "Id": "container-id",
        "Image": "sha256:image",
        "Mounts": [{"Source": "/different", "Destination": "/artifacts"}],
    }
    with patch.object(
        supervisor.subprocess,
        "run",
        return_value=__import__("subprocess").CompletedProcess(
            ["docker", "inspect"], 0, json.dumps([observed]), ""
        ),
    ) as run:
        assert not supervisor.stop_owned_container(recorded)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    assert run.call_count == 1


@pytest.mark.parametrize("value", ["false", "123", "[]", '""'])
def test_bot_login_requires_a_nonempty_toml_string(value: str) -> None:
    from agent_factory.config import ConfigurationError

    document = (
        Path("config/codagent.toml")
        .read_text()
        .replace('bot_login = "codagent-factory[bot]"', f"bot_login = {value}")
    )
    with pytest.raises(ConfigurationError, match="github.bot_login"):
        SharedConfig.from_toml(document)
