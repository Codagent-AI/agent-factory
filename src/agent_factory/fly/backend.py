"""Readiness implementation for the Fly Machine backend."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.backends import Disposal, Probe
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.fly.api import FlyApiError, FlyMachinesClient, is_gone, read_token
from agent_factory.fly.launcher import LAUNCHER_NAME
from agent_factory.fly.launcher import executable as fly_launcher
from agent_factory.fly.transport import (
    FlyTransport,
    FlyTransportError,
    image_digest,
    image_repository,
    resolve_claude_login,
)
from agent_factory.operations import Diagnostic
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Run


def fly_repository_diagnostic(image: str, app: str) -> Diagnostic:
    repository = image_repository(image)
    expected = f"registry.fly.io/{app}"
    if repository == expected:
        return Diagnostic("Fly image repository", True, repository, "", "eval-fly")
    return Diagnostic(
        "Fly image repository",
        False,
        f"{image} targets {repository}",
        f"Set [fly] image to {expected}:base.",
        "eval-fly",
    )


class FlyMachineBackend:
    name = "fly-machine"

    def __init__(
        self,
        *,
        client_factory: Callable[[str, Path], object] = FlyMachinesClient,
        transport_factory: Callable[[str, str], FlyTransport] = FlyTransport,
        app: str | None = None,
        token_file: Path | None = None,
        local: LocalConfig | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._transport_factory = transport_factory
        self._app = app
        self._token_file = token_file
        self._local = local

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
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
        result = [_launcher_diagnostic()]
        result.append(fly_repository_diagnostic(fly.image, fly.app))
        roles: Mapping[str, object] = cast(
            Mapping[str, object], getattr(getattr(shared, "eval", None), "defaults", {})
        )
        if any(
            str(roles.get(role, "")).startswith("claude:")
            for role in ("lead", "implementor", "tester")
        ):
            try:
                login = resolve_claude_login([local.credentials.suite_environment])
                result.append(
                    Diagnostic(
                        "Fly Claude login",
                        login.kind != "unavailable",
                        f"Claude login source: {login.kind}"
                        if login.kind != "unavailable"
                        else login.reason,
                        "Set CLAUDE_CODE_OAUTH_TOKEN in the suite environment "
                        "or repair the service user's Claude login."
                        if login.kind == "unavailable"
                        else "",
                        "eval-fly",
                    )
                )
            except OSError as error:
                result.append(
                    Diagnostic(
                        "Fly Claude login",
                        False,
                        type(error).__name__,
                        "Repair the suite environment file.",
                        "eval-fly",
                    )
                )
        token_check = _token_diagnostic(fly.token_file)
        result.append(token_check)
        if token_check.available:
            client = FlyMachinesClient(fly.app, fly.token_file)
            for name, call, action in (
                (
                    "Fly app API",
                    client.get_app,
                    f"Verify deploy-token access to Fly app {fly.app}.",
                ),
            ):
                try:
                    call()
                    result.append(
                        Diagnostic(name, True, "check succeeded", "No action required.", "eval-fly")
                    )
                except FlyApiError as error:
                    result.append(Diagnostic(name, False, str(error), action, "eval-fly"))
        result.append(_flyctl_diagnostic(fly.app, fly.token_file))
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
        if record.get("run_id") != manifest.get("run_id"):
            # The record still names an earlier attempt in this Machine; the
            # launcher has not yet claimed it for the current one.
            return None
        return {
            **dict(record),
            "app": app,
            "id": machine_id,
            "run_id": expected["run_id"],
            "claim_id": expected["claim_id"],
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
        if is_gone(machine):
            return Probe("gone", f"Machine is {state}")
        if state in {"started", "starting", "restarting", "created", "replacing"}:
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
            # The guest records the job's session leader here; the job runs under
            # setsid, so its pid is also its process group.
            self._transport(identity).command(
                'pgid="$(cat /artifacts/.factory/active-job-pgid 2>/dev/null)"; '
                'case "$pgid" in (*[!0-9]*|"") exit 0;; esac; '
                'kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pgid" 2>/dev/null || true'
            )
        except (FlyTransportError, OSError):
            return False
        return True

    def dispose(
        self, identity: Mapping[str, object], decision: Disposal, store: object | None = None
    ) -> None:
        """Apply a post-classification decision only after ownership is re-verified."""
        probe = self.probe(identity)
        if probe.state == "mismatch":
            _set_setting(
                store,
                "fly:mismatch",
                {
                    "machine_id": _machine_id(identity),
                    "run_id": identity.get("run_id"),
                    "expected": identity.get("expected_metadata"),
                    "observed": probe.detail,
                    "remedy": "destroy the Machine by hand or wait for its deadline",
                },
            )
            return
        if probe.state == "unknown":
            _cleanup_failure(store, _machine_id(identity), probe.detail)
            return
        if probe.state == "gone":
            _clear_machine_record(store, identity)
            _clear_cleanup_failure(store, _machine_id(identity))
            return
        try:
            observed = self._client(identity).get_machine(_machine_id(identity))
            identity = {**identity, "deadline_epoch": _metadata(observed).get("deadline_epoch")}
        except FlyApiError as error:
            _cleanup_failure(store, _machine_id(identity), str(error))
            return
        if decision == "destroy":
            if not self._destroy_verified(self._client(identity), _machine_id(identity)):
                # Keep the record so reconciliation knows the Machine and retries the destroy.
                _set_machine_record(store, identity, decision, probe.state)
                _cleanup_failure(store, _machine_id(identity), "destroy was not verified")
                return
            _clear_machine_record(store, identity)
            _clear_cleanup_failure(store, _machine_id(identity))
            return
        if decision == "stop" and probe.state == "alive":
            deadline = _quota_machine_deadline(
                _claim_hold(store, _claim_id(identity), "quota"), self._local
            )
            if deadline is None and self._local is None:
                # Unit callers without controller configuration can still exercise
                # ownership behavior; real controller disposal always supplies local.
                deadline = _deadline_epoch(identity.get("deadline_epoch"))
            if deadline is None:
                _cleanup_failure(store, _machine_id(identity), "quota hold has no usable deadline")
                return
            try:
                self._client(identity).set_metadata(
                    _machine_id(identity), "deadline_epoch", str(deadline)
                )
                verified = self._client(identity).get_machine(_machine_id(identity))
                if _metadata(verified).get("deadline_epoch") != str(deadline):
                    raise FlyApiError("metadata", detail="deadline update was not visible")
                if self._local is not None:
                    self._transport(identity).command(
                        f"printf '%s\\n' {deadline} > /var/lib/factory/deadline"
                    )
                self._client(identity).stop(_machine_id(identity))
            except (FlyApiError, FlyTransportError, OSError) as error:
                _cleanup_failure(store, _machine_id(identity), str(error))
                return
            identity = {**identity, "deadline_epoch": deadline}
            probe = Probe("stopped")
            _clear_cleanup_failure(store, _machine_id(identity))
        _set_machine_record(store, identity, decision, probe.state)

    def _transport(self, identity: Mapping[str, object]) -> FlyTransport:
        transport = self._transport_factory(_app(identity), _machine_id(identity))
        # flyctl authenticates with the same deploy token as the REST client.
        with contextlib.suppress(AttributeError, ValueError):
            transport.token_file = Path(_token_file(identity))
        return transport

    @staticmethod
    def _destroy_verified(client: FlyMachinesClient, machine_id: str) -> bool:
        try:
            client.destroy(machine_id)
            observed = client.get_machine(machine_id)
        except FlyApiError as error:
            return error.status == 404
        return is_gone(observed)

    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]:
        artifact = _artifact_path(plan)
        if artifact is None:
            raise ValueError("Fly plan has no artifact path")
        # Resolve as launch does, so reattachment works where only the interpreter's
        # directory holds the launcher; an explicit SANDBOX_RUNNER still wins.
        launcher = (
            _allowed_environment(plan).get("SANDBOX_RUNNER") or fly_launcher() or LAUNCHER_NAME
        )
        return (launcher, "attach", "--run-dir", str(artifact))

    def reconcile(self, store: object) -> list[str]:
        """Contain every tagged Machine even when no local attempt knows about it."""
        try:
            client = cast(
                FlyMachinesClient, self._client_factory(self._app or "", self._token_file or Path())
            )
            machines = client.list_machines("factory-owner", "agent-factory")
        except (FlyApiError, OSError, ValueError) as error:
            _cleanup_failure(store, "list", str(error))
            return []
        records = _settings_by_prefix(store, "fly:machine:")
        failures = _cleanup_failures(store)
        # A listing that succeeds resolves any earlier failed listing.
        failures.pop("list", None)
        self._refresh_stopped_deadlines(store, client, records, failures)
        known_ids = {
            value.get("machine_id")
            for value in records.values()
            if isinstance(value.get("machine_id"), str)
        } | _live_machine_ids(store)
        # Disposal already chose to destroy these, verified as owned, but Fly did not confirm it.
        pending_destroy = {
            value.get("machine_id")
            for value in records.values()
            if value.get("decision") == "destroy" and isinstance(value.get("machine_id"), str)
        }
        unknown: list[dict[str, object]] = []
        destroyed: list[str] = []
        now = time.time()
        for machine in machines:
            machine_id = machine.get("id")
            if not isinstance(machine_id, str) or not machine_id:
                continue
            deadline = _deadline_epoch(_metadata(machine).get("deadline_epoch"))
            if (deadline is not None and now > deadline) or machine_id in pending_destroy:
                if self._destroy_verified(client, machine_id):
                    destroyed.append(machine_id)
                    failures.pop(machine_id, None)
                    _clear_machine_record_by_id(store, records, machine_id)
                else:
                    failures[machine_id] = {
                        "machine_id": machine_id,
                        "reason": "destroy was not verified",
                    }
                continue
            metadata_run_id = _metadata(machine).get("run_id")
            get_run = getattr(store, "get_run", None)
            run = (
                cast(Callable[[str], Run | None], get_run)(metadata_run_id)
                if isinstance(metadata_run_id, str) and callable(get_run)
                else None
            )
            undisposed_run = run is not None and (
                run.status in NONTERMINAL_RUN_STATUSES
                or not _settings_by_prefix(store, f"fly:machine:{metadata_run_id}")
            )
            if machine_id not in known_ids and not undisposed_run:
                unknown.append(
                    {
                        "machine_id": machine_id,
                        "deadline_epoch": deadline,
                        "reason": "not recorded by local store",
                    }
                )
        _set_setting(store, "fly:unknown", {"machines": unknown} if unknown else {})
        _set_setting(
            store, "fly:cleanup-failed", {"machines": list(failures.values())} if failures else {}
        )
        self._resolve_gone_mismatch(store, client)
        return destroyed

    def _refresh_stopped_deadlines(
        self,
        store: object,
        client: FlyMachinesClient,
        records: Mapping[str, Mapping[str, object]],
        failures: dict[str, dict[str, object]],
    ) -> None:
        """Move quota-held deadlines with the provider reset, before they can bill."""
        if self._local is None or self._local.fly is None:
            return
        for key, record in records.items():
            if record.get("decision") != "stop":
                continue
            claim_id = record.get("claim_id")
            if not isinstance(claim_id, str):
                continue
            hold = _claim_hold(store, claim_id, "quota")
            deadline = _quota_machine_deadline(hold, self._local)
            machine_id = record.get("machine_id")
            if deadline is None or not isinstance(machine_id, str):
                continue
            if _deadline_epoch(record.get("deadline_epoch")) == deadline:
                continue
            try:
                # One merged, non-launching update: a bare env config would replace the
                # Machine's image, init, and ownership metadata, and would boot it.
                client.update_stopped_deadline(machine_id, deadline)
            except FlyApiError as error:
                failures[machine_id] = {"machine_id": machine_id, "reason": str(error)}
                continue
            failures.pop(machine_id, None)
            _set_setting(store, key, {**record, "deadline_epoch": deadline, "state": "stopped"})

    @staticmethod
    def _resolve_gone_mismatch(store: object, client: FlyMachinesClient) -> None:
        mismatch = _get_setting(store, "fly:mismatch")
        machine_id = mismatch.get("machine_id") if mismatch else None
        run_id = mismatch.get("run_id") if mismatch else None
        if not isinstance(machine_id, str):
            return
        try:
            client.get_machine(machine_id)
            return
        except FlyApiError as error:
            if error.status != 404:
                return
        _clear_setting(store, "fly:mismatch")
        if isinstance(run_id, str):
            finish = getattr(store, "finish_run", None)
            if callable(finish):
                with suppress(KeyError, RuntimeError):
                    finish(
                        run_id, execution_status="interrupted", result={"reason": "machine lost"}
                    )

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
            "image_digest": image_digest(machine),
            "cpu_kind": size.get("cpu_kind"),
            "cpus": size.get("cpus"),
            "memory_mb": size.get("memory_mb"),
            "region": machine.get("region"),
        }

    def _client(self, identity: Mapping[str, object]) -> FlyMachinesClient:
        return cast(
            FlyMachinesClient, self._client_factory(_app(identity), Path(_token_file(identity)))
        )


def _launcher_diagnostic() -> Diagnostic:
    """The launcher is the harness-to-Fly seam; without it no evaluation can start."""
    resolved = fly_launcher()
    if resolved is None:
        return Diagnostic(
            "Fly launcher",
            False,
            f"{LAUNCHER_NAME} is not installed alongside the running factory",
            "Reinstall the factory so its console scripts sit beside the running interpreter.",
            "eval-fly",
        )
    return Diagnostic("Fly launcher", True, resolved, "No action required.", "eval-fly")


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


def _flyctl_diagnostic(app: str, token_file: Path | None = None) -> Diagnostic:
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
        # ``flyctl ssh issue`` takes no app and mints a credential; a read-only
        # listing proves flyctl runs and can reach this app with the deploy token,
        # which is what ssh transport depends on. No Machine is needed or created.
        environment = dict(os.environ)
        if token_file is not None:
            with contextlib.suppress(FlyApiError):
                environment["FLY_ACCESS_TOKEN"] = read_token(token_file)
        completed = subprocess.run(
            ("flyctl", "machine", "list", "--app", app, "--json"),
            capture_output=True,
            timeout=30,
            check=False,
            env=environment,
            stdin=subprocess.DEVNULL,
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
        "flyctl reaches the app with the deploy token"
        if completed.returncode == 0
        else "flyctl cannot reach the app with the deploy token",
        "Restore flyctl SSH access to the Fly app.",
        "eval-fly",
    )


def _artifact_path(plan: object) -> Path | None:
    if isinstance(plan, Mapping):
        hints = cast(Mapping[str, object], plan).get("ownership_hints")
    else:
        hints = getattr(plan, "ownership_hints", None)
    if not isinstance(hints, Mapping):
        return None
    hint_values = cast(Mapping[str, object], hints)
    value = hint_values.get("artifact_path")
    return Path(value) if isinstance(value, str) else None


def _allowed_environment(plan: object) -> Mapping[str, str]:
    if isinstance(plan, Mapping):
        value = cast(Mapping[str, object], plan).get("allowed_environment", {})
    else:
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


def _claim_id(identity: Mapping[str, object]) -> str:
    value = identity.get("claim_id")
    if isinstance(value, str) and value:
        return value
    expected = identity.get("expected_metadata")
    values: Mapping[str, object] = (
        cast(Mapping[str, object], expected) if isinstance(expected, Mapping) else {}
    )
    value = values.get("claim_id")
    if not isinstance(value, str) or not value:
        raise ValueError("Fly claim identity is unavailable")
    return value


def _deadline_epoch(value: object) -> int | None:
    try:
        deadline = int(str(value))
    except (TypeError, ValueError):
        return None
    return deadline if deadline > 0 else None


def _set_setting(store: object | None, key: str, value: Mapping[str, object]) -> None:
    setter = getattr(store, "set_setting", None)
    if callable(setter):
        setter("runtime", key, value)


def _clear_setting(store: object | None, key: str) -> None:
    clearer = getattr(store, "clear_setting", None)
    if callable(clearer):
        clearer("runtime", key)


def _get_setting(store: object, key: str) -> Mapping[str, object] | None:
    getter = getattr(store, "get_setting", None)
    value = getter("runtime", key) if callable(getter) else None
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _settings_by_prefix(store: object, prefix: str) -> Mapping[str, Mapping[str, object]]:
    getter = getattr(store, "get_settings_by_prefix", None)
    if not callable(getter):
        return {}
    values = getter("runtime", prefix)
    return cast(Mapping[str, Mapping[str, object]], values) if isinstance(values, Mapping) else {}


def _live_machine_ids(store: object) -> set[str]:
    """Machines hosting a running attempt, which the run's progress records until disposal."""
    getter = getattr(store, "nonterminal_runs", None)
    runs = cast(list[object], getter()) if callable(getter) else []
    ids: set[str] = set()
    for run in runs:
        progress: object = getattr(run, "progress", None)
        if not isinstance(progress, Mapping):
            continue
        machine = cast(Mapping[str, object], progress).get("machine")
        if not isinstance(machine, Mapping):
            continue
        machine_id = cast(Mapping[str, object], machine).get("id")
        if isinstance(machine_id, str) and machine_id:
            ids.add(machine_id)
    return ids


def _set_machine_record(
    store: object | None, identity: Mapping[str, object], decision: Disposal, state: str
) -> None:
    claim_id = identity.get("expected_metadata")
    expected: Mapping[str, object] = (
        cast(Mapping[str, object], claim_id) if isinstance(claim_id, Mapping) else {}
    )
    claim = expected.get("claim_id", identity.get("claim_id"))
    if not isinstance(claim, str) or not claim:
        return
    run_id = identity.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return
    _set_setting(
        store,
        f"fly:machine:{run_id}",
        {
            "machine_id": _machine_id(identity),
            "run_id": run_id,
            "claim_id": claim,
            "decision": decision,
            "deadline_epoch": _deadline_epoch(identity.get("deadline_epoch")),
            "state": state,
        },
    )


def _clear_machine_record(store: object | None, identity: Mapping[str, object]) -> None:
    run_id = identity.get("run_id")
    if isinstance(run_id, str) and run_id:
        _clear_setting(store, f"fly:machine:{run_id}")


def _clear_machine_record_by_id(
    store: object, records: Mapping[str, Mapping[str, object]], machine_id: str
) -> None:
    for key, record in records.items():
        if record.get("machine_id") == machine_id:
            _clear_setting(store, key)


def _cleanup_failure(store: object | None, machine_id: str, reason: str) -> None:
    existing = _cleanup_failures(store)
    existing[machine_id] = {"machine_id": machine_id, "reason": reason}
    _set_setting(store, "fly:cleanup-failed", {"machines": list(existing.values())})


def _clear_cleanup_failure(store: object | None, machine_id: str) -> None:
    existing = _cleanup_failures(store)
    if machine_id not in existing:
        return
    existing.pop(machine_id)
    if existing:
        _set_setting(store, "fly:cleanup-failed", {"machines": list(existing.values())})
    else:
        _clear_setting(store, "fly:cleanup-failed")


def _cleanup_failures(store: object | None) -> dict[str, dict[str, object]]:
    value = _get_setting(store, "fly:cleanup-failed") if store is not None else None
    machines = value.get("machines") if value else None
    if not isinstance(machines, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in cast(list[object], machines):
        if not isinstance(item, Mapping):
            continue
        record = cast(Mapping[str, object], item)
        machine_id = record.get("machine_id")
        if isinstance(machine_id, str):
            result[machine_id] = dict(record)
    return result


def _claim_hold(store: object, claim_id: str, name: str) -> Mapping[str, object] | None:
    getter = getattr(store, "get_hold", None)
    value = getter(claim_id, name) if callable(getter) else None
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _quota_machine_deadline(
    hold: Mapping[str, object] | None, local: LocalConfig | None
) -> int | None:
    if hold is None or local is None or local.fly is None:
        return None
    value = hold.get("until")
    if not isinstance(value, str):
        return None
    try:
        reset = datetime.fromisoformat(value)
    except ValueError:
        return None
    if reset.tzinfo is None or reset <= datetime.now(UTC):
        return None
    eligible = reset.astimezone(local.schedule.timezone).replace(second=0, microsecond=0)
    while not local.schedule.allows_admission(eligible):
        eligible += timedelta(minutes=1)
    return int(
        eligible.timestamp() + local.limits.total_seconds + local.fly.collection_grace_seconds
    )


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
