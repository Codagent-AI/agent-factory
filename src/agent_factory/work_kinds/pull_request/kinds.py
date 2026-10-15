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
    # The contract when configuration names none; read when no shared config is at hand.
    default_contract: str
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
    # Whether the kind's configuration section is present; without shared config, unknown.
    enabled: Callable[[SharedConfig | None], bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "doctor_groups", MappingProxyType(dict(self.doctor_groups)))


FEATURE_STAGED_FILES = (
    "factory-task-guard-v1.0.yaml",
    "factory-feature-v1.0.yaml",
    "factory-define-v1.0.yaml",
    "factory-define-rules.md",
    "prepare-branch.sh",
    "merge-base.sh",
    "continue-change.sh",
    "check-merge.sh",
    "record-merge-stop.sh",
    "factory-resume-skip.sh",
    "record-stop.sh",
    "record-archive-block.sh",
    "checkpoint.sh",
    "reconcile-skip.sh",
    "locate-archive.py",
    "verify-failure.py",
    "check-openspec.sh",
    "record-validation-failure.sh",
    "verify-classification.py",
    "record-classification-failure.py",
    "verify-feature-outcome.py",
    "annotate-pr.sh",
    "annotate-pr.py",
    "task-compliance-gate.py",
    "mark-later-commits.py",
    "check-contract.sh",
    "record-outcome.sh",
)

TASK_STAGED_FILES = (
    "factory-task-v1.0.yaml",
    "check-contract.sh",
    "record-triage.sh",
    "decision_json.py",
    "record-outcome.sh",
    "annotate-chore-pr.sh",
)


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
    default_contract="factory-fix/1",
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
    enabled=lambda shared: True,
)


FEATURE = PullRequestKind(
    kind="feature",
    unit_key="feature",
    noun="Feature",
    item_noun="feature",
    issue_type=lambda shared: shared.routing.feature_type,
    workflow_name="factory-feature",
    workflow_file="factory-feature-v1.0.yaml",
    staged_files=FEATURE_STAGED_FILES,
    contract=lambda shared: (
        shared.feature.contract if shared.feature is not None else FEATURE.default_contract
    ),
    default_contract="factory-feature/1",
    outcome_file="feature-outcome.json",
    branch_prefix="factory/feature",
    sync_marker="feature-sync",
    allowed_modes=("host",),
    roles=("lead", "implementor", "tester", "crosscheck"),
    doctor_groups={"host": "feature-host"},
    reconcile=ReconcilePolicy.RESUME_FROM_OWN_BRANCH,
    local=lambda local: local.feature,
    defaults=lambda shared: shared.feature.defaults if shared.feature is not None else {},
    targets=lambda shared: shared.fix.targets,
    enabled=lambda shared: shared is not None and shared.feature is not None,
)


TASK = PullRequestKind(
    kind="task",
    unit_key="task",
    noun="Task",
    item_noun="task",
    issue_type=lambda shared: shared.routing.task_type,
    workflow_name="factory-task",
    workflow_file="factory-task-v1.0.yaml",
    staged_files=TASK_STAGED_FILES,
    contract=lambda shared: (
        shared.task.contract if shared.task is not None else TASK.default_contract
    ),
    default_contract="factory-task/1",
    outcome_file="task-outcome.json",
    branch_prefix="factory/task",
    sync_marker="task-sync",
    allowed_modes=("host",),
    roles=("lead", "implementor", "tester"),
    doctor_groups={"host": "task-host"},
    reconcile=ReconcilePolicy.SETTLE_ON_OPEN_PR,
    local=lambda local: local.task,
    defaults=lambda shared: shared.task.defaults if shared.task is not None else {},
    targets=lambda shared: shared.fix.targets,
    enabled=lambda shared: shared is not None and shared.task is not None,
)


def registered() -> tuple[PullRequestKind, ...]:
    return (FIX, FEATURE, TASK)
