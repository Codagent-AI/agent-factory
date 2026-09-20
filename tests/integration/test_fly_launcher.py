"""INT-004: the launcher's Machine lifecycle against a fake API and a fake flyctl.

The guest side is the real init script under bash, rooted in a temp directory,
so delivery, the job loop, the file manifest, and collection are all exercised.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict, cast

import pytest

from agent_factory.fly.api import FlyMachinesClient
from agent_factory.fly.guest import guest_init_script, stand_in_script
from agent_factory.fly.launcher import main
from agent_factory.fly.transport import (
    EXIT_COLLECTION_FAILED,
    EXIT_MACHINE_LOST,
    EXIT_MISMATCH,
    EXIT_TRANSPORT,
    JobRequest,
    Lifecycle,
)
from tests.fixtures.fly.api import FakeMachinesApi
from tests.fixtures.fly.flyctl import write_guest_flyctl


class FlyctlCall(TypedDict):
    argv: list[str]
    token_in_env: bool
    record_exists: bool


class Environment:
    def __init__(self, tmp_path: Path, api: FakeMachinesApi) -> None:
        self.api = api
        self.guest_root = tmp_path / "guest"
        self.artifact = tmp_path / "artifact"
        self.factory = self.artifact / ".factory"
        self.factory.mkdir(parents=True)
        self.guest_root.mkdir()
        self.flyctl_log = tmp_path / "flyctl.log"
        self.token = tmp_path / "token"
        self.token.write_text("secret-deploy-token\n", encoding="utf-8")
        self.home = tmp_path / "home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".claude").mkdir()
        (self.home / ".cursor").mkdir()
        (self.home / ".codex/auth.json").write_text("codex-secret", encoding="utf-8")
        (self.home / ".claude/.credentials.json").write_text("claude-secret", encoding="utf-8")
        (self.home / ".cursor/auth.json").write_text("cursor-secret", encoding="utf-8")
        self.input_dir = tmp_path / "suite"
        self.input_dir.mkdir()
        (self.input_dir / "controller.mjs").write_text("// suite", encoding="utf-8")
        self.bin = tmp_path / "bin"
        write_guest_flyctl(
            self.bin, self.guest_root, self.flyctl_log, self.factory / "machine.json"
        )

    def manifest(self, run_id: str = "run-1", **extra: object) -> dict[str, object]:
        return {
            "run_id": run_id,
            "claim_id": "claim-1",
            "unit_key": "rep-1",
            "nonce": "nonce-1",
            "image": "registry.fly.io/app:base",
            "deadline": {"total_seconds": 600, "collection_grace_seconds": 60},
            "fly": {
                "app": "app",
                "token_file": str(self.token),
                "region": "ewr",
                "cpu_kind": "shared",
                "cpus": 4,
                "memory_mb": 8192,
                "heartbeat_seconds": 1,
            },
            **extra,
        }

    def lifecycle(self, manifest: dict[str, object]) -> Lifecycle:
        client = FlyMachinesClient("app", self.token, base_url=self.api.base_url)
        return Lifecycle(manifest, self.factory, client=client)

    def request(self, body: str, *, auth: bool = True) -> JobRequest:
        return JobRequest(
            artifact_dir=self.artifact,
            script=stand_in_script(body),
            input_dir=self.input_dir,
            environment="CANDIDATE_TOKEN=delivery-secret\n",
            codex_auth=auth,
            claude_auth=auth,
        )

    def flyctl_calls(self) -> list[FlyctlCall]:
        return [
            cast(FlyctlCall, json.loads(line)) for line in self.flyctl_log.read_text().splitlines()
        ]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Environment]:
    with FakeMachinesApi() as api:
        environment = Environment(tmp_path, api)
        monkeypatch.setenv("PATH", f"{environment.bin}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("HOME", str(environment.home))
        guest = subprocess.Popen(
            ["bash", "-c", guest_init_script()],
            env={
                **os.environ,
                "FACTORY_ROOT": str(environment.guest_root),
                "FACTORY_WATCHDOG_SECONDS": "1",
                "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 3600),
            },
        )
        try:
            yield environment
        finally:
            guest.kill()
            guest.wait()


_SUITE = (
    "echo suite-output; "
    "ls -A /host-home/codex /host-home/claude > /artifacts/seen-credentials.txt; "
    "cat /eval-input/controller.mjs > /artifacts/seen-input.txt; "
    "echo '{\"schema_version\": 1}' > /artifacts/run-state.json; sleep 2; "
    'echo \'{"evaluation_status": "completed"}\' > /artifacts/result.json; exit 7'
)


def test_fresh_launch_records_ownership_then_delivers_runs_and_collects(
    env: Environment, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    code = env.lifecycle(env.manifest()).run(env.request(_SUITE))

    assert code == 7  # the guest job's own exit code
    calls = env.flyctl_calls()
    # Ownership was durable on the Mac before flyctl carried anything to the Machine.
    assert all(call["record_exists"] for call in calls)
    assert all(call["token_in_env"] for call in calls)
    assert "secret-deploy-token" not in env.flyctl_log.read_text()
    delivered = sorted(
        call["argv"][-1] for call in calls if call["argv"][:3] == ["ssh", "sftp", "put"]
    )
    assert delivered == [
        "/artifacts/.factory/job/1/job.sh",
        "/host-home/claude/.credentials.json",
        "/host-home/codex/auth.json",
        "/run/factory/env",
        f"/tmp/factory-bundle-{os.getpid()}.tar",
    ]
    create = next(r for r in env.api.requests if r["method"] == "POST")
    assert "secret" not in json.dumps(create["body"])
    # The job saw its inputs and credentials, and they were gone before DONE.
    seen = (env.artifact / "seen-credentials.txt").read_text()
    assert "auth.json" in seen and ".credentials.json" in seen
    assert (env.artifact / "seen-input.txt").read_text() == "// suite"
    assert not (env.guest_root / "host-home").exists()
    assert not (env.guest_root / "run/factory/env").exists()
    collected = {str(p.relative_to(env.artifact)) for p in env.artifact.rglob("*") if p.is_file()}
    assert not any("host-home" in name or name.endswith("/env") for name in collected)
    assert "delivery-secret" not in "".join(
        p.read_text(errors="replace") for p in env.artifact.rglob("*") if p.is_file()
    )
    # Verified tree is in place, staging is gone, and the exit code is recorded.
    assert json.loads((env.artifact / "result.json").read_text())["evaluation_status"]
    assert (env.artifact / "guest-exit-code").read_text().strip() == "7"
    assert not (env.factory / "staging").exists()
    # stdout carries only relayed guest bytes; diagnostics have their own file.
    assert capfdbinary.readouterr().out == b"suite-output\n"
    assert "delivered job 1" in (env.factory / "launcher.log").read_text()
    assert json.loads((env.factory / "heartbeat.json").read_text())["checkpoint_seen"] is True
    record = json.loads((env.factory / "machine.json").read_text())
    assert record["run_id"] == "run-1" and record["job"] == 1 and record["nonce"] == "nonce-1"


def test_heartbeat_is_rewritten_only_when_its_content_changes(env: Environment) -> None:
    env.lifecycle(env.manifest()).run(env.request("sleep 3"))
    first = (env.factory / "heartbeat.json").stat().st_mtime_ns
    # Several polls happened during the sleep; an unchanged guest leaves one write.
    polls = [c for c in env.flyctl_calls() if "job.log" in c["argv"][-1]]
    assert len(polls) >= 2
    assert (env.factory / "heartbeat.json").stat().st_mtime_ns == first


def test_recorded_machine_that_is_gone_before_any_job_is_replaced(env: Environment) -> None:
    (env.factory / "machine.json").write_text(
        json.dumps({"app": "app", "id": "machine-dead", "run_id": "run-1", "nonce": "nonce-1"})
    )
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == 0
    record = json.loads((env.factory / "machine.json").read_text())
    assert record["id"] == "machine-1"
    assert {"id": "machine-dead", "disposal": "gone"} in record["history"]


def test_mismatched_machine_is_never_adopted_or_destroyed(env: Environment) -> None:
    env.api.machines["machine-x"] = {
        "id": "machine-x",
        "state": "started",
        "config": {"metadata": {"factory-owner": "agent-factory", "nonce": "someone-else"}},
    }
    (env.factory / "machine.json").write_text(
        json.dumps({"app": "app", "id": "machine-x", "run_id": "run-1", "nonce": "nonce-1"})
    )
    assert env.lifecycle(env.manifest()).run(env.request("true")) == EXIT_MISMATCH
    assert "machine-x" in env.api.machines
    assert not any(r["method"] == "DELETE" for r in env.api.requests)
    assert not env.flyctl_log.exists()  # nothing was delivered


def test_recovery_resumes_in_the_same_machine_with_a_fresh_deadline(env: Environment) -> None:
    assert env.lifecycle(env.manifest()).run(env.request(_SUITE)) == 7
    first = json.loads((env.factory / "machine.json").read_text())
    time.sleep(1.1)

    recovery = env.manifest("run-2", expect_checkpoint=True)
    code = env.lifecycle(recovery).run(env.request("echo resumed > /artifacts/resumed.txt"))

    assert code == 0
    record = json.loads((env.factory / "machine.json").read_text())
    assert record["id"] == first["id"] and record["run_id"] == "run-2" and record["job"] == 2
    assert record["deadline"] > first["deadline"]
    metadata = env.api.machines[first["id"]]["config"]["metadata"]  # type: ignore[index]
    assert metadata["run_id"] == "run-2"
    assert metadata["deadline_epoch"] == str(record["deadline"])
    assert (env.guest_root / "var/lib/factory/deadline").read_text().strip() == str(
        record["deadline"]
    )
    assert (env.artifact / "resumed.txt").read_text().strip() == "resumed"
    assert len([r for r in env.api.requests if str(r["path"]).endswith("/machines")]) == 1


def test_expected_checkpoint_missing_is_a_lost_machine_but_unexpected_is_not(
    env: Environment,
) -> None:
    assert env.lifecycle(env.manifest()).run(env.request("exit 1", auth=False)) == 1
    # The first attempt never created a checkpoint: recovery runs fresh, same Machine.
    fresh = env.manifest("run-2", expect_checkpoint=False)
    assert env.lifecycle(fresh).run(env.request("true", auth=False)) == 0
    # A checkpoint the factory saw earlier and cannot find now is a lost Machine.
    lost = env.manifest("run-3", expect_checkpoint=True)
    assert env.lifecycle(lost).run(env.request("true", auth=False)) == EXIT_MACHINE_LOST


def test_recovery_with_the_machine_gone_is_a_lost_machine(env: Environment) -> None:
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == 0
    env.api.machines.clear()
    code = env.lifecycle(env.manifest("run-2")).run(env.request("true", auth=False))
    assert code == EXIT_MACHINE_LOST
    assert len([r for r in env.api.requests if str(r["path"]).endswith("/machines")]) == 1


def test_stopped_machine_gets_its_deadline_in_config_before_it_is_started(
    env: Environment,
) -> None:
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == 0
    machine_id = json.loads((env.factory / "machine.json").read_text())["id"]
    env.api.machines[machine_id]["state"] = "stopped"
    env.api.requests.clear()

    assert env.lifecycle(env.manifest("run-2")).run(env.request("true", auth=False)) == 0

    posts = [r for r in env.api.requests if r["method"] == "POST"]
    update = next(i for i, r in enumerate(posts) if str(r["path"]).endswith(machine_id))
    start = next(i for i, r in enumerate(posts) if str(r["path"]).endswith("/start"))
    assert update < start
    body = posts[update]["body"]
    assert isinstance(body, dict) and body["skip_launch"] is True
    deadline = json.loads((env.factory / "machine.json").read_text())["deadline"]
    assert body["config"]["env"]["FACTORY_DEADLINE_EPOCH"] == str(deadline)
    assert body["config"]["init"], "a config update must carry the whole observed config"


def test_attach_continues_a_running_job_without_repeating_output(
    env: Environment, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    class Died(Exception):
        pass

    def die(_seconds: float) -> None:
        raise Died

    client = FlyMachinesClient("app", env.token, base_url=env.api.base_url)
    first = Lifecycle(env.manifest(), env.factory, client=client, sleep=die)
    with pytest.raises(Died):
        first.run(env.request("echo one; sleep 3; echo two", auth=False))

    assert env.lifecycle(env.manifest()).attach(env.artifact) == 0
    assert capfdbinary.readouterr().out == b"one\ntwo\n"
    assert len([r for r in env.api.requests if str(r["path"]).endswith("/machines")]) == 1


def test_truncated_collection_is_rejected_and_leaves_no_partial_result(env: Environment) -> None:
    (env.guest_root / ".truncate-tar").write_text("")
    code = env.lifecycle(env.manifest()).run(env.request(_SUITE, auth=False))
    assert code == EXIT_COLLECTION_FAILED
    assert not (env.artifact / "result.json").exists()
    assert not (env.artifact / "guest-exit-code").exists()


def test_stand_in_mode_runs_the_same_path_and_destroys_its_machine(
    env: Environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", env.api.base_url)
    config = tmp_path / "local.toml"
    example = Path("config/local.example.toml").read_text(encoding="utf-8")
    config.write_text(
        example + f'\n[eval]\nexecution = "fly"\n\n[fly]\napp = "app"\n'
        f'image = "registry.fly.io/app:base"\ntoken_file = "{env.token}"\n'
        "heartbeat_seconds = 1\n",
        encoding="utf-8",
    )
    script = tmp_path / "stand-in.sh"
    script.write_text("echo marker > /artifacts/marker.txt\n", encoding="utf-8")
    run_dir = tmp_path / "stand-in-run"
    write_guest_flyctl(env.bin, env.guest_root, env.flyctl_log, run_dir / ".factory/machine.json")

    code = main(
        [
            "stand-in",
            "--config",
            str(config),
            "--run-dir",
            str(run_dir),
            "--script",
            str(script),
            "--deadline-seconds",
            "300",
        ]
    )

    assert code == 0
    assert (run_dir / "marker.txt").read_text().strip() == "marker"
    assert env.api.machines == {}
    create = next(r for r in env.api.requests if str(r["path"]).endswith("/machines"))
    deadline = int(create["body"]["config"]["metadata"]["deadline_epoch"])  # type: ignore[index]
    assert 290 <= deadline - int(time.time()) <= 300


def test_corrupt_machine_record_is_an_error_not_a_reason_to_create_another(
    env: Environment,
) -> None:
    (env.factory / "machine.json").write_text('{"id": "machine-1", "run', encoding="utf-8")
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == EXIT_TRANSPORT
    assert not any(r["method"] == "POST" for r in env.api.requests)
    assert "record is invalid" in (env.factory / "launcher.log").read_text()


def test_untrusted_job_cannot_plant_links_or_overwrite_host_state(env: Environment) -> None:
    hostile = (
        "ln -s /etc/hosts /artifacts/leak; "
        "mkdir -p /artifacts/logs; ln -s /etc /artifacts/logs/etc; "
        'echo \'{"id": "attacker"}\' > /artifacts/.factory/machine.json; '
        "echo '{}' > /artifacts/.factory/manifest.json; "
        "echo kept > /artifacts/logs/real.txt"
    )
    assert env.lifecycle(env.manifest()).run(env.request(hostile, auth=False)) == 0

    assert (env.artifact / "logs/real.txt").read_text().strip() == "kept"
    assert not (env.artifact / "leak").exists() and not (env.artifact / "leak").is_symlink()
    assert not (env.artifact / "logs/etc").exists()
    assert not any(path.is_symlink() for path in env.artifact.rglob("*"))
    # Host-managed state is untouched by what the guest wrote under .factory.
    assert json.loads((env.factory / "machine.json").read_text())["id"] == "machine-1"
    assert not (env.factory / "manifest.json").exists()
