"""INT-004: the launcher's Machine lifecycle against a fake API and a fake flyctl.

The guest side is the real init script under bash, rooted in a temp directory,
so delivery, the job loop, the file manifest, and collection are all exercised.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import subprocess
import tarfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest

from agent_factory.fly import transport
from agent_factory.fly.api import FlyMachinesClient
from agent_factory.fly.guest import stand_in_script
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
from tests.fixtures.fly.guest import start_guest, stop_guest


def fast_poll(seconds: float) -> None:
    """Observe the local guest five times as often as the manifest's whole-second interval."""
    time.sleep(seconds / 5)


class FastLifecycle(Lifecycle):
    """The launcher's own Lifecycle, observing at the fast test cadence."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("sleep", fast_poll)
        super().__init__(*args, **kwargs)


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
        return Lifecycle(manifest, self.factory, client=client, sleep=fast_poll)

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
        monkeypatch.setattr(transport, "Lifecycle", FastLifecycle)
        guest = start_guest(
            {
                **os.environ,
                "FACTORY_ROOT": str(environment.guest_root),
                # The guest's job loop picks up delivered jobs at this cadence.
                "FACTORY_WATCHDOG_SECONDS": "0.2",
                "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 3600),
            }
        )
        try:
            yield environment
        finally:
            stop_guest(guest)


_SUITE = (
    "echo suite-output; "
    "ls -A /host-home/codex /host-home/claude > /artifacts/seen-credentials.txt; "
    "cat /eval-input/controller.mjs > /artifacts/seen-input.txt; "
    "echo '{\"schema_version\": 1}' > /artifacts/run-state.json; sleep 1; "
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
    # The job's user cannot remove the root-owned directory itself, only what is in
    # it; no credential file may remain and the cleanup must not complain.
    assert [p for p in (env.guest_root / "host-home").rglob("*") if p.is_file()] == []
    assert "cannot remove" not in (env.artifact / ".factory/job/1/job.log").read_text()
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
    env.lifecycle(env.manifest()).run(env.request("sleep 1"))
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


class _ClockLater:
    """The time module with its wall clock read a few seconds ahead."""

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)

    @staticmethod
    def time() -> float:
        return time.time() + 5


def test_recovery_resumes_in_the_same_machine_with_a_fresh_deadline(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert env.lifecycle(env.manifest()).run(env.request(_SUITE)) == 7
    first = json.loads((env.factory / "machine.json").read_text())
    # The recovery starts later than the first attempt, without waiting for the clock.
    monkeypatch.setattr(transport, "time", _ClockLater())

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
        first.run(env.request("echo one; sleep 1; echo two", auth=False))

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


def test_delivery_hands_the_job_to_the_user_the_guest_init_runs_as(env: Environment) -> None:
    """The image runs its init as a non-root user, while ssh delivers as root.

    A root-owned 0700 job script is not executable by that user, so the job would
    never start. Ownership must move to the init's user before ``start`` appears.
    """
    assert env.lifecycle(env.manifest()).run(env.request("true")) == 0
    commands = [c["argv"][-1] for c in env.flyctl_calls() if c["argv"][:2] == ["ssh", "console"]]
    start = next(
        i for i, command in enumerate(commands) if "job/1/start" in command and "touch" in command
    )
    handover = commands[start]
    assert "chown -R -h -P" in handover and handover.index("chown") < handover.index("touch")
    owned = handover.split("chown -R -h -P", 1)[1].split("&&", 1)[0].split()[1:]
    # Only the job's own children change hands. The parents stay root-owned, so a
    # job cannot replace them with links that a later root delivery would follow.
    assert sorted(owned) == [
        "/artifacts/.factory/job/1",
        "/eval-input",
        "/host-home/claude",
        "/host-home/codex",
        "/run/factory/env",
    ]
    setup = next(command for command in commands if "mkdir -p /run/factory" in command)
    assert "chmod 711 /run/factory /host-home" in setup  # traversable, not listable
    # The owner is read from a directory the init itself created, never assumed.
    assert "stat -c %u:%g /artifacts/.factory/job" in handover


def test_slow_machine_start_is_waited_for_beyond_one_fly_wait_window(env: Environment) -> None:
    """Fly caps one wait at 60 s, but a first image pull on a host can take longer."""
    env.api.wait_failures.extend([408, 408])
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == 0
    waits = [r for r in env.api.requests if "/wait" in str(r["path"])]
    assert len(waits) == 3


def test_machine_that_never_starts_is_a_transport_failure(env: Environment) -> None:
    env.api.wait_failures.extend([408] * 20)
    assert env.lifecycle(env.manifest()).run(env.request("true", auth=False)) == EXIT_TRANSPORT
    assert "did not start" in (env.factory / "launcher.log").read_text()


def test_harness_delivery_is_refreshed_readable_and_not_handed_to_job(env: Environment) -> None:
    panel = env.input_dir.parent / "panel"
    panel.mkdir(mode=0o700)
    module = panel / "judge.mjs"
    module.write_text("// pinned panel")
    module.chmod(0o600)
    target = env.guest_root / "lib/panel-judging"
    target.mkdir(parents=True)
    (target / "stale.mjs").write_text("// stale")
    request = JobRequest(
        artifact_dir=env.artifact,
        script=stand_in_script("cat /lib/panel-judging/judge.mjs > /artifacts/panel.txt"),
        input_dir=env.input_dir,
        environment="\n",
        codex_auth=False,
        claude_auth=False,
        deliveries=((panel, "/lib/panel-judging"),),
    )

    assert env.lifecycle(env.manifest()).run(request) == 0
    assert (env.artifact / "panel.txt").read_text() == "// pinned panel"
    assert not (target / "stale.mjs").exists()
    assert target.stat().st_mode & 0o005 == 0o005
    assert (target / "judge.mjs").stat().st_mode & 0o004
    assert not target.stat().st_mode & 0o022
    assert not (target / "judge.mjs").stat().st_mode & 0o022
    commands = [c["argv"][-1] for c in env.flyctl_calls() if c["argv"][:2] == ["ssh", "console"]]
    setup = next(c for c in commands if "rm -rf /eval-input" in c)
    assert "/lib/panel-judging" in setup.split("&&", 1)[0]
    assert any("chown -R -h -P root:root /lib/panel-judging" in c for c in commands)
    handover = next(c for c in commands if 'chown -R -h -P "$owner"' in c)
    assert "/lib/panel-judging" not in handover


def test_operator_resume_seeds_a_replacement_machine_from_the_collected_checkpoint(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundles: list[list[str]] = []
    put_file = transport.FlyTransport.put_file

    def record_bundle(
        self: transport.FlyTransport, local: Path, remote: str, **kwargs: Any
    ) -> None:
        if remote.startswith("/tmp/factory-bundle-"):
            with tarfile.open(local) as archive:
                bundles.append(archive.getnames())
        put_file(self, local, remote, **kwargs)

    monkeypatch.setattr(transport.FlyTransport, "put_file", record_bundle)
    assert env.lifecycle(env.manifest()).run(env.request(_SUITE)) == 7
    first = json.loads((env.factory / "machine.json").read_text())
    job_log = (env.factory / "job/1/job.log").read_bytes()
    # The Machine was destroyed after the claim settled; a new one starts empty.
    env.api.machines.clear()
    artifacts = env.guest_root / "artifacts"
    for child in artifacts.iterdir():
        if child.name != ".factory":
            subprocess.run(["rm", "-rf", str(child)], check=True)
    subprocess.run(["rm", "-rf", str(artifacts / ".factory/job/1")], check=True)
    # Without the operator's grant the gone Machine is still a lost Machine.
    assert (
        env.lifecycle(env.manifest("run-2", expect_checkpoint=True)).run(
            env.request("true", auth=False)
        )
        == EXIT_MACHINE_LOST
    )

    resume = env.manifest("run-3", expect_checkpoint=True, seed_checkpoint=True)
    code = env.lifecycle(resume).run(
        env.request("cat /artifacts/run-state.json > /artifacts/seen-state.json", auth=False)
    )

    assert code == 0
    record = json.loads((env.factory / "machine.json").read_text())
    assert record["id"] != first["id"] and record["job"] == 2
    assert {"id": first["id"], "disposal": "gone"} in record["history"]
    assert json.loads((env.artifact / "seen-state.json").read_text()) == {"schema_version": 1}
    # The earlier job's collected evidence and the host-managed tree were not overwritten.
    assert (env.factory / "job/1/job.log").read_bytes() == job_log
    assert not (artifacts / ".factory/machine.json").exists()
    assert "seeding a new Machine" in (env.factory / "launcher.log").read_text()
    assert record["seed"] == "done"
    # Root's tar restores a "." entry's owner and mode onto /artifacts, taking it from
    # the guest's user (EACCES on mkdir /artifacts/evidence); the seed must not carry one.
    seeded = next(names for names in bundles if "./run-state.json" in names)
    assert "." not in seeded and "./" not in seeded
    assert not any(name.startswith("./.factory") for name in seeded)

    # A launcher that died mid-seed leaves it pending; the next one adopting the
    # replacement Machine seeds again before checking the checkpoint.
    (env.factory / "machine.json").write_text(json.dumps({**record, "seed": "pending"}))
    (artifacts / "run-state.json").unlink()
    retry = env.manifest("run-4", expect_checkpoint=True)
    assert env.lifecycle(retry).run(env.request("true", auth=False)) == 0
    assert json.loads((env.factory / "machine.json").read_text())["seed"] == "done"


def test_explicit_resume_seeds_after_archiving_the_prior_launcher_record(env: Environment) -> None:
    assert env.lifecycle(env.manifest()).run(env.request(_SUITE)) == 7
    env.api.machines.clear()
    artifacts = env.guest_root / "artifacts"
    (artifacts / "run-state.json").unlink()
    subprocess.run(["rm", "-rf", str(artifacts / ".factory/job/1")], check=True)
    env.factory.rename(env.factory.with_name(".previous-factory"))
    env.factory.mkdir()

    resume = env.manifest("run-2", expect_checkpoint=True, seed_checkpoint=True)
    lifecycle = env.lifecycle(resume)
    request = env.request("cat /artifacts/run-state.json > /artifacts/seen-state.json", auth=False)
    assert lifecycle.run(request) == 0
    assert json.loads((env.artifact / "seen-state.json").read_text()) == {"schema_version": 1}
    assert json.loads((env.factory / "machine.json").read_text())["seed"] == "done"


def test_job_script_without_an_engine_is_unchanged_and_archon_has_its_own() -> None:
    import hashlib

    from agent_factory.fly.guest import job_script

    base: dict[str, object] = {
        "repositories": {"runner": "git@github.com:o/r.git", "skills": "https://x/s.git"},
        "commits": {"runner": "a1", "skills": "b2"},
    }
    text = job_script(base, "exec /suite/run")
    # Pinned from the script before the Archon branch existed.
    assert (
        hashlib.sha256(text.encode()).hexdigest()
        == "c4d3b488a984d46b460582ea8ae4bafb23a6b4c13ddf240e9502e4420383dd67"
    )
    archon = job_script(
        {
            "engine": "archon",
            "repositories": {"workflows": "git@github.com:o/w.git"},
            "commits": {"workflows": "w1", "archon": "ar1"},
        },
        "exec /suite/run",
    )
    assert "go build" not in archon and "agent-runner-source" not in archon
    assert archon.index("trap ") < archon.index("git clone")
    assert "https://github.com/o/w.git" in archon and "checkout --detach w1" in archon
    assert "AND_SCENE_EXPECT_WORKFLOWS_COMMIT=w1" in archon
    assert "AND_SCENE_EXPECT_ARCHON_COMMIT=ar1" in archon
    assert archon.rstrip().endswith("exec /suite/run")


def test_archon_repetition_steps_start_observe_collect_and_destroy(env: Environment) -> None:
    lifecycle = env.lifecycle(env.manifest(owner="agent-evals-archon"))
    assert lifecycle.start(env.request("echo hi; exit 3")) == 0
    state = ""
    for _ in range(100):
        state = lifecycle.observe(1).state
        if state == "done":
            break
        time.sleep(0.2)
    assert state == "done"
    assert lifecycle.collect(env.artifact, 1) == 3
    create = next(r for r in env.api.requests if r["method"] == "POST")
    assert "agent-evals-archon" in json.dumps(create["body"])
    metadata = cast(dict[str, Any], create["body"])["config"]["metadata"]
    assert metadata["factory-owner"] == "agent-evals-archon"
    # Collection leaves the Machine in place; destroying it is a separate, owned step.
    assert lifecycle.observe(1).state == "done"
    assert lifecycle.destroy_machine() == "destroyed"
    assert lifecycle.destroy_machine() == "absent"
    assert lifecycle.observe(1).state == "lost"


def test_eval_cli_image_recipe_pins_archon_bun_openspec_and_the_model_clis() -> None:
    from agent_factory.eval_cli import archon_dockerfile

    base = "FROM node:22\nUSER pwuser\nWORKDIR /workspace\n"
    runner = "0151294" + "0" * 33
    text = archon_dockerfile(
        base, "1.4.2", "abc123", "1.17.0", "0.169.0", "2.1.311", runner_commit=runner
    )
    assert text.startswith(base.rstrip())
    assert 'rev-parse HEAD)" = "abc123"' in text and "bun@1.4.2" in text
    assert "openspec@1.17.0" in text and "/opt/archon-eval/sandbox-sync-home.sh" in text
    # The pinned CLIs replace the base image's unpinned copies, and the build checks them.
    assert "@openai/codex@0.169.0" in text and "@anthropic-ai/claude-code@2.1.311" in text
    assert '[ "$(codex --version)" = "codex-cli 0.169.0" ]' in text
    assert '[ "$(claude --version)" = "2.1.311 (Claude Code)" ]' in text
    assert "ENV DISABLE_AUTOUPDATER=1" in text
    # The image carries its Runner build provenance for the suite's native measurement.
    assert f"ENV AGENT_RUNNER_SOURCE_COMMIT={runner}" in text
    assert text.rstrip().endswith("USER pwuser\nWORKDIR /workspace")


def test_eval_cli_image_start_requires_exact_model_cli_versions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_factory.eval_cli import build_parser

    argv = [
        "image", "start", "--config", "c", "--work-dir", "w", "--eval-id", "e",
        "--archon-dir", "a", "--archon-commit", "h", "--runner-dir", "r", "--runner-commit", "h",
    ]  # fmt: skip
    pinned = build_parser().parse_args(
        [*argv, "--codex-version", "0.169.0", "--claude-code-version", "2.1.311"]
    )
    assert (pinned.codex_version, pinned.claude_code_version) == ("0.169.0", "2.1.311")
    for extra in (
        ["--codex-version", "0.169.0"],
        ["--codex-version", "latest", "--claude-code-version", "2.1.311"],
        ["--codex-version", "0.169.0", "--claude-code-version", "2.1.311; rm -rf /"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args([*argv, *extra])
    capsys.readouterr()


def test_eval_cli_poll_reports_lost_after_repeated_unreachable_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_factory import eval_cli
    from agent_factory.fly.transport import Observation

    factory = tmp_path / "run/.factory"
    factory.mkdir(parents=True)
    (factory / "eval-state.json").write_text('{"phase": "delivered", "job": 1}')

    class Unreachable:
        def observe(self, _job: int) -> Observation:
            return Observation("unreachable")

    def fly_settings(_config: Path) -> tuple[None, object]:
        return None, object()

    def lifecycle(*_args: object) -> Unreachable:
        return Unreachable()

    monkeypatch.setattr(eval_cli, "_fly", fly_settings)
    monkeypatch.setattr(eval_cli, "_lifecycle", lifecycle)
    states: list[str] = []
    for _ in range(eval_cli.LOST_AFTER_FAILURES):
        eval_cli.main(["poll", "--config", "c", "--run-dir", str(tmp_path / "run")])
        states.append(json.loads(capsys.readouterr().out)["state"])
    assert states == ["unreachable"] * (eval_cli.LOST_AFTER_FAILURES - 1) + ["lost"]


def _jwt(expires: int) -> str:
    def part(value: object) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'exp': expires})}.signature"


def _write_codex_login(home: Path, token: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    tokens = {
        "id_token": "id-token",
        "access_token": token,
        "refresh_token": "refresh-secret",
        "account_id": "acct-1",
    }
    (home / "auth.json").write_text(json.dumps({"tokens": tokens}), encoding="utf-8")


def test_codex_login_is_delivered_as_an_access_token_refreshed_only_when_short(
    tmp_path: Path,
) -> None:
    from agent_factory.fly.codex_login import delivered_login

    home = tmp_path / "codex"
    _write_codex_login(home, _jwt(10_000))
    refreshes: list[int] = []

    def refresh() -> None:
        refreshes.append(1)
        _write_codex_login(home, _jwt(200_000))

    login = delivered_login(5_000, home=home, now=lambda: 0, refresh=refresh)
    assert refreshes == [] and login.expires_at == 10_000
    lines = login.environment_lines()
    assert f"ARCHON_CODEX_CHATGPT_ACCESS_TOKEN={_jwt(10_000)}" in lines
    assert "ARCHON_CODEX_CHATGPT_ACCOUNT_ID=acct-1" in lines
    assert "refresh-secret" not in lines and _jwt(10_000) not in repr(login)
    # The judge's own `codex exec` gets a login file that cannot refresh.
    when = datetime.datetime(2026, 11, 13, 8, 0, tzinfo=datetime.UTC)
    written = json.loads(login.login_file(when))
    assert written["tokens"] == {
        "id_token": "id-token",
        "access_token": _jwt(10_000),
        "refresh_token": "",
        "account_id": "acct-1",
    }
    assert written["last_refresh"] == "2026-11-13T08:00:00Z"
    login = delivered_login(50_000, home=home, now=lambda: 0, refresh=refresh)
    assert refreshes == [1] and login.expires_at == 200_000


def test_codex_login_missing_or_still_short_is_refused_without_the_token(
    tmp_path: Path,
) -> None:
    from agent_factory.fly.codex_login import CodexLoginError, delivered_login

    home = tmp_path / "codex"
    with pytest.raises(CodexLoginError, match="codex login"):
        delivered_login(1, home=home, now=lambda: 0, refresh=lambda: None)
    _write_codex_login(home, _jwt(100))
    with pytest.raises(CodexLoginError, match="expire") as caught:
        delivered_login(1_000, home=home, now=lambda: 0, refresh=lambda: None)
    assert _jwt(100) not in str(caught.value) and "refresh-secret" not in str(caught.value)


_FAKE_CODEX = """
import json, os, sys
from pathlib import Path
initialized = False
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        if set(message["params"]["clientInfo"]) != {"name", "title", "version"}:
            error = {"code": -32600, "message": "Invalid request: missing field `version`"}
            print(json.dumps({"id": message["id"], "error": error}), flush=True)
            continue
        initialized = True
        print(json.dumps({"id": message["id"], "result": {}}), flush=True)
    elif message.get("method") == "account/read" and not initialized:
        error = {"code": -32600, "message": "Not initialized"}
        print(json.dumps({"id": message["id"], "error": error}), flush=True)
    elif message.get("method") == "account/read":
        assert message["params"] == {"refreshToken": True}
        account = None
        if os.environ.get("FAKE_CODEX_ACCOUNT") != "none":
            Path(os.environ["CODEX_HOME"], "auth.json").write_text(os.environ["FAKE_CODEX_LOGIN"])
            account = {"type": "chatgpt"}
        reply = {"account": account, "requiresOpenaiAuth": True}
        print(json.dumps({"id": message["id"], "result": reply}), flush=True)
"""


def test_codex_refresh_waits_for_the_macs_codex_to_answer_and_save_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from agent_factory.fly.codex_login import CodexLoginError, read_login, refresh_native_login

    home = tmp_path / "codex"
    _write_codex_login(home, _jwt(100))
    fresh = tmp_path / "fresh"
    _write_codex_login(fresh, _jwt(900_000))
    bin_dir = tmp_path / "codex-bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(f"#!{sys.executable}\n{_FAKE_CODEX}", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("FAKE_CODEX_LOGIN", (fresh / "auth.json").read_text())
    refresh_native_login()
    assert read_login(home).expires_at == 900_000
    monkeypatch.setenv("FAKE_CODEX_ACCOUNT", "none")
    with pytest.raises(CodexLoginError, match="codex login"):
        refresh_native_login()


def test_eval_cli_start_sends_the_codex_access_token_and_not_the_login_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from types import SimpleNamespace

    from agent_factory import eval_cli
    from agent_factory.fly.codex_login import CodexLogin

    run = tmp_path / "run"
    factory = run / ".factory"
    factory.mkdir(parents=True)
    manifest = {
        "engine": "archon",
        "repositories": {"workflows": "https://github.com/o/w.git"},
        "commits": {"workflows": "w1", "archon": "ar1"},
        "deadline": {"total_seconds": 100_000, "collection_grace_seconds": 900},
    }
    (factory / "manifest.json").write_text(json.dumps(manifest))
    launch: dict[str, object] = {
        "dry_run": True,
        "script": "exec /suite/run",
        "input_dir": str(tmp_path),
        "env_files": [],
        "env_names": [],
        "mount_codex_auth": True,
    }
    (factory / "parsed-launch.json").write_text(json.dumps(launch))
    image = tmp_path / "image.json"
    image.write_text(json.dumps({"digest": "sha256:" + "a" * 64, "repository": "r"}))
    minimums: list[int] = []
    requests: list[JobRequest] = []

    def login(minimum: int) -> CodexLogin:
        minimums.append(minimum)
        return CodexLogin("id-secret", "access-secret", "acct-1", 0)

    class Recording:
        def __init__(self, *_args: object) -> None:
            pass

        def start(self, request: JobRequest) -> int:
            requests.append(request)
            return 0

    fly = SimpleNamespace(
        app="app",
        token_file=tmp_path / "token",
        region="ewr",
        cpu_kind="shared",
        cpus=4,
        memory_mb=8192,
        heartbeat_seconds=20,
    )

    def fly_settings(_config: Path) -> tuple[None, object]:
        return None, fly

    monkeypatch.setattr(eval_cli, "_fly", fly_settings)
    monkeypatch.setattr(eval_cli, "delivered_login", login)
    monkeypatch.setattr(eval_cli, "Lifecycle", Recording)
    argv = ["start", "--config", "c", "--run-dir", str(run), "--image-file", str(image)]
    assert eval_cli.main(argv) == 0
    assert "access-secret" not in capsys.readouterr().out
    # The token must outlive the whole job, even past the one-day floor.
    assert minimums == [100_900]
    assert requests[0].codex_auth is False
    assert "ARCHON_CODEX_CHATGPT_ACCESS_TOKEN=access-secret" in requests[0].environment
    login_file = requests[0].codex_login_file
    assert login_file is not None
    assert json.loads(login_file)["tokens"]["refresh_token"] == ""


def test_built_codex_login_replaces_the_macs_file_on_the_machine(env: Environment) -> None:
    import dataclasses

    suite = (
        "cat /host-home/codex/auth.json > /artifacts/seen-codex.txt; "
        'echo \'{"evaluation_status": "completed"}\' > /artifacts/result.json'
    )
    request = dataclasses.replace(
        env.request(suite, auth=False), codex_login_file=b'{"built": true}'
    )
    assert env.lifecycle(env.manifest()).run(request) == 0
    assert (env.artifact / "seen-codex.txt").read_text() == '{"built": true}'
    assert [p for p in (env.guest_root / "host-home").rglob("*") if p.is_file()] == []


# -- INT-001 (Halo eval suite): scoring machine, sizes, delivery limits, teardown ------


def _commit(repo: Path, files: dict[str, str]) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "-m", "c"],
        check=True,
    )  # fmt: skip
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


class Machines:
    """eval_cli's real commands against the fake API, one real guest per fake Machine."""

    def __init__(self, tmp_path: Path, api: FakeMachinesApi) -> None:
        self.api = api
        self.tmp = tmp_path
        self.roots = tmp_path / "machines"
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.home = tmp_path / "home"
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude/.credentials.json").write_text("claude-secret", encoding="utf-8")
        self.archon_commit = _commit(tmp_path / "archon-image", {"README": "archon"})
        self.workflows = tmp_path / "workflows"
        self.workflows_commit = _commit(self.workflows, {"README": "workflows"})
        self.suite = tmp_path / "suite"
        for name, text in {
            "fixture/start.json": "{}",
            "AGENTS-runbook.md": "runbook",
            "hidden/MenuTest.java": "hidden",
            "score/score.mjs": "score",
            "calibration/half.patch": "calibration",
        }.items():
            (self.suite / name).parent.mkdir(parents=True, exist_ok=True)
            (self.suite / name).write_text(text, encoding="utf-8")
        self.panel = tmp_path / "panel-judging"
        self.panel.mkdir()
        (self.panel / "judge.mjs").write_text("judge", encoding="utf-8")
        self.env_file = tmp_path / "suite.env"
        self.env_file.write_text(
            "GITHUB_TOKEN=github-secret\nCLAUDE_CODE_OAUTH_TOKEN=claude-token-secret\n",
            encoding="utf-8",
        )
        self.image = tmp_path / "image.json"
        self.image.write_text(json.dumps({"digest": "sha256:" + "a" * 64, "repository": "r"}))

    def boot(self, machine_id: str) -> None:
        """Give a new fake Machine the image's paths, then start its guest init."""
        root = self.roots / machine_id
        archon = root / "opt/archon"
        if not archon.exists():
            subprocess.run(
                ["git", "clone", "-q", str(self.tmp / "archon-image"), str(archon)], check=True
            )
        sync = root / "opt/archon-eval/sandbox-sync-home.sh"
        sync.parent.mkdir(parents=True, exist_ok=True)
        sync.write_text('#!/bin/sh\nmkdir -p "$FACTORY_ROOT/workspace/home"\n')
        sync.chmod(0o755)
        # A Machine's variables come only from the delivered environment file.
        guest_env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"GITHUB_TOKEN", "GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}
        }
        self.processes[machine_id] = start_guest(
            {
                **guest_env,
                "FACTORY_ROOT": str(root),
                "FACTORY_WATCHDOG_SECONDS": "0.2",
                "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 3600),
            },
            quiet=True,
        )

    def close(self) -> None:
        for process in self.processes.values():
            stop_guest(process)

    def run_dir(self, name: str, manifest: dict[str, object], launch: dict[str, object]) -> Path:
        run = self.tmp / name
        (run / ".factory").mkdir(parents=True)
        (run / ".factory/manifest.json").write_text(json.dumps(manifest))
        (run / ".factory/parsed-launch.json").write_text(json.dumps({"dry_run": True, **launch}))
        return run

    def manifest(self, unit_key: str, **extra: object) -> dict[str, object]:
        return {
            "schema": "archon-fly-eval/1",
            "engine": "archon",
            "owner": "agent-evals-archon",
            "run_id": unit_key,
            "claim_id": "e1",
            "unit_key": unit_key,
            "nonce": f"nonce-{unit_key}",
            "deadline": {"total_seconds": 600, "collection_grace_seconds": 60},
            "repositories": {"workflows": str(self.workflows)},
            "commits": {"workflows": self.workflows_commit, "archon": self.archon_commit},
            **extra,
        }

    def cli(self, command: str, run: Path, *extra: str) -> tuple[int, dict[str, object]]:
        from agent_factory import eval_cli

        argv = [command, "--config", "c", "--run-dir", str(run), *extra]
        code = eval_cli.main(argv)
        return code, self.output

    output: dict[str, object] = {}

    def creates(self) -> list[dict[str, Any]]:
        return [
            cast(dict[str, Any], r["body"])
            for r in self.api.requests
            if r["method"] == "POST" and str(r["path"]).endswith("/machines")
        ]


@pytest.fixture
def machines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[Machines]:
    import functools
    from types import SimpleNamespace

    from agent_factory import eval_cli
    from agent_factory.fly.codex_login import CodexLogin

    with FakeMachinesApi() as api:
        fleet = Machines(tmp_path, api)
        bin_dir = tmp_path / "bin"
        write_guest_flyctl(
            bin_dir, fleet.roots, tmp_path / "flyctl.log", tmp_path / "none", per_machine=True
        )
        token = tmp_path / "token"
        token.write_text("secret-deploy-token\n", encoding="utf-8")
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("HOME", str(fleet.home))
        monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", api.base_url)
        # The Claude login comes from the test HOME's file, never this Mac's Keychain.
        monkeypatch.setattr(
            transport,
            "resolve_claude_login",
            functools.partial(transport.resolve_claude_login, platform="linux"),
        )
        fly = SimpleNamespace(
            app="app",
            token_file=token,
            region="ewr",
            cpu_kind="shared",
            cpus=2,
            memory_mb=4096,
            heartbeat_seconds=1,
        )

        def fly_settings(_config: Path) -> tuple[None, object]:
            return None, fly

        def login(_minimum: int) -> CodexLogin:
            return CodexLogin("id-secret", "access-secret", "acct-1", 0)

        monkeypatch.setattr(eval_cli, "_fly", fly_settings)
        monkeypatch.setattr(eval_cli, "delivered_login", login)
        original_emit = eval_cli._emit  # pyright: ignore[reportPrivateUsage]

        def emit(value: dict[str, object]) -> None:
            fleet.output = dict(value)
            original_emit(value)

        monkeypatch.setattr(eval_cli, "_emit", emit)
        try:
            yield fleet
        finally:
            fleet.close()
            capsys.readouterr()


def _finish(fleet: Machines, run: Path) -> str:
    """Start, wait for, collect and destroy one repetition step; return its Machine."""
    code, started = fleet.cli("start", run, "--image-file", str(fleet.image))
    assert code == 0, started
    machine_id = str(started["machine"])
    fleet.boot(machine_id)
    state = ""
    for _ in range(300):
        state = str(fleet.cli("poll", run)[1]["state"])
        if state == "done":
            break
        time.sleep(0.1)
    assert state == "done"
    assert fleet.cli("collect", run)[0] == 0
    assert fleet.cli("destroy", run)[0] == 0
    return machine_id


@pytest.mark.parametrize(
    ("fault", "diagnostic"),
    [
        ("fly-token", "deploy token file is unreadable"),
        ("fly-app", "the Fly app is not reachable"),
        ("claude", "no usable Claude login"),
        ("codex", "no usable Codex login"),
        ("github", "neither GITHUB_TOKEN nor GH_TOKEN"),
    ],
)
def test_eval_preflight_refuses_missing_inputs_without_reporting_values(
    machines: Machines,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fault: str,
    diagnostic: str,
) -> None:
    from functools import partial

    from agent_factory import eval_cli
    from agent_factory.fly.api import FlyApiError
    from agent_factory.fly.codex_login import delivered_login

    if fault == "fly-token":
        (machines.tmp / "token").unlink()
    if fault == "fly-app":

        def app_unavailable(_client: FlyMachinesClient) -> dict[str, object]:
            raise FlyApiError("/v1/apps/app", 503, "unavailable")

        monkeypatch.setattr(FlyMachinesClient, "get_app", app_unavailable)
    if fault == "claude":
        machines.env_file.write_text("GITHUB_TOKEN=github-secret\n", encoding="utf-8")
        monkeypatch.setattr(
            eval_cli,
            "resolve_claude_login",
            partial(
                eval_cli.resolve_claude_login, home=machines.tmp / "no-login", platform="linux"
            ),
        )
    if fault == "codex":
        monkeypatch.setattr(
            eval_cli,
            "delivered_login",
            partial(delivered_login, home=machines.tmp / "no-login", refresh=lambda: None),
        )
    if fault == "github":
        machines.env_file.write_text(
            "CLAUDE_CODE_OAUTH_TOKEN=claude-token-secret\n", encoding="utf-8"
        )

    code = eval_cli.main(
        [
            "preflight",
            "--config",
            "c",
            "--owner",
            "agent-evals-archon",
            "--env-file",
            str(machines.env_file),
            "--claude-auth",
            "--codex-auth",
        ]
    )
    assert code == 1
    assert machines.output["ok"] is False
    assert diagnostic in str(machines.output["errors"])
    captured = capsys.readouterr()
    for secret in ("github-secret", "secret-deploy-token", "claude-token-secret", "access-secret"):
        assert secret not in captured.out + captured.err
    assert all(request["method"] == "GET" for request in machines.api.requests)
    assert machines.creates() == []


_SEEN = (
    "{ "
    'echo "env_file=$([ -e /run/factory/env ] && echo yes || echo no)"; '
    'echo "github_token=${GITHUB_TOKEN:+yes}"; echo "gh_token=${GH_TOKEN:+yes}"; '
    'echo "claude_token=${CLAUDE_CODE_OAUTH_TOKEN:+yes}"; '
    # The token's digest, so its value is checked without being written down.
    'python3 -c \'import hashlib, os; print("claude_digest=" + hashlib.sha256('
    'os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").encode()).hexdigest())\'; '
    # The names in the environment file, never their values.
    "{ sed 's/=.*//; s/^/env_name=/' /run/factory/env 2>/dev/null || true; }; "
    "ls -A /host-home/codex /host-home/claude; "
    "(cd /eval-input && find . -type f | sort); "
    "(cd /lib/panel-judging && find . -type f | sort); "
    "} > /artifacts/{step}/seen.txt"
)


def test_scoring_machine_runs_after_the_run_machine_with_only_the_claude_token(
    machines: Machines,
) -> None:
    from agent_factory.fly.guest import job_script

    fleet = machines
    suite, panel = fleet.suite, fleet.panel
    run_script = (
        "mkdir -p /artifacts/run; "
        + _SEEN.replace("{step}", "run")
        + "; echo '{}' > /artifacts/run/delivery.json"
    )
    run_manifest = fleet.manifest(
        "e1-rep-1",
        delivery=[str(suite / "fixture"), str(suite / "AGENTS-runbook.md"), str(panel)],
        private=[str(suite / "hidden"), str(suite / "score"), str(suite / "calibration")],
    )
    run = fleet.run_dir(
        "run",
        run_manifest,
        {
            "script": run_script,
            "input_dir": str(suite),
            "deliveries": [[str(panel), "/lib/panel-judging"]],
            "env_files": [str(fleet.env_file)],
            "env_names": [],
            "mount_codex_auth": True,
            "mount_claude_auth": True,
        },
    )
    score_script = (
        "mkdir -p /artifacts/score; "
        + _SEEN.replace("{step}", "score")
        + '; echo \'{"evaluation_status": "completed"}\' > /artifacts/score/result.json'
    )
    score = fleet.run_dir(
        "score",
        fleet.manifest(
            "e1-rep-1-score",
            job="score",
            machine={"cpu_kind": "performance", "cpus": 4, "memory_mb": 16384},
            delivery=[str(suite / "score"), str(suite / "hidden"), str(panel)],
        ),
        {
            "script": score_script,
            "input_dir": str(suite),
            "deliveries": [[str(panel), "/lib/panel-judging"]],
            # The suite file holds the GitHub token too; only the Claude token goes.
            "env_files": [str(fleet.env_file)],
            "mount_codex_auth": True,
            "mount_claude_auth": True,
        },
    )

    run_machine = _finish(fleet, run)
    score_machine = _finish(fleet, score)

    ran = (run / "run/seen.txt").read_text().splitlines()
    assert "env_file=yes" in ran and "github_token=yes" in ran
    # Only the run list reached the run machine: no hidden tests, scorer or calibration.
    inputs = [line for line in ran if line.startswith("./")]
    assert inputs == ["./AGENTS-runbook.md", "./fixture/start.json", "./judge.mjs"]
    scored = (score / "score/seen.txt").read_text().splitlines()
    assert "env_file=yes" in scored and "claude_token=yes" in scored
    assert "github_token=" in scored and "gh_token=" in scored
    names = [line for line in scored if line.startswith("env_name=")]
    assert names == ["env_name=CLAUDE_CODE_OAUTH_TOKEN"]
    digest = hashlib.sha256(b"claude-token-secret").hexdigest()
    assert f"claude_digest={digest}" in scored
    # The token is the judges' whole Claude login: no credentials file goes with it.
    assert "auth.json" in scored and ".credentials.json" not in scored
    inputs = [line for line in scored if line.startswith("./")]
    assert inputs == ["./hidden/MenuTest.java", "./score/score.mjs", "./judge.mjs"]
    assert json.loads((score / "score/result.json").read_text())["evaluation_status"]
    # The scoring job built nothing and checked no commit, and it finished and cleaned up.
    job = fleet.roots / score_machine / "artifacts/.factory/job/1"
    script = (job / "job.sh").read_text()
    for absent in ("git clone", "rev-parse", "go build"):
        assert absent not in script
    assert (job / "DONE").is_file()
    assert [p for p in (fleet.roots / score_machine / "host-home").rglob("*") if p.is_file()] == []
    assert not (fleet.roots / score_machine / "run/factory/env").exists()
    # The run machine's job is the Archon job, unchanged.
    assert (fleet.roots / run_machine / "artifacts/.factory/job/1/DONE").is_file()
    delivered = json.loads((run / ".factory/manifest.json").read_text())
    assert job_script(delivered, run_script) == job_script(
        {key: value for key, value in delivered.items() if key not in {"delivery", "private"}},
        run_script,
    )
    # Sizes: the run machine keeps the configured size; the scoring machine has its own.
    run_create, score_create = fleet.creates()

    def size(create: dict[str, Any]) -> dict[str, object]:
        guest = create["config"]["guest"]
        return {key: guest[key] for key in ("cpu_kind", "cpus", "memory_mb")}

    assert size(run_create) == {"cpu_kind": "shared", "cpus": 2, "memory_mb": 4096}
    assert size(score_create) == {"cpu_kind": "performance", "cpus": 4, "memory_mb": 16384}
    assert score_create["config"]["metadata"]["unit_key"] == "e1-rep-1-score"
    assert "secret" not in json.dumps(fleet.creates())
    for folder in (run, score):
        everything = "".join(
            p.read_text(errors="replace") for p in folder.rglob("*") if p.is_file()
        )
        for secret in ("github-secret", "claude-token-secret", "access-secret"):
            assert secret not in everything
    assert run_machine not in fleet.api.machines and score_machine not in fleet.api.machines


def test_archon_job_script_is_unchanged_and_an_unknown_job_is_refused() -> None:
    import hashlib

    from agent_factory.fly.guest import job_script

    manifest: dict[str, object] = {
        "engine": "archon",
        "repositories": {"workflows": "git@github.com:o/w.git"},
        "commits": {"workflows": "w1", "archon": "ar1"},
    }
    # Pinned from the Archon job script before the scoring job existed.
    assert (
        hashlib.sha256(job_script(manifest, "exec /suite/run").encode()).hexdigest()
        == "96ad85a0d9340f89cd796b45139f95492870cb1af988bb15c3444de9540d742e"
    )
    with pytest.raises(ValueError, match="unknown guest job"):
        job_script({**manifest, "job": "scoring"}, "exec /suite/run")


@pytest.mark.parametrize(
    ("change", "named"),
    [
        # A harness folder that is not on the delivery list.
        ({"deliveries": "calibration"}, "calibration"),
        # A listed path under a private path, as a run list naming hidden tests would be.
        ({"delivery": "hidden"}, "hidden"),
        # With only a private list, an input folder that holds a private path.
        ({"delivery": None}, "hidden"),
    ],
)
def test_delivery_outside_the_lists_is_refused_before_any_machine(
    machines: Machines, change: dict[str, object], named: str
) -> None:
    fleet = machines
    suite = fleet.suite
    delivery: list[str] | None = [str(suite / "fixture"), str(fleet.panel)]
    deliveries = [[str(fleet.panel), "/lib/panel-judging"]]
    if change.get("deliveries"):
        deliveries.append([str(suite / "calibration"), "/lib/panel-judging/extra"])
    if "delivery" in change:
        delivery = None if change["delivery"] is None else [*delivery, str(suite / "hidden")]
    extra: dict[str, object] = {"private": [str(suite / "hidden")]}
    if delivery is not None:
        extra["delivery"] = delivery
    run = fleet.run_dir(
        "run",
        fleet.manifest("e1-rep-1", **extra),
        {
            "script": "true",
            "input_dir": str(suite),
            "deliveries": deliveries,
            "env_files": [str(fleet.env_file)],
            "mount_claude_auth": True,
        },
    )
    code, output = fleet.cli("start", run, "--image-file", str(fleet.image))
    assert code == 1
    assert str(suite / named) in str(output["error"])
    assert fleet.creates() == []


@pytest.mark.parametrize("entry", ["CLAUDE_CODE_OAUTH_TOKEN=", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_scoring_machine_without_a_claude_token_gets_no_environment_file(
    machines: Machines, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    fleet = machines
    # No usable token: the GitHub token stays on the Mac and the login falls back.
    # A bare name takes nothing from this process, even when it holds a token.
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "mac-token-secret")
    fleet.env_file.write_text(f"GITHUB_TOKEN=github-secret\n{entry}\n", encoding="utf-8")
    score_script = "mkdir -p /artifacts/score; " + _SEEN.replace("{step}", "score")
    score = fleet.run_dir(
        "score",
        fleet.manifest("e1-rep-1-score", job="score"),
        {
            "script": score_script,
            "input_dir": str(fleet.suite),
            "env_files": [str(fleet.env_file)],
            "mount_claude_auth": True,
        },
    )
    _finish(fleet, score)
    scored = (score / "score/seen.txt").read_text().splitlines()
    assert "env_file=no" in scored
    assert "github_token=" in scored and "claude_token=" in scored
    assert not [line for line in scored if line.startswith("env_name=")]
    # The test HOME's credentials file is the login, as with no environment file.
    assert ".credentials.json" in scored
    everything = "".join(p.read_text(errors="replace") for p in score.rglob("*") if p.is_file())
    assert "mac-token-secret" not in everything and "github-secret" not in everything


def test_scoring_launch_plan_with_environment_variables_is_refused(machines: Machines) -> None:
    fleet = machines
    run = fleet.run_dir(
        "score",
        fleet.manifest("e1-rep-1-score", job="score"),
        {"script": "true", "input_dir": str(fleet.suite), "env_names": ["GITHUB_TOKEN"]},
    )
    code, output = fleet.cli("start", run, "--image-file", str(fleet.image))
    assert code == 1 and "no environment variables" in str(output["error"])
    assert fleet.creates() == []


def test_machine_size_must_be_well_formed(machines: Machines) -> None:
    fleet = machines
    for size in ({"cpus": 0}, {"gpus": 1}, {"cpu_kind": "huge"}, {"memory_mb": "16g"}):
        run = fleet.run_dir(
            f"run-{len(fleet.creates())}-{sorted(size)[0]}",
            fleet.manifest("e1-rep-1", machine=size),
            {"script": "true", "input_dir": str(fleet.suite), "env_files": []},
        )
        code, output = fleet.cli("start", run, "--image-file", str(fleet.image))
        assert code == 1 and "machine" in str(output["error"]), size
    assert fleet.creates() == []


def test_teardown_destroys_the_evals_run_and_scoring_machines_only(
    machines: Machines,
) -> None:
    from agent_factory import eval_cli

    fleet = machines
    for unit, owner in (
        ("e1-rep-1", "agent-evals-archon"),
        ("e1-rep-1-score", "agent-evals-archon"),
        ("e10-rep-1-score", "agent-evals-archon"),
        ("e1-rep-1-scorex", "agent-evals-archon"),
        ("e1-rep-2", "agent-factory"),
    ):
        fleet.api.machines[unit] = {
            "id": unit,
            "state": "started",
            "config": {"metadata": {"factory-owner": owner, "unit_key": unit}},
        }
    assert eval_cli.main(["teardown", "--config", "c", "--eval-id", "e1", "--destroy"]) == 0
    destroyed = sorted(
        str(r["path"]).rsplit("/", 1)[-1].split("?")[0]
        for r in fleet.api.requests
        if r["method"] == "DELETE"
    )
    assert destroyed == ["e1-rep-1", "e1-rep-1-score"]


@pytest.mark.parametrize(
    ("condition", "deleted", "reason"),
    [
        ("unique", True, None),
        ("shared", False, "shared with other-eval"),
        ("missing", False, "the eval tag is absent"),
        ("retagged", False, "registry tags changed"),
        ("listed-late", False, "registry tags changed"),
        ("kept", False, "kept machines still need the image"),
    ],
)
def test_teardown_deletes_only_its_exclusive_registry_digest(
    machines: Machines,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    condition: str,
    deleted: bool,
    reason: str | None,
) -> None:
    from agent_factory import eval_cli

    assert machines.api.machines == {}
    own = "archon-eval-e1"
    digest = "sha256:" + "a" * 64
    different = "sha256:" + "b" * 64
    deletions: list[tuple[str, str]] = []
    lists = 0
    own_reads = 0

    class Registry:
        def list_machines(self, key: str, value: str) -> list[dict[str, object]]:
            assert (key, value) == ("factory-owner", "agent-evals-archon")
            return []

        def list_tags(self, repository: str) -> list[str]:
            nonlocal lists
            assert repository == "registry.fly.io/app"
            lists += 1
            if condition == "missing":
                return ["other-eval"]
            names = [own, "other-eval"]
            if condition == "listed-late" and lists == 2:
                names.append("new-tag")
            return names

        def resolve_manifest(self, image: str) -> str:
            nonlocal own_reads
            assert image.startswith("registry.fly.io/app:")
            if image.endswith(f":{own}"):
                own_reads += 1
                return different if condition == "retagged" and own_reads == 3 else digest
            assert image.endswith(":other-eval")
            return digest if condition == "shared" else different

        def delete_manifest(self, repository: str, target: str) -> bool:
            deletions.append((repository, target))
            return True

    def client(_fly: object) -> Registry:
        return Registry()

    monkeypatch.setattr(eval_cli, "_client", client)
    options = ["--keep", "e1-rep-1"] if condition == "kept" else []
    assert (
        eval_cli.main(["teardown", "--config", "c", "--eval-id", "e1", "--delete-image", *options])
        == 0
    )
    assert deletions == ([("registry.fly.io/app", digest)] if deleted else [])
    report = json.loads(capsys.readouterr().out)["image"]
    assert report["deleted"] is deleted
    if reason:
        assert reason in report["reason"]
    if condition == "kept":
        assert (lists, own_reads) == (0, 0)


def test_image_start_appends_the_suite_layer_and_status_reports_its_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import hashlib
    from types import SimpleNamespace

    from agent_factory import eval_cli

    bin_dir = tmp_path / "bin"
    write_guest_flyctl(bin_dir, tmp_path / "guest", tmp_path / "flyctl.log", tmp_path / "none")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    token = tmp_path / "token"
    token.write_text("secret-deploy-token\n", encoding="utf-8")
    fly = SimpleNamespace(app="app", token_file=token, region="ewr")

    def fly_settings(_config: Path) -> tuple[None, object]:
        return None, fly

    monkeypatch.setattr(eval_cli, "_fly", fly_settings)
    runner = tmp_path / "runner"
    runner_commit = _commit(
        runner,
        {
            "docker/dev/Dockerfile": "FROM node:22\nUSER pwuser\nWORKDIR /workspace\n",
            "scripts/sandbox-sync-home.sh": "#!/bin/sh\n",
        },
    )
    archon = tmp_path / "archon"
    archon_commit = _commit(archon, {"package.json": '{"engines": {"bun": "1.2.3"}}'})
    layer_file = tmp_path / "halo.Dockerfile"
    layer_file.write_text("USER root\nCOPY suite/tools.sh /opt/halo/tools.sh\n")
    layer_context = tmp_path / "image"
    (layer_context / "start").mkdir(parents=True)
    (layer_context / "tools.sh").write_text("#!/bin/sh\n")
    (layer_context / "tools.sh").chmod(0o755)
    (layer_context / "start/build.gradle").write_text("// halo")

    def image(work: Path, *extra: str) -> dict[str, object]:
        argv = [
            "image", "start", "--config", "c", "--work-dir", str(work), "--eval-id", "e1",
            "--archon-dir", str(archon), "--archon-commit", archon_commit,
            "--runner-dir", str(runner), "--runner-commit", runner_commit,
            "--codex-version", "0.169.0", "--claude-code-version", "2.1.311", *extra,
        ]  # fmt: skip
        assert eval_cli.main(argv) == 0
        for _ in range(100):
            if (work / "build.exit").is_file():
                break
            time.sleep(0.1)
        # The double pushes under its own tag; name the eval's image, as Fly does.
        (work / "build.log").write_text(
            "#14 pushing manifest for registry.fly.io/app:archon-eval-e1@sha256:" + "b" * 64 + "\n"
        )
        capsys.readouterr()
        assert eval_cli.main(["image", "status", "--config", "c", "--work-dir", str(work)]) == 0
        return cast(dict[str, object], json.loads(capsys.readouterr().out))

    layered = tmp_path / "layered"
    status = image(
        layered, "--extra-dockerfile", str(layer_file), "--extra-context", str(layer_context)
    )
    recipe = (layered / "context/Dockerfile.archon").read_text()
    archon_tail = recipe.index("ENV AGENT_RUNNER_SOURCE_COMMIT=")
    assert archon_tail < recipe.index("COPY suite/tools.sh /opt/halo/tools.sh")
    # The base image's user and folder hold after the suite layer.
    assert recipe.rstrip().endswith(
        "COPY suite/tools.sh /opt/halo/tools.sh\nUSER pwuser\nWORKDIR /workspace"
    )
    assert (layered / "context/suite/tools.sh").read_text() == "#!/bin/sh\n"
    assert (layered / "context/suite/start/build.gradle").read_text() == "// halo"
    assert status["state"] == "built" and status["digest"] == "sha256:" + "b" * 64
    assert status["layer_dockerfile_sha256"] == hashlib.sha256(layer_file.read_bytes()).hexdigest()
    assert status["layer_context_sha256"] == eval_cli.tree_sha256(layer_context)
    assert (
        json.loads((layered / "image.json").read_text())["layer_context_sha256"]
        == (status["layer_context_sha256"])
    )
    # A changed file in the context changes its hash.
    before = eval_cli.tree_sha256(layer_context)
    (layer_context / "tools.sh").chmod(0o644)
    assert eval_cli.tree_sha256(layer_context) != before

    # A context that holds the work folder, or lies inside it, is refused at once.
    for work, context in ((layer_context / "build", layer_context), (tmp_path, layer_context)):
        argv = [
            "image", "start", "--config", "c", "--work-dir", str(work), "--eval-id", "e1",
            "--archon-dir", str(archon), "--archon-commit", archon_commit,
            "--runner-dir", str(runner), "--runner-commit", runner_commit,
            "--codex-version", "0.169.0", "--claude-code-version", "2.1.311",
            "--extra-dockerfile", str(layer_file), "--extra-context", str(context),
        ]  # fmt: skip
        assert eval_cli.main(argv) == 1
        assert "must not hold each other" in capsys.readouterr().out
    assert not (layer_context / "build").exists()
    assert (layer_context / "tools.sh").is_file()

    plain = tmp_path / "plain"
    status = image(plain)
    assert not (plain / "context/suite").exists()
    assert "layer_dockerfile_sha256" not in status and "layer_context_sha256" not in status
    # Without a layer the recipe is the Archon recipe alone, as before.
    unlayered = recipe[: recipe.index("\nUSER root\nCOPY suite")]
    assert (plain / "context/Dockerfile.archon").read_text() == unlayered

    # Hold the build behind a file gate so it cannot finish before image start
    # returns. A bounded wait lets the test fail even if start stops detaching.
    release = tmp_path / "release"
    failed_build = bin_dir / "flyctl"
    failed_build.write_text(
        "#!/bin/sh\n"
        'for attempt in $(seq 1 100); do [ -f "$EVAL_BUILD_RELEASE" ] && break; sleep 0.1; done\n'
        "printf '%03000d\\n' 0\n"
        "echo 'pinned tool version check failed' >&2\n"
        "exit 17\n"
    )
    failed_build.chmod(0o755)
    monkeypatch.setenv("EVAL_BUILD_RELEASE", str(release))
    failing = tmp_path / "failing"
    status_args = ["image", "status", "--config", "c", "--work-dir", str(failing)]
    start_args = [
        "image", "start", "--config", "c", "--work-dir", str(failing), "--eval-id", "e1",
        "--archon-dir", str(archon), "--archon-commit", archon_commit,
        "--runner-dir", str(runner), "--runner-commit", runner_commit,
        "--codex-version", "0.169.0", "--claude-code-version", "2.1.311",
    ]  # fmt: skip
    assert eval_cli.main(start_args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "building"
    assert not (failing / "build.exit").exists()
    assert eval_cli.main(status_args) == 0
    assert json.loads(capsys.readouterr().out) == {"state": "building"}
    release.touch()
    for _ in range(100):
        if (failing / "build.exit").is_file():
            break
        time.sleep(0.1)
    assert (failing / "build.exit").read_text().strip() == "17"
    assert eval_cli.main(status_args) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["state"] == "failed"
    assert "pinned tool version check failed" in failure["log_tail"]
    assert len(failure["log_tail"].encode()) == 2000
    assert not (failing / "image.json").exists()


@pytest.mark.parametrize("case", ["link-outside", "link-inside", "link-to-harness", "unused"])
def test_listed_path_that_would_not_arrive_where_it_names_is_refused(
    machines: Machines, case: str
) -> None:
    fleet = machines
    suite = fleet.suite
    elsewhere = fleet.tmp / "elsewhere"
    elsewhere.mkdir()
    if case == "link-outside":
        (suite / "linked").symlink_to(elsewhere)
        entry = suite / "linked"
    elif case == "link-inside":
        (suite / "linked").symlink_to(suite / "fixture")
        entry = suite / "linked"
    elif case == "link-to-harness":
        # The link's target is also sent as a harness folder, but not to /eval-input.
        (suite / "linked").symlink_to(fleet.panel)
        entry = suite / "linked"
    else:
        # Outside the input folder, and holding none of the harness folders sent.
        entry = elsewhere
    run = fleet.run_dir(
        "run",
        fleet.manifest("e1-rep-1", delivery=[str(suite / "AGENTS-runbook.md"), str(entry)]),
        {
            "script": "true",
            "input_dir": str(suite),
            "deliveries": [[str(fleet.panel), "/lib/panel-judging"]]
            if case == "link-to-harness"
            else [],
            "env_files": [str(fleet.env_file)],
        },
    )
    code, output = fleet.cli("start", run, "--image-file", str(fleet.image))
    assert code == 1
    assert str(entry) in str(output["error"])
    assert fleet.creates() == []


def test_compatibility_launch_refuses_a_manifest_only_start_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "artifact"
    (artifact / ".factory").mkdir(parents=True)
    (tmp_path / "input").mkdir()
    env_file = tmp_path / "env"
    env_file.write_text("GITHUB_TOKEN=github-secret\n", encoding="utf-8")
    argv = [
        "--artifact-dir", str(artifact), "--input-dir", str(tmp_path / "input"), "--dev-audit",
        "--docker-run-arg", "--security-opt", "--docker-run-arg", "seccomp=unconfined",
        "--env-file", str(env_file), "--", "echo ok",
    ]  # fmt: skip
    for extra in (
        {"job": "score"},
        {"machine": {"cpus": 4}},
        {"delivery": [str(tmp_path / "input")]},
        {"private": [str(tmp_path / "input")]},
    ):
        manifest = artifact / ".factory/manifest.json"
        manifest.write_text(json.dumps({"artifact_dir": str(artifact), **extra}))
        assert main(argv, manifest_path=manifest) == 70
        message = capsys.readouterr().err
        assert "needs agent-factory-fly-eval start" in message and next(iter(extra)) in message
    assert not (artifact / ".factory/machine.json").exists()
