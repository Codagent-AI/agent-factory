"""INT-007: eval intake under Fly execution, through a real controller cycle.

``runtime.cycle`` runs in-process against an in-memory GitHub board (the gh
runner is the only seam replaced), the fake Machines API, and real Git worktrees.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from agent_factory import runtime
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.github import AppCredentials
from agent_factory.operations import Diagnostic
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, parse_request
from tests.fixtures.fly.api import FakeMachinesApi
from tests.fixtures.fly.flyctl import write_flyctl

SHARED = """\
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
general_sources = ["example/evals"]
eval_label = "run-eval"
eval_type = "Eval"

[eval]
harness_ref = "main"
suite = "and-scene"
repetitions = 1

[eval.defaults]
lead = "codex:x:medium"
implementor = "codex:x:medium"
tester = "codex:x:medium"
"""


def _git(path: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *arguments], text=True).strip()


def _repository(path: Path, files: dict[str, str]) -> str:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(path, "add", ".")
    _git(
        path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    origin = path.parent / f"{path.name}-origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(path), str(origin)], check=True)
    _git(path, "remote", "add", "origin", str(origin))
    _git(path, "fetch", "-q", "origin")
    return _git(path, "rev-parse", "HEAD")


class FakeGitHub:
    """The Project board and issue endpoints ``runtime.cycle`` talks to, in memory."""

    def __init__(self, shared: SharedConfig, body: str) -> None:
        self.shared = shared
        self.body = body
        self.labels: set[str] = set()
        self.comments: list[dict[str, object]] = []
        self.fields: dict[str, str] = {
            shared.project.status.id: shared.project.status.option("ready"),
            shared.project.owner.id: shared.project.owner.option("factory"),
        }
        self.text_fields: dict[str, str] = {}

    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str:
        del environment
        endpoint = arguments[1]
        if "/collaborators/" in endpoint:
            return json.dumps({"permission": "write"})
        if endpoint == "graphql":
            return json.dumps({"data": self._graphql(cast(dict[str, object], body))})
        if endpoint.endswith("/labels"):
            if "POST" in arguments:
                self.labels.update(cast(list[str], cast(dict[str, object], body)["labels"]))
                return "[]"
            return json.dumps([{"name": name} for name in sorted(self.labels)])
        if endpoint.endswith("/labels/needs-input") and "DELETE" in arguments:
            self.labels.discard("needs-input")
            return "{}"
        if "/comments" in endpoint:
            if "POST" in arguments:
                comment: dict[str, object] = {
                    "id": len(self.comments) + 1,
                    "body": cast(dict[str, object], body)["body"],
                    "user": {"login": self.shared.bot_login},
                }
                self.comments.append(comment)
                return json.dumps(comment)
            return json.dumps(self.comments)
        raise AssertionError(f"unexpected gh request: {arguments}")

    def _graphql(self, body: dict[str, object]) -> dict[str, object]:
        query = str(body["query"])
        variables = cast(dict[str, object], body["variables"])
        shared = self.shared
        if "query Fields" in query:
            fields: list[dict[str, object]] = [
                {
                    "id": configured.id,
                    "dataType": "SINGLE_SELECT",
                    "options": [{"id": v, "name": k} for k, v in configured.options.items()],
                }
                for configured in (
                    shared.project.status,
                    shared.project.owner,
                    shared.project.verdict,
                )
            ]
            fields.append({"id": shared.project.refs.id, "dataType": "TEXT"})
            if shared.project.priority_id:
                fields.append(
                    {
                        "id": shared.project.priority_id,
                        "dataType": "SINGLE_SELECT",
                        "name": "Priority",
                    }
                )
            return {"node": {"fields": {"nodes": fields, "pageInfo": {"hasNextPage": False}}}}
        if "query Items" in query:
            item = {
                "id": "P1",
                "content": {
                    "__typename": "Issue",
                    "id": "I1",
                    "number": 1,
                    "body": self.body,
                    "state": "OPEN",
                    "author": {"login": "writer"},
                    "repository": {"nameWithOwner": shared.routing.eval_source},
                    "labels": {"nodes": [{"name": shared.routing.eval_label}]},
                    "issueType": {"name": shared.routing.eval_type},
                },
                "fieldValues": {
                    "nodes": [
                        {"field": {"id": field}, "optionId": option}
                        for field, option in self.fields.items()
                    ]
                },
            }
            return {"node": {"items": {"nodes": [item], "pageInfo": {"hasNextPage": False}}}}
        field = str(variables["field"])
        if "clearProjectV2ItemFieldValue" in query:
            self.fields.pop(field, None)
        elif "text" in variables:
            self.text_fields[field] = str(variables["text"])
        else:
            self.fields[field] = str(variables["option"])
        return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": variables["item"]}}}


class Installation:
    def __init__(self, tmp_path: Path, api: FakeMachinesApi, body: str) -> None:
        self.root = tmp_path
        self.revisions = {
            "runner": _repository(
                tmp_path / "runner",
                {"workflows/core/implement-change-v1.0.yaml": "# fixture"},
            ),
            "skills": _repository(tmp_path / "skills", {"README.md": "fixture"}),
            "evals": _repository(
                tmp_path / "evals",
                {
                    "evals/agent-runner/and-scene/run.sh": "#!/bin/sh\n",
                    "evals/agent-runner/and-scene/human-review.sh": "#!/bin/sh\n",
                },
            ),
        }
        shared_path = tmp_path / "shared.toml"
        shared_path.write_text(SHARED, encoding="utf-8")
        self.shared = SharedConfig.from_file(shared_path)
        token = tmp_path / "fly-token"
        token.write_text("deploy-token\n", encoding="utf-8")
        token.chmod(0o600)
        (tmp_path / "key.pem").write_text("key", encoding="utf-8")
        (tmp_path / "suite.env").write_text("CANDIDATE_TOKEN=x\n", encoding="utf-8")
        self.config_path = tmp_path / "local.toml"
        self.config_path.write_text(
            f'''\
shared_config = "{shared_path}"
storage_root = "{tmp_path / "factory"}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 0
[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 600
total_seconds = 3600
codex_reset_fallback_seconds = 18000
[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
[fix]
execution = "host"
[eval]
execution = "fly"
[fly]
app = "app"
image = "registry.fly.io/app:base"
token_file = "{token}"
''',
            encoding="utf-8",
        )
        self.local = LocalConfig.from_file(self.config_path)
        self.github = FakeGitHub(self.shared, body)
        self.api = api
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_flyctl(bin_dir)
        self.bin = bin_dir

    def tick(self) -> None:
        runtime.cycle(self.local.state_path, self.config_path)

    def store(self) -> closing[ClaimStore]:
        return closing(ClaimStore(self.local.state_path))


@pytest.fixture
def cursor_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installation]:
    with FakeMachinesApi() as api:
        installation = Installation(tmp_path, api, "```eval\ntester = 'cursor:agent:medium'\n```")
        _wire(monkeypatch, installation)
        yield installation


@pytest.fixture
def frozen_cursor_claim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Installation]:
    with FakeMachinesApi() as api:
        installation = Installation(tmp_path, api, "```eval\nrepetitions = 1\n```")
        _wire(monkeypatch, installation)
        yield installation


def _wire(monkeypatch: pytest.MonkeyPatch, installation: Installation) -> None:
    monkeypatch.setenv("PATH", f"{installation.bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", installation.api.base_url)
    monkeypatch.setattr(runtime, "SubprocessGhRunner", lambda: installation.github)

    def static_token(credentials: AppCredentials) -> Callable[[], str]:
        del credentials
        return lambda: "token"

    monkeypatch.setattr(runtime, "InstallationTokenProvider", static_token)
    installation.local.storage_root.mkdir(parents=True, exist_ok=True)


def test_cursor_request_under_fly_is_needs_input_naming_the_role_until_it_is_replaced(
    cursor_request: Installation,
) -> None:
    site = cursor_request
    with site.store() as store:
        store.set_paused(True)  # feedback works while paused; admission does not

    site.tick()

    assert site.github.labels == {"needs-input"}
    feedback = [c for c in site.github.comments if "needs-input" in str(c["body"])]
    assert len(feedback) == 1
    assert "tester: Cursor is unavailable on Fly" in str(feedback[0]["body"])
    with site.store() as store:
        assert store.claims_for_item("P1") == []
        assert store.nonterminal_runs() == []
    assert site.github.fields[site.shared.project.status.id] == (
        site.shared.project.status.option("ready")
    )
    assert not any(str(r["method"]) == "POST" for r in site.api.requests)

    # The user replaces the flagged role with a supported CLI.
    site.github.body = "```eval\ntester = 'codex:x:medium'\n```"
    site.tick()

    assert site.github.labels == set()
    assert len([c for c in site.github.comments if "needs-input" in str(c["body"])]) == 1


def test_frozen_cursor_claim_is_held_under_fly_without_mutating_its_inputs(
    frozen_cursor_claim: Installation, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = frozen_cursor_claim
    # Admitted under Docker, when the tester default was a Cursor profile.
    docker_defaults = EvalDefaults(
        "main",
        "main",
        {
            "lead": "codex:x:medium",
            "implementor": "codex:x:medium",
            "tester": "cursor:agent:medium",
        },
        False,
        1,
        execution="docker",
    )
    request = parse_request(site.github.body, docker_defaults)
    frozen = request.freeze(
        runner_sha=site.revisions["runner"],
        skills_sha=site.revisions["skills"],
        harness_sha=site.revisions["evals"],
        suite="and-scene",
    ).payload
    with site.store() as store:
        claim = store.create_claim(
            ClaimDraft(
                site.shared.routing.eval_source, 1, "I1", "P1", "eval", request.fingerprint, frozen
            )
        )
        store.set_claim_lifecycle(claim.id, "active", {})

    # Every other prerequisite passes; only the frozen Cursor role stands in the way.
    def no_findings(*args: object, **kwargs: object) -> list[Diagnostic]:
        return []

    monkeypatch.setattr(runtime, "doctor", no_findings)

    site.tick()

    with site.store() as store:
        saved = store.get_claim(claim.id)
        assert saved is not None
        assert saved.frozen_spec == frozen
        assert saved.lifecycle == "waiting"
        assert store.get_hold(claim.id, "readiness") == {
            "reason": "tester: Cursor is unavailable on Fly"
        }
        assert store.runs_for_claim(claim.id) == []
        assert store.claims_for_item("P1") == [saved]
    assert site.github.labels == set()
    assert any(
        "Waiting: tester: Cursor is unavailable on Fly" in str(c["body"])
        for c in site.github.comments
    )
    assert site.github.fields[site.shared.project.status.id] == (
        site.shared.project.status.option("ready")
    )
    assert not any(str(r["method"]) == "POST" for r in site.api.requests)
