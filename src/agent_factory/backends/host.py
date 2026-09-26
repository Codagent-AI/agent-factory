"""Ownership of a local process group."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from agent_factory.backends import Disposal, Probe
from agent_factory.backends.resolve import plan_hints
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.operations import Diagnostic
from agent_factory.store import Run


class HostProcessBackend:
    name = "host"
    supports_attach = False

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        from agent_factory.operations import _fix_diagnostics  # pyright: ignore[reportPrivateUsage]

        if getattr(getattr(local, "fix", None), "execution", None) == "host":
            return list(_fix_diagnostics(local, shared))
        return []

    def identity_from_plan(self, plan: object, run: object) -> Mapping[str, object] | None:
        hints = plan_hints(plan)
        provenance: dict[str, object] = {
            key: hints[key] for key in ("runner_executable", "runner_version") if key in hints
        }
        return {**(run.process if isinstance(run, Run) else {}), **provenance}

    def probe(self, identity: Mapping[str, object]) -> Probe:
        from agent_factory.supervisor import (
            ProcessProbeError,
            process_identity_status,
            process_start_identity,
        )

        status = process_identity_status(identity)
        if (
            status == "missing"
            and isinstance(identity.get("pid"), int)
            and isinstance(identity.get("start"), str)
        ):
            try:
                if process_start_identity(cast(int, identity["pid"])) is not None:
                    return Probe("mismatch", "recorded process ID belongs to another process")
            except ProcessProbeError as error:
                return Probe("unknown", str(error))
        return Probe("gone" if status == "missing" else status)  # type: ignore[arg-type]

    def adopt(self, plan: object, run: object, store: object) -> Probe:
        return self.probe(self.identity_from_plan(plan, run) or {})

    def terminate(self, identity: Mapping[str, object]) -> bool:
        from agent_factory.supervisor import terminate_owned_process

        return terminate_owned_process(identity)

    def dispose(
        self, identity: Mapping[str, object], decision: Disposal, store: object | None = None
    ) -> None:
        pass

    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]:
        raise NotImplementedError("host processes cannot be reattached")

    def reconcile(self, store: object) -> list[str]:
        return []

    def provenance(self, identity: Mapping[str, object]) -> Mapping[str, object]:
        return {
            key: identity[key] for key in ("runner_executable", "runner_version") if key in identity
        }
