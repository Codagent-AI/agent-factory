from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from agent_factory import runtime
from agent_factory.config import ConfigurationError, SharedConfig
from agent_factory.controller import AttemptResult, Controller, RequestSnapshot
from agent_factory.github import IssueComment
from agent_factory.store import Claim, ClaimStore
from agent_factory.suites.and_scene import ReadinessError, SourceRepositories
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler, parse_request


def _revisions(claim: Claim) -> dict[str, object]:
    return cast(dict[str, object], claim.frozen_spec["revisions"])


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def source_pair(tmp_path: Path) -> tuple[Path, Path, str, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "old",
    )
    old = git(origin, "rev-parse", "HEAD")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "new",
    )
    new = git(origin, "rev-parse", "HEAD")
    return origin, clone, old, new


def resolve(clone: Path, ref: str) -> tuple[str, str]:
    defaults = EvalDefaults("main", "main", {}, False, 1)
    request = parse_request(f'```eval\nagent_runner_ref = "{ref}"\n```', defaults)
    return runtime._resolve(SourceRepositories(clone, clone, clone), request)  # pyright: ignore[reportPrivateUsage]


def test_resolves_remote_branch_without_changing_local_checkout(tmp_path: Path) -> None:
    _, clone, old, new = source_pair(tmp_path)
    assert resolve(clone, "main") == (new, new)
    assert git(clone, "rev-parse", "HEAD") == old
    assert resolve(clone, old) == (old, new)


def test_fetch_failure_never_uses_cached_local_refs(tmp_path: Path) -> None:
    _, clone, _, _ = source_pair(tmp_path)
    git(clone, "remote", "set-url", "origin", str(tmp_path / "missing"))
    with pytest.raises(ReadinessError, match="fetch"):
        resolve(clone, "main")


def test_missing_ref_is_a_controlled_readiness_failure(tmp_path: Path) -> None:
    _, clone, _, _ = source_pair(tmp_path)
    with pytest.raises(ReadinessError, match="revision"):
        resolve(clone, "does-not-exist")


def test_fetch_timeout_is_a_controlled_readiness_failure(tmp_path: Path) -> None:
    with (
        patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 60)),
        pytest.raises(ReadinessError, match="timed out"),
    ):
        resolve(tmp_path, "main")


def eval_snapshot(
    *, issue_number: int = 1, issue_id: str = "I1", item: str = "P1"
) -> RequestSnapshot:
    return RequestSnapshot(
        repository="example/evals",
        issue_number=issue_number,
        issue_id=issue_id,
        project_item_id=item,
        author="writer",
        author_permission="write",
        issue_type="Eval",
        labels=frozenset({"run-eval"}),
        status="Ready",
        owner="factory",
        verdict=None,
        body="```eval\nrepetitions = 1\n```",
        closed=False,
    )


class NoComments:
    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        return "1"


def test_eval_handler_resolves_harness_branch_at_each_admission(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(
        origin,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "old",
    )
    old = git(origin, "rev-parse", "HEAD")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)
    defaults = EvalDefaults("main", "main", {}, False, 1)
    store = ClaimStore(tmp_path / "state.sqlite3")
    handler = EvalHandler(
        defaults,
        harness_ref="main",
        sources=SourceRepositories(clone, clone, clone),
    )
    handler.attach_store(store)
    controller = Controller(store, NoComments(), {"eval": handler})

    first = controller.accept(eval_snapshot(), resolve=lambda _: ("a" * 40, "b" * 40))
    assert first is not None
    assert _revisions(first)["evals"] == old
    run = controller.reserve_next(first.id, readiness=lambda: None)
    assert run is not None
    controller.record_result(
        run.id, AttemptResult("failed", None, {"failure": {"owner": "harness"}})
    )
    retry = controller.reserve_next(first.id, readiness=lambda: None)
    assert retry is not None and retry.reason == "recovery"
    retried_claim = store.get_claim(first.id)
    assert retried_claim is not None
    assert _revisions(retried_claim)["evals"] == old

    git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit",
        "--allow-empty", "-m", "advance")
    advanced = git(origin, "rev-parse", "HEAD")
    assert advanced != old

    second = controller.accept(
        eval_snapshot(issue_number=2, issue_id="I2", item="P2"),
        resolve=lambda _: ("a" * 40, "b" * 40),
    )
    assert second is not None
    assert _revisions(second)["evals"] == advanced

    assert handler.refs_text(first) == f"runner@{'a' * 7} skills@{'b' * 7} evals@{old[:7]}"
    assert handler.refs_text(second) == f"runner@{'a' * 7} skills@{'b' * 7} evals@{advanced[:7]}"
    store.close()


def _minimal_shared_config_text(*, harness_ref: str = "main") -> str:
    return f'''\
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
harness_ref = "{harness_ref}"
suite = "and-scene"
repetitions = 3
'''


def test_shared_config_rejects_a_harness_commit_sha_value() -> None:
    with pytest.raises(ConfigurationError, match="harness_ref"):
        SharedConfig.from_toml(_minimal_shared_config_text(harness_ref="a" * 40))
