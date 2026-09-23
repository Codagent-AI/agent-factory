"""E2E-001: an eval request to a settled result through Fly, with a restart and a lost Machine.

The factory CLI runs against the stub GitHub of ``test_factory_cycle.py``, the fake
Machines API, and a ``flyctl`` double that gives every fake Machine its own local
filesystem in which the real guest init script runs under bash. The launcher on
``PATH`` is the real one with the Runner bootstrap of the job wrapper replaced,
since no Machine exists to clone and build Runner in.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import pytest

from agent_factory.config import LocalConfig
from agent_factory.fly.guest import guest_init_script
from agent_factory.store import ClaimStore, Run
from tests.e2e.test_factory_cycle import (
    _field_value,  # pyright: ignore[reportPrivateUsage]
    _git,  # pyright: ignore[reportPrivateUsage]
    _setup,  # pyright: ignore[reportPrivateUsage]
)
from tests.fixtures.fly.api import FakeMachinesApi
from tests.fixtures.fly.flyctl import write_guest_flyctl

REVIEWABLE: dict[str, object] = {
    "evaluation_status": "pending-human-review",
    "product_verdict": "unavailable",
    "automated_subtotal": 60,
}

# The stub harness: composes the suite script and hands the exact launcher grammar
# to SANDBOX_RUNNER, as the pinned run.sh does. The suite script checkpoints, then
# waits for the test to hand it its result.
_RUN_SH = f"""#!{sys.executable}
import os, subprocess, sys
args = sys.argv[1:]
def option(name):
    return args[args.index(name) + 1]
suite_dir = os.path.dirname(os.path.abspath(__file__))
script = (
    "echo suite started; "
    "echo '{{\\"schema_version\\": 1}}' > /artifacts/run-state.json; "
    "while [ ! -e /artifacts/finish ]; do sleep 0.1; done; "
    "cp /artifacts/finish /artifacts/result.json; echo suite finished"
)
runner = option("--agent-runner-dir")
command = [os.environ["SANDBOX_RUNNER"]]
if "--dry-run" in args:
    command.append("--dry-run")
command += [
    "--artifact-dir", option("--artifact-dir"), "--input-dir", suite_dir, "--dev-audit",
    "--docker-run-arg", "--security-opt", "--docker-run-arg", "seccomp=unconfined",
    "--docker-run-arg", "--mount",
    "--docker-run-arg", "type=bind,source=" + runner + ",target=/agent-runner-source,readonly",
    "--env-file", option("--env-file"), "--mount-codex-auth", "--", script,
]
sys.exit(subprocess.call(command))
"""

_LAUNCHER = """#!{python}
import os, sys
os.environ["AGENT_FACTORY_FLY_API_URL"] = {url!r}
from agent_factory.fly import guest, launcher
# A local guest cannot clone and build Runner; the suite script runs as the job.
guest.job_script = lambda manifest, script: guest.stand_in_script(script)
sys.exit(launcher.main())
"""

_BEFORE_CLI = """
from agent_factory.fly import api as fly_api, backend as fly_backend
class LocalRegistryClient(fly_api.FlyMachinesClient):
    def __init__(self, app, token_file, **kw):
        super().__init__(app, token_file, registry_base_url={url!r}, **kw)
fly_backend.FlyMachinesClient = LocalRegistryClient
"""


class Guests:
    """One real guest init per fake Machine, each rooted in its own directory."""

    def __init__(self, api: FakeMachinesApi, roots: Path) -> None:
        self.api = api
        self.roots = roots
        self.processes: dict[str, subprocess.Popen[bytes]] = {}

    def root(self, machine_id: str) -> Path:
        return self.roots / machine_id

    def boot_new(self) -> None:
        for machine_id in list(self.api.machines):
            if machine_id in self.processes:
                continue
            root = self.root(machine_id)
            root.mkdir(parents=True, exist_ok=True)
            self.processes[machine_id] = subprocess.Popen(
                ["bash", "-c", guest_init_script()],
                env={
                    **os.environ,
                    "FACTORY_ROOT": str(root),
                    "FACTORY_WATCHDOG_SECONDS": "1",
                    "FACTORY_DEADLINE_EPOCH": str(int(time.time()) + 3600),
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    def finish(self, machine_id: str, result: dict[str, object]) -> None:
        artifacts = self.root(machine_id) / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "finish").write_text(json.dumps(result), encoding="utf-8")

    def lose(self, machine_id: str) -> None:
        """The Machine vanishes: the API forgets it and ssh no longer reaches it."""
        (self.root(machine_id) / ".unreachable").write_text("", encoding="utf-8")
        self.api.machines.pop(machine_id, None)
        self._kill(machine_id)

    def _kill(self, machine_id: str) -> None:
        process = self.processes.get(machine_id)
        if process is None:
            return
        _kill_group(process.pid)
        process.wait()

    def close(self) -> None:
        for machine_id in list(self.processes):
            self._kill(machine_id)


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    done = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    state = done.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait(condition: Callable[[], bool], factory: Factory, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        factory.guests.boot_new()
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time\n" + factory.diagnosis())


class Factory:
    def __init__(self, tmp_path: Path, api: FakeMachinesApi) -> None:
        self.api = api
        self.config, self.board, self.env, self.shared = _setup(tmp_path)
        bin_dir = tmp_path / "bin"
        self.roots = tmp_path / "machines"
        self.guests = Guests(api, self.roots)
        # The harness stub that composes the suite script for the launcher seam.
        evals = tmp_path / "evals"
        run_sh = evals / "evals/agent-runner/and-scene/run.sh"
        run_sh.write_text(_RUN_SH, encoding="utf-8")
        _git(evals, "add", ".")
        _git(
            evals, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "fly"
        )
        _git(evals, "push", "-q", "origin", "main")
        # Three repetitions, all on Codex.
        data = json.loads(self.board.read_text())
        body = str(data["items"][0]["content"]["body"]).replace("repetitions=1", "repetitions=3")
        data["items"][0]["content"]["body"] = body
        self.board.write_text(json.dumps(data))
        # Evals on Fly, fixes on the host; generous supervision limits for the journey.
        token = tmp_path / "fly-token"
        token.write_text("deploy-token\n", encoding="utf-8")
        token.chmod(0o600)
        text = self.config.read_text(encoding="utf-8")
        text = text.replace("inactivity_seconds = 20", "inactivity_seconds = 120")
        text = text.replace("execution_seconds = 30", "execution_seconds = 300")
        text = text.replace("total_seconds = 60", "total_seconds = 600")
        text += (
            '[fix]\nexecution = "host"\n[eval]\nexecution = "fly"\n'
            f'[fly]\napp = "app"\nimage = "registry.fly.io/app:base"\ntoken_file = "{token}"\n'
            "heartbeat_seconds = 1\n"
        )
        self.config.write_text(text, encoding="utf-8")
        suite_environment = LocalConfig.from_file(self.config).credentials.suite_environment
        suite_environment.write_text(
            suite_environment.read_text(encoding="utf-8")
            + "\nCLAUDE_CODE_OAUTH_TOKEN=fixture-token\n",
            encoding="utf-8",
        )
        # Transport double, launcher, and the operator's Codex login for delivery.
        write_guest_flyctl(
            bin_dir, self.roots, tmp_path / "flyctl.log", tmp_path / "none", per_machine=True
        )
        # HOME is a fixture home, so the real Claude CLI would report no login.
        claude = bin_dir / "claude"
        claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        claude.chmod(0o755)
        launcher = bin_dir / "agent-factory-fly-launcher"
        launcher.write_text(_LAUNCHER.format(python=sys.executable, url=api.base_url))
        launcher.chmod(0o755)
        home = tmp_path / "home"
        (home / ".codex").mkdir(parents=True)
        (home / ".codex/auth.json").write_text("codex-secret", encoding="utf-8")
        self.env["HOME"] = str(home)
        self.env["AGENT_FACTORY_FLY_API_URL"] = api.base_url
        self.store = ClaimStore(tmp_path / "factory/state.sqlite3")

    def cli(self, command: str) -> str:
        entrypoint = (
            "import io, runpy, urllib.request\n"
            "from agent_factory.config import ScheduleConfig\n"
            "allows_admission = ScheduleConfig.allows_admission\n"
            "ScheduleConfig.allows_admission = "
            "lambda self, now: allows_admission(self, now.replace(hour=12))\n"
            "real_urlopen = urllib.request.urlopen\n"
            "def token_response(request, *, timeout=None):\n"
            "    if not request.full_url.startswith('https://api.github.com/'):\n"
            "        return real_urlopen(request, timeout=timeout)\n"
            '    return io.BytesIO(b\'{"token":"test","expires_at":"2099-01-01T00:00:00Z"}\')\n'
            "urllib.request.urlopen = token_response\n"
            + _BEFORE_CLI.format(url=self.api.base_url)
            + "runpy.run_module('agent_factory.cli', run_name='__main__')\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", entrypoint, "--config", str(self.config), command],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout

    def run(self, run_id: str) -> Run:
        return cast(Run, self.store.get_run(run_id))

    def active(self) -> Run:
        runs = self.store.nonterminal_runs()
        assert len(runs) == 1, self.diagnosis()
        return runs[0]

    def diagnosis(self) -> str:
        """Why nothing is running: readiness, holds, events, and delivered comments."""
        lines = [f"readiness: {self.store.get_setting('runtime', 'readiness:eval')}"]
        for claim in self.store.all_claims():
            lines.append(f"claim {claim.id}: {claim.lifecycle} {claim.outcome}")
            lines.append(f"  readiness hold: {self.store.get_hold(claim.id, 'readiness')}")
            lines.append(f"  reporting: {claim.reporting}")
        lines.extend(f"comment: {body}" for body in self.comments())
        runs = [
            run for claim in self.store.all_claims() for run in self.store.runs_for_claim(claim.id)
        ]
        for run in runs:
            lines.append(f"run {run.unit_key} {run.status} {run.reason}: {run.result}")
            evidence = Path(run.evidence_path)
            for name in ("factory-suite.log", ".factory/launcher.log"):
                if (evidence / name).is_file():
                    lines.append(f"--- {run.unit_key} {name} ---")
                    lines.append((evidence / name).read_text(encoding="utf-8", errors="replace"))
            watcher_log = self.config.parent / "factory/logs/runs" / run.id / "supervisor.log"
            if watcher_log.is_file():
                lines.append(f"--- {run.unit_key} supervisor.log ---")
                lines.append(watcher_log.read_text(encoding="utf-8", errors="replace")[-3000:])
        return "\n".join(lines)

    def creates(self) -> list[dict[str, object]]:
        return [
            r
            for r in self.api.requests
            if r["method"] == "POST" and str(r["path"]).endswith("/machines")
        ]

    def deletes(self, machine_id: str) -> int:
        return len(
            [
                r
                for r in self.api.requests
                if r["method"] == "DELETE" and f"/machines/{machine_id}" in str(r["path"])
            ]
        )

    def comments(self) -> list[str]:
        return [str(c["body"]) for c in json.loads(self.board.read_text())["comments"]]

    def close(self) -> None:
        for run in self.store.nonterminal_runs():
            for identity in (run.process, run.supervisor):
                pid = identity.get("pid")
                if isinstance(pid, int):
                    _kill_group(pid)
        self.store.close()
        self.guests.close()


@pytest.fixture
def factory(tmp_path: Path) -> Iterator[Factory]:
    with FakeMachinesApi(manifest_digest="sha256:base") as api:
        site = Factory(tmp_path, api)
        try:
            yield site
        finally:
            site.close()


def _pid(identity: dict[str, object]) -> int:
    value = identity.get("pid")
    assert isinstance(value, int), identity
    return value


def _machine_id(run: Run) -> str:
    machine = run.progress.get("machine")
    assert isinstance(machine, dict), run.progress
    value = cast(dict[str, object], machine).get("id")
    assert isinstance(value, str)
    return value


def test_e2e_001_fly_eval_survives_a_restart_and_settles_a_lost_machine(factory: Factory) -> None:
    store, guests, shared = factory.store, factory.guests, factory.shared

    # -- request and first repetition ---------------------------------------
    factory.cli("tick")
    first = factory.active()
    assert first.unit_key == "rep-1" and first.reason == "initial"
    _wait(lambda: "machine-1" in factory.api.machines, factory)
    _wait(
        lambda: factory.run(first.id).progress.get("checkpoint_seen") is True,
        factory,
    )
    heartbeat = json.loads((Path(first.evidence_path) / ".factory/heartbeat.json").read_text())
    assert heartbeat["checkpoint_seen"] is True
    running = factory.run(first.id)
    assert _machine_id(running) == "machine-1"
    machine_progress = cast(dict[str, object], running.progress["machine"])
    assert machine_progress["state"] == "alive"
    assert isinstance(machine_progress["deadline_epoch"], int)

    # -- the Mac restarts: controller, watcher, and launcher all die ----------
    watcher, launcher = _pid(running.supervisor), _pid(running.process)
    _kill_group(watcher)
    _kill_group(launcher)
    _wait(lambda: not _alive(watcher) and not _alive(launcher), factory)
    factory.cli("tick")
    _wait(
        lambda: (
            _identity_pid(factory.run(first.id).supervisor) not in {None, watcher}
            and _identity_pid(factory.run(first.id).process) not in {None, launcher}
        ),
        factory,
    )
    adopted = factory.run(first.id)
    assert adopted.status == "running" and adopted.reason == "initial"
    assert _machine_id(adopted) == "machine-1"
    assert list(factory.api.machines) == ["machine-1"]
    assert len(factory.creates()) == 1, "restart must adopt, not create"
    assert store.recovery_attempts(first.claim_id, "rep-1") == 0
    assert len(store.runs_for_claim(first.claim_id)) == 1

    # -- repetition one settles and its Machine is destroyed -----------------
    guests.finish("machine-1", REVIEWABLE)
    _wait(lambda: factory.run(first.id).status == "completed", factory)
    artifact_one = Path(first.evidence_path)
    assert json.loads((artifact_one / "result.json").read_text()) == REVIEWABLE
    launcher_log = (artifact_one / ".factory/launcher.log").read_text()
    assert launcher_log.count("collected job 1") == 1
    assert (artifact_one / "guest-exit-code").read_text().strip() == "0"
    factory.cli("tick")
    assert "machine-1" not in factory.api.machines
    assert factory.deletes("machine-1") == 1
    assert store.get_setting("runtime", "fly:machine:" + first.id) is None

    # -- repetition two loses its Machine mid-run ----------------------------
    second = factory.active()
    assert second.unit_key == "rep-2" and second.reason == "initial"
    _wait(lambda: "machine-2" in factory.api.machines, factory)
    _wait(lambda: factory.run(second.id).progress.get("checkpoint_seen") is True, factory)
    guests.lose("machine-2")
    _wait(lambda: factory.run(second.id).status not in {"running", "observing"}, factory)
    factory.cli("tick")
    lost = factory.run(second.id)
    assert lost.status == "failed"
    assert lost.result["reason"] == "machine lost"
    assert lost.result["failure"] == {"owner": "factory", "code": "machine-lost"}
    assert store.recovery_attempts(second.claim_id, "rep-2") == 0
    assert [run.unit_key for run in store.runs_for_claim(first.claim_id)] == [
        "rep-1",
        "rep-2",
        "rep-3",
    ]
    assert not (Path(second.evidence_path) / "result.json").exists()

    # -- repetition three runs in a new Machine ------------------------------
    third = factory.active()
    assert third.unit_key == "rep-3" and third.reason == "initial"
    _wait(lambda: "machine-3" in factory.api.machines, factory)
    _wait(lambda: factory.run(third.id).progress.get("checkpoint_seen") is True, factory)
    assert _machine_id(factory.run(third.id)) == "machine-3"
    assert len(factory.creates()) == 3
    guests.finish("machine-3", REVIEWABLE)
    _wait(lambda: factory.run(third.id).status == "completed", factory)
    factory.cli("tick")
    factory.cli("tick")

    # -- the card reaches Review with the loss explained ----------------------
    claim = store.get_claim(first.claim_id)
    assert claim is not None and claim.lifecycle == "settled"
    assert claim.outcome["verdict"] == "pending-human-review"
    assert _field_value(factory.board, shared.project.status.id) == shared.project.status.option(
        "review"
    )
    assert _field_value(factory.board, shared.project.verdict.id) == (
        shared.project.verdict.option("pending-human-review")
    )
    comments = factory.comments()
    results = [c for c in comments if "aggregate verdict is pending-human-review" in c]
    assert len(results) == 1
    assert "- rep-2 was lost to factory infrastructure: machine lost." in results[0]
    review_commands = [c for c in comments if "human-review.sh" in c]
    assert len(review_commands) == 2
    assert any(f"{claim.id}:rep-1:review-command" in c for c in review_commands)
    assert any(f"{claim.id}:rep-3:review-command" in c for c in review_commands)
    assert not any("rep-2:review-command" in c for c in comments)
    assert any("--run-dir " + first.evidence_path in c for c in review_commands)
    assert not any(second.evidence_path in c for c in review_commands)

    # -- nothing is left running on Fly -------------------------------------
    assert factory.api.machines == {}
    assert store.nonterminal_runs() == []
    status = factory.cli("status")
    assert "Machine:" not in status
    assert "blocking condition" not in status


def _identity_pid(identity: dict[str, object]) -> int | None:
    value = identity.get("pid")
    return value if isinstance(value, int) else None
