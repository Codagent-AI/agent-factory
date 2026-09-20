"""Readiness implementation for the Fly Machine backend."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from agent_factory.backends import Disposal, Probe
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.fly.api import FlyApiError, FlyMachinesClient
from agent_factory.fly.transport import FlyTransport, FlyTransportError
from agent_factory.operations import Diagnostic


class FlyMachineBackend:
    name = "fly-machine"

    def __init__(
        self,
        *,
        client_factory: Callable[[str, Path], object] = FlyMachinesClient,
        transport_factory: Callable[[str, str], FlyTransport] = FlyTransport,
    ) -> None:
        self._client_factory = client_factory
        self._transport_factory = transport_factory

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        del shared
        if local.fly is None:
            return [
                Diagnostic(
                    "Fly configuration",
                    False,
                    "Fly settings are missing",
                    "Configure [fly] settings.",
                    "eval-fly",
                )
            ]
        fly = local.fly
        token_check = _token_diagnostic(fly.token_file)
        result = [token_check]
        if token_check.available:
            client = FlyMachinesClient(fly.app, fly.token_file)
            for name, call, action in (
                (
                    "Fly app API",
                    client.get_app,
                    f"Verify deploy-token access to Fly app {fly.app}.",
                ),
                (
                    "Fly image manifest",
                    lambda: client.resolve_manifest(fly.image),
                    f"Push or configure a resolvable Fly image: {fly.image}.",
                ),
            ):
                try:
                    call()
                    result.append(
                        Diagnostic(name, True, "check succeeded", "No action required.", "eval-fly")
                    )
                except FlyApiError as error:
                    result.append(Diagnostic(name, False, str(error), action, "eval-fly"))
        result.append(_flyctl_diagnostic(fly.app))
        return result

    def identity_from_plan(self, plan: object, run: object) -> Mapping[str, object] | None:
        artifact = _artifact_path(plan)
        if artifact is None:
            return None
        try:
            record = _json_mapping(artifact / ".factory" / "machine.json")
            manifest = _json_mapping(artifact / ".factory" / "manifest.json")
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        machine_id = record.get("id")
        app = record.get("app")
        fly = manifest.get("fly")
        if (
            not isinstance(machine_id, str)
            or not isinstance(app, str)
            or not isinstance(fly, Mapping)
        ):
            return None
        fly_values = cast(Mapping[str, object], fly)
        token_file = fly_values.get("token_file")
        if not isinstance(token_file, str):
            return None
        expected = {
            "factory-owner": "agent-factory",
            "run_id": manifest.get("run_id"),
            "claim_id": manifest.get("claim_id"),
            "unit_key": manifest.get("unit_key"),
            "nonce": record.get("nonce", manifest.get("nonce")),
        }
        if not all(isinstance(value, str) and value for value in expected.values()):
            return None
        return {
            **dict(record),
            "app": app,
            "id": machine_id,
            "token_file": token_file,
            "expected_metadata": expected,
        }

    def probe(self, identity: Mapping[str, object]) -> Probe:
        try:
            machine = self._client(identity).get_machine(_machine_id(identity))
        except FlyApiError as error:
            return Probe("gone" if error.status == 404 else "unknown", str(error))
        except (OSError, ValueError) as error:
            return Probe("unknown", str(error))
        expected = _expected_metadata(identity)
        observed = _metadata(machine)
        mismatches = {
            key: {"expected": value, "observed": observed.get(key)}
            for key, value in expected.items()
            if observed.get(key) != value
        }
        if mismatches:
            return Probe("mismatch", json.dumps(mismatches, sort_keys=True))
        state = machine.get("state")
        if state in {"started", "starting", "restarting"}:
            return Probe("alive")
        if state in {"stopped", "suspended"}:
            return Probe("stopped")
        return Probe("unknown", f"unrecognized Machine state: {state!r}")

    def terminate(self, identity: Mapping[str, object]) -> bool:
        if self.probe(identity).state not in {"alive", "stopped"}:
            return False
        try:
            # Jobs run in their own process group; TERM lets the guest write DONE
            # and collect artifacts before the launcher exits.
            self._transport_factory(_app(identity), _machine_id(identity)).command(
                'pkill -TERM -g "$(cat /artifacts/.factory/job/current-pgid 2>/dev/null)" || true'
            )
        except (FlyTransportError, OSError):
            return False
        return True

    def dispose(self, identity: Mapping[str, object], decision: Disposal) -> None:
        if decision != "destroy":
            return
        if self.probe(identity).state not in {"alive", "stopped"}:
            return
        try:
            self._client(identity).destroy(_machine_id(identity))
        except FlyApiError:
            return

    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]:
        artifact = _artifact_path(plan)
        if artifact is None:
            raise ValueError("Fly plan has no artifact path")
        launcher = _allowed_environment(plan).get("SANDBOX_RUNNER", "agent-factory-fly-launcher")
        return (launcher, "attach", "--run-dir", str(artifact))

    def reconcile(self, store: object) -> list[str]:
        raise NotImplementedError

    def provenance(self, identity: Mapping[str, object]) -> Mapping[str, object]:
        try:
            machine = self._client(identity).get_machine(_machine_id(identity))
        except FlyApiError:
            return {"machine_id": _machine_id(identity), "observation": "unavailable"}
        config = machine.get("config")
        values: Mapping[str, object] = (
            cast(Mapping[str, object], config) if isinstance(config, Mapping) else {}
        )
        guest = values.get("guest")
        size: Mapping[str, object] = (
            cast(Mapping[str, object], guest) if isinstance(guest, Mapping) else {}
        )
        return {
            "machine_id": _machine_id(identity),
            "image_digest": values.get("image_ref", values.get("image")),
            "cpu_kind": size.get("cpu_kind"),
            "cpus": size.get("cpus"),
            "memory_mb": size.get("memory_mb"),
            "region": machine.get("region"),
        }

    def _client(self, identity: Mapping[str, object]) -> FlyMachinesClient:
        return cast(
            FlyMachinesClient, self._client_factory(_app(identity), Path(_token_file(identity)))
        )


def _token_diagnostic(path: Path) -> Diagnostic:
    try:
        metadata = path.stat()
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return Diagnostic(
            "Fly deploy token",
            False,
            f"token file is unavailable: {error}",
            "Create an owner-readable, single-line Fly deploy token file.",
            "eval-fly",
        )
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or len(content.splitlines()) != 1
        or not content.strip()
    ):
        return Diagnostic(
            "Fly deploy token",
            False,
            "token file must be private and contain exactly one token",
            "chmod 600 the token file and leave only the deploy token.",
            "eval-fly",
        )
    return Diagnostic(
        "Fly deploy token",
        True,
        "private single-line token is available",
        "No action required.",
        "eval-fly",
    )


def _flyctl_diagnostic(app: str) -> Diagnostic:
    executable = os.environ.get("PATH", "")
    if not any(
        os.access(os.path.join(part, "flyctl"), os.X_OK) for part in executable.split(os.pathsep)
    ):
        return Diagnostic(
            "flyctl transport",
            False,
            "flyctl is not executable on the service PATH",
            "Install flyctl and add it to the launch service PATH.",
            "eval-fly",
        )
    try:
        completed = subprocess.run(
            ("flyctl", "ssh", "issue", "--app", app), capture_output=True, timeout=20, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic(
            "flyctl transport",
            False,
            f"ssh check could not run: {error}",
            "Restore flyctl SSH access to the Fly app.",
            "eval-fly",
        )
    return Diagnostic(
        "flyctl transport",
        completed.returncode == 0,
        "ssh transport check succeeded"
        if completed.returncode == 0
        else "flyctl ssh cannot reach the app",
        "Restore flyctl SSH access to the Fly app.",
        "eval-fly",
    )


def _artifact_path(plan: object) -> Path | None:
    hints = getattr(plan, "ownership_hints", None)
    if not isinstance(hints, Mapping):
        return None
    hint_values = cast(Mapping[str, object], hints)
    value = hint_values.get("artifact_path")
    return Path(value) if isinstance(value, str) else None


def _allowed_environment(plan: object) -> Mapping[str, str]:
    value = getattr(plan, "allowed_environment", {})
    return cast(Mapping[str, str], value) if isinstance(value, Mapping) else {}


def _json_mapping(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} is not an object")
    return cast(Mapping[str, object], value)


def _machine_id(identity: Mapping[str, object]) -> str:
    value = identity.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("Machine identity is unavailable")
    return value


def _app(identity: Mapping[str, object]) -> str:
    value = identity.get("app")
    if not isinstance(value, str) or not value:
        raise ValueError("Fly app is unavailable")
    return value


def _token_file(identity: Mapping[str, object]) -> str:
    value = identity.get("token_file")
    if not isinstance(value, str) or not value:
        raise ValueError("Fly token file is unavailable")
    return value


def _expected_metadata(identity: Mapping[str, object]) -> Mapping[str, str]:
    value = identity.get("expected_metadata")
    if not isinstance(value, Mapping):
        raise ValueError("Machine ownership metadata is unavailable")
    values = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in values.items()):
        raise ValueError("Machine ownership metadata is unavailable")
    return {cast(str, key): cast(str, item) for key, item in values.items()}


def _metadata(machine: Mapping[str, object]) -> Mapping[str, object]:
    config = machine.get("config")
    if not isinstance(config, Mapping):
        return {}
    config_values = cast(Mapping[str, object], config)
    metadata = config_values.get("metadata")
    return cast(Mapping[str, object], metadata) if isinstance(metadata, Mapping) else {}
