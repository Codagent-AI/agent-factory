"""INT-007: configuration defaults, doctor groups, and status lines under Fly execution.

Doctor and status run in-process against the fake Machines API (which also
stands in for the image registry) and an inert ``flyctl`` on ``PATH``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_factory import runtime, supervisor
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.fly import backend as fly_backend
from agent_factory.fly.api import FlyMachinesClient
from agent_factory.operations import Diagnostic, doctor, format_doctor, status
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.supervisor import SupervisionLimits
from agent_factory.work_kinds.eval.handler import EvalHandler
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
general_sources = ["example/evals", "example/work"]
eval_label = "run-eval"
eval_type = "Eval"

[eval]
harness_ref = "main"
suite = "and-scene"
repetitions = 3

[eval.defaults]
lead = "codex:x:medium"
implementor = "codex:x:medium"
tester = "codex:x:medium"

[[fix.targets]]
repository = "example/work"
"""


class Site:
    """A local installation: config files, credentials, and a PATH of stubs."""

    def __init__(self, tmp_path: Path, api: FakeMachinesApi) -> None:
        self.root = tmp_path
        self.api = api
        self.shared_path = tmp_path / "shared.toml"
        self.shared_path.write_text(SHARED, encoding="utf-8")
        self.token = tmp_path / "fly-token"
        self.token.write_text("deploy-token\n", encoding="utf-8")
        self.token.chmod(0o600)
        (tmp_path / "key.pem").write_text("key", encoding="utf-8")
        (tmp_path / "key.pem").chmod(0o600)
        (tmp_path / "suite.env").write_text("CANDIDATE_TOKEN=x\n", encoding="utf-8")
        (tmp_path / "suite.env").chmod(0o600)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        write_flyctl(self.bin)
        self.stub("codex", exit_code=0)
        self.stub("claude", exit_code=0)

    def stub(self, name: str, *, exit_code: int) -> None:
        script = self.bin / name
        script.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
        script.chmod(0o755)

    def config(self, *, evals: str, fixes: str = "host") -> LocalConfig:
        path = self.root / f"local-{evals}-{fixes}.toml"
        path.write_text(
            f'''\
shared_config = "{self.shared_path}"
storage_root = "{self.root / "factory"}"
[repositories]
agent_evals = "{self.root / "evals"}"
agent_runner = "{self.root / "runner"}"
agent_skills = "{self.root / "skills"}"
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
github_app_key = "{self.root / "key.pem"}"
suite_environment = "{self.root / "suite.env"}"
[fix]
execution = "{fixes}"
[eval]
execution = "{evals}"
'''
            + (
                f'[fly]\napp = "app"\nimage = "registry.fly.io/app:base"\n'
                f'token_file = "{self.token}"\n'
                if evals == "fly"
                else ""
            ),
            encoding="utf-8",
        )
        return LocalConfig.from_file(path)

    def shared(self) -> SharedConfig:
        return SharedConfig.from_file(self.shared_path)


@pytest.fixture
def site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Site]:
    with FakeMachinesApi(manifest_digest="sha256:base") as api:
        installation = Site(tmp_path, api)
        monkeypatch.setenv("PATH", f"{installation.bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", api.base_url)

        class LocalRegistryClient(FlyMachinesClient):
            """The real client with the image registry redirected to the fake."""

            def __init__(self, app: str, token_file: Path) -> None:
                super().__init__(app, token_file, registry_base_url=api.base_url)

        monkeypatch.setattr(fly_backend, "FlyMachinesClient", LocalRegistryClient)
        yield installation


def _by_group(diagnostics: list[Diagnostic]) -> dict[str, list[Diagnostic]]:
    groups: dict[str, list[Diagnostic]] = {}
    for item in diagnostics:
        groups.setdefault(item.group, []).append(item)
    return groups


# -- configuration -----------------------------------------------------------


def test_fly_settings_default_to_ewr_shared_4_cpus_8_gib_and_900_s_grace(site: Site) -> None:
    config = site.config(evals="fly")

    assert config.eval_execution == "fly"
    assert config.fly is not None
    assert config.fly.region == "ewr"
    assert config.fly.cpu_kind == "shared"
    assert config.fly.cpus == 4
    assert config.fly.memory_mb == 8192
    assert config.fly.collection_grace_seconds == 900


def test_codagent_shared_defaults_select_opus_lead_and_luna_implementor_and_tester() -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))

    assert shared.eval.defaults["lead"] == "claude:opus:high"
    assert shared.eval.defaults["implementor"] == "codex:gpt-6-luna:medium"
    assert shared.eval.defaults["tester"] == "codex:gpt-6-luna:medium"


# -- doctor --------------------------------------------------------------------


def test_doctor_under_fly_reports_the_eval_fly_group_and_never_mentions_docker(
    site: Site,
) -> None:
    config = site.config(evals="fly", fixes="host")

    diagnostics = doctor(config)
    text = format_doctor(diagnostics)

    groups = _by_group(diagnostics)
    assert {"shared", "eval", "eval-fly", "fix-host"} <= set(groups)
    assert "eval-sandbox" not in groups and "fix-sandbox" not in groups
    assert "-- eval-fly --" in text
    assert "docker" not in text.lower()
    fly_checks = {item.name: item for item in groups["eval-fly"]}
    assert set(fly_checks) == {
        "Fly launcher",
        "Fly deploy token",
        "Fly app API",
        "Fly image manifest",
        "flyctl transport",
    }
    assert all(item.available for item in fly_checks.values()), text
    # Diagnosis is read-only: the app and manifest were looked up, nothing was created.
    assert {str(r["method"]) for r in site.api.requests} <= {"GET", "HEAD"}
    assert not any(str(r["path"]).endswith("/machines") for r in site.api.requests)
    assert "Traceback" not in text


def test_doctor_fails_the_eval_fly_group_for_an_unresolvable_image_without_creating(
    site: Site,
) -> None:
    site.api.manifest_digest = ""  # the registry answers without a digest
    config = site.config(evals="fly", fixes="host")

    diagnostics = doctor(config)

    manifest = next(d for d in diagnostics if d.name == "Fly image manifest")
    assert not manifest.available
    assert "registry.fly.io/app:base" in manifest.action
    assert all(str(r["method"]) != "POST" for r in site.api.requests)


def test_doctor_under_docker_still_runs_the_docker_group(site: Site) -> None:
    site.stub("docker", exit_code=0)
    config = site.config(evals="docker", fixes="host")

    diagnostics = doctor(config)
    text = format_doctor(diagnostics)

    groups = _by_group(diagnostics)
    assert "eval-sandbox" in groups and "eval-fly" not in groups
    assert "-- eval-sandbox --" in text
    assert any(item.name == "Docker" for item in groups["eval-sandbox"])
    assert site.api.requests == []


def test_mode_neutral_eval_checks_sit_in_the_eval_group_under_both_modes(site: Site) -> None:
    site.stub("docker", exit_code=0)

    under_fly = _by_group(doctor(site.config(evals="fly")))["eval"]
    under_docker = _by_group(doctor(site.config(evals="docker")))["eval"]

    neutral = {item.name for item in under_fly}
    assert neutral == {item.name for item in under_docker}
    assert {"shared configuration", "codex model authentication", "free storage"} <= neutral


def test_bad_host_codex_login_holds_eval_under_fly_without_probing_docker(site: Site) -> None:
    site.stub("codex", exit_code=1)
    config = site.config(evals="fly", fixes="host")
    shared = site.shared()
    handler = EvalHandler.from_config(shared, config)

    def never_probed() -> Diagnostic:
        raise AssertionError("the Docker memory probe ran under Fly")

    failures = runtime._kind_failures(  # pyright: ignore[reportPrivateUsage]
        handler,
        config,
        shared,
        doctor(config, include_fix=False, include_informational=False),
        never_probed,
    )

    assert any(
        item.name == "codex model authentication" and item.group == "eval" for item in failures
    )
    assert not any("Docker" in item.name for item in failures)


# -- status --------------------------------------------------------------------


def _fly_plan(artifact: Path) -> ExecutionPlan:
    return ExecutionPlan(
        ("run.sh",),
        str(artifact),
        {},
        (),
        (str(artifact / "factory-suite.log"),),
        {"artifact_path": str(artifact), "backend": "fly-machine"},
        False,
    )


def test_status_shows_machine_id_state_and_deadline_for_an_active_run(
    site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = site.config(evals="fly")
    store = ClaimStore(site.root / "state.sqlite3")
    try:
        claim = store.create_claim(
            ClaimDraft("example/evals", 7, "I7", "P7", "eval", "fp", {"settings": {}})
        )
        artifact = site.root / "artifact"
        run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(artifact))
        plan = _fly_plan(artifact)
        store.configure_run(
            run.id,
            plan={
                "argv": list(plan.argv),
                "working_directory": plan.working_directory,
                "allowed_environment": {},
                "credential_paths": [],
                "progress_sources": list(plan.progress_sources),
                "ownership_hints": dict(plan.ownership_hints),
                "resume": False,
            },
            limits={},
        )
        store.mark_running(run.id, {})
        running = store.get_run(run.id)
        assert running is not None
        deadline = int(time.time()) + 4500
        factory = artifact / ".factory"
        factory.mkdir(parents=True)
        # The record and manifest exactly as the launcher and plan builder leave them.
        (factory / "machine.json").write_text(
            json.dumps(
                {
                    "app": "app",
                    "id": "machine-1",
                    "run_id": run.id,
                    "claim_id": claim.id,
                    "unit_key": "rep-1",
                    "nonce": "nonce-1",
                    "deadline": deadline,
                    "image_ref": "registry.fly.io/app@sha256:abc",
                    "region": "ewr",
                    "job": 1,
                }
            )
        )
        (factory / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run.id,
                    "claim_id": claim.id,
                    "unit_key": "rep-1",
                    "nonce": "nonce-1",
                    "fly": {"app": "app", "token_file": str(site.token)},
                }
            )
        )
        site.api.machines["machine-1"] = {
            "id": "machine-1",
            "state": "started",
            "region": "ewr",
            "config": {
                "image": "registry.fly.io/app@sha256:abc",
                "metadata": {
                    "factory-owner": "agent-factory",
                    "run_id": run.id,
                    "claim_id": claim.id,
                    "unit_key": "rep-1",
                    "nonce": "nonce-1",
                    "deadline_epoch": str(deadline),
                },
            },
        }

        # The watcher records the Machine, then would attach and observe; the
        # transport and the observation loop are out of scope here.
        def no_launcher(*_: object) -> dict[str, object]:
            return {}

        def no_observation(*_: object) -> None:
            return None

        monkeypatch.setattr(supervisor, "_spawn_plan_process", no_launcher)
        monkeypatch.setattr(supervisor, "_observe_fly", no_observation)
        supervisor._supervise_fly(  # pyright: ignore[reportPrivateUsage]
            store,
            running,
            plan,
            SupervisionLimits(60, 600, 3600),
        )

        text = status(store, config)

        assert "current: example/evals#7 rep-1 (running)" in text
        assert f"Machine: machine-1, state: alive, deadline: {deadline}" in text
        assert "deploy-token" not in text
    finally:
        store.close()


def test_status_shows_a_quota_held_machine_as_stopped_with_its_deadline(site: Site) -> None:
    config = site.config(evals="fly")
    store = ClaimStore(site.root / "state.sqlite3")
    try:
        claim = store.create_claim(
            ClaimDraft("example/evals", 7, "I7", "P7", "eval", "fp", {"settings": {}})
        )
        store.set_claim_lifecycle(claim.id, "waiting", {"verdict": "quota-deferred"})
        store.set_hold(claim.id, "quota", {"until": "2031-03-10T09:00:00+00:00"})
        # The record disposal writes for a stopped Machine (see test_fly_disposal.py).
        store.set_setting(
            "runtime",
            "fly:machine:run-1",
            {
                "machine_id": "machine-1",
                "run_id": "run-1",
                "claim_id": claim.id,
                "decision": "stop",
                "deadline_epoch": 1930000000,
                "state": "stopped",
            },
        )

        text = status(store, config)

        assert "claim: example/evals#7 (waiting)" in text
        assert "quota hold: 2031-03-10T09:00:00+00:00" in text
        assert "Machine: machine-1 stopped (quota hold), deadline 1930000000" in text
    finally:
        store.close()


def test_doctor_under_fly_fails_a_cursor_role_even_when_cursor_is_logged_in(
    site: Site,
) -> None:
    site.shared_path.write_text(
        SHARED.replace('tester = "codex:x:medium"', 'tester = "cursor:agent:medium"'),
        encoding="utf-8",
    )
    site.stub("cursor", exit_code=0)

    diagnostics = doctor(site.config(evals="fly", fixes="host"))

    compatibility = [d for d in diagnostics if "Cursor is unavailable on Fly" in d.detail]
    assert len(compatibility) == 1
    assert not compatibility[0].available
    assert compatibility[0].group == "eval-fly"
    assert "tester" in compatibility[0].detail
    assert not any("cursor" in d.name.lower() and d.available for d in diagnostics)
