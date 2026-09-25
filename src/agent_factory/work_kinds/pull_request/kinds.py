"""Frozen data describing each pull-request work kind."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from agent_factory.config import FixTarget, LocalConfig, ScheduleConfig, SharedConfig


class KindLimits(Protocol):
    @property
    def inactivity_seconds(self) -> int: ...

    @property
    def execution_seconds(self) -> int: ...

    @property
    def total_seconds(self) -> int: ...


class KindLocalConfig(Protocol):
    @property
    def limits(self) -> KindLimits: ...

    @property
    def schedule(self) -> ScheduleConfig | None: ...

    @property
    def execution(self) -> str: ...

    @property
    def minimum_free_gib(self) -> float | None: ...


class ReconcilePolicy(Enum):
    SETTLE_ON_OPEN_PR = "settle-on-open-pr"
    RESUME_FROM_OWN_BRANCH = "resume-from-own-branch"


@dataclass(frozen=True)
class PullRequestKind:
    kind: str
    unit_key: str
    noun: str
    item_noun: str
    issue_type: Callable[[SharedConfig], str]
    workflow_name: str
    workflow_file: str
    staged_files: tuple[str, ...]
    contract: Callable[[SharedConfig], str]
    outcome_file: str
    branch_prefix: str
    sync_marker: str
    allowed_modes: tuple[str, ...]
    roles: tuple[str, ...]
    doctor_groups: Mapping[str, str]
    reconcile: ReconcilePolicy
    local: Callable[[LocalConfig], KindLocalConfig]
    defaults: Callable[[SharedConfig], Mapping[str, str]]
    targets: Callable[[SharedConfig], tuple[FixTarget, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "doctor_groups", MappingProxyType(dict(self.doctor_groups)))


FIX = PullRequestKind(
    kind="fix",
    unit_key="fix",
    noun="Fix",
    item_noun="bug",
    issue_type=lambda shared: shared.routing.bug_type,
    workflow_name="factory-fix",
    workflow_file="factory-fix-v1.0.yaml",
    staged_files=(
        "factory-fix-v1.0.yaml",
        "record-triage.sh",
        "record-outcome.sh",
        "check-contract.sh",
    ),
    contract=lambda shared: shared.fix.contract,
    outcome_file="fix-outcome.json",
    branch_prefix="factory/fix",
    sync_marker="fix-sync",
    allowed_modes=("docker", "host"),
    roles=("lead", "implementor", "tester"),
    doctor_groups={"docker": "fix-sandbox", "host": "fix-host"},
    reconcile=ReconcilePolicy.SETTLE_ON_OPEN_PR,
    local=lambda local: local.fix,
    defaults=lambda shared: shared.fix.defaults,
    targets=lambda shared: shared.fix.targets,
)


def registered() -> tuple[PullRequestKind, ...]:
    return (FIX,)
