"""Stable ownership boundary for durable execution backends."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from agent_factory.operations import Diagnostic


@dataclass(frozen=True)
class ExecutionIdentity:
    values: Mapping[str, object]


@dataclass(frozen=True)
class Probe:
    state: Literal["alive", "stopped", "gone", "mismatch", "unknown"]
    detail: str = ""


Disposal = Literal["destroy", "stop", "keep"]


class ExecutionBackend(Protocol):
    name: str

    def readiness(self, local: object, shared: object) -> list[Diagnostic]: ...
    def identity_from_plan(self, plan: object, run: object) -> Mapping[str, object] | None: ...
    def probe(self, identity: Mapping[str, object]) -> Probe: ...
    def terminate(self, identity: Mapping[str, object]) -> bool: ...
    def dispose(
        self, identity: Mapping[str, object], decision: Disposal, store: object | None = None
    ) -> None: ...
    def attach_argv(self, plan: object, run: object) -> tuple[str, ...]: ...
    def reconcile(self, store: object) -> list[str]: ...
    def provenance(self, identity: Mapping[str, object]) -> Mapping[str, object]: ...
