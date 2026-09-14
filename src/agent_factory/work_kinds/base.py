"""Work-kind handler protocol and the shared dataclasses the core dispatches through."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, cast

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, ScheduleConfig, SharedConfig
    from agent_factory.controller import (
        AttemptResult,
        ClaimPresentation,
        ExecutionPlan,
        RequestSnapshot,
    )
    from agent_factory.github import IssueComment, ProjectQueueItem
    from agent_factory.operations import Diagnostic
    from agent_factory.store import Claim, ClaimDraft, ClaimStore, Run
    from agent_factory.suites.and_scene import PreparedWorktrees
    from agent_factory.supervisor import SupervisionLimits

Gesture = Literal["fresh", "unblock"]
ClassificationKind = Literal["technical", "settled", "blocked", "quota"]


@dataclass(frozen=True)
class Feedback:
    """Admission-time rejection that the controller delivers without creating a claim."""

    explanation: str
    labels: Mapping[str, bool] = field(default_factory=lambda: dict[str, bool]())


@dataclass(frozen=True)
class Preparation:
    """Kind-specific artifacts produced before a run is reserved."""

    worktrees: PreparedWorktrees | None = None
    payload: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True)
class Classification:
    """How the generic core should account for a recorded attempt."""

    kind: ClassificationKind


@dataclass(frozen=True)
class Outcome:
    """Aggregate claim settlement produced from completed units."""

    verdict: str
    event_key: str = "handoff"
    event_body: str = ""


@dataclass(frozen=True)
class ReportEvent:
    """A durable issue comment the core should persist and deliver."""

    key: str
    body: str


class WorkKindHandler(Protocol):
    """Kind-specific interpretation used by the generic controller and runtime."""

    kind: str

    def handles(self, snapshot: RequestSnapshot) -> bool: ...

    def snapshot(
        self, card: ProjectQueueItem, client: object, shared: SharedConfig
    ) -> RequestSnapshot | None: ...

    def request_fingerprint(self, snapshot: RequestSnapshot) -> str | Feedback: ...

    def accept(
        self,
        snapshot: RequestSnapshot,
        store: ClaimStore,
        resolve: object,
    ) -> ClaimDraft | Feedback: ...

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]: ...

    def prepare(self, claim: Claim) -> Preparation: ...

    def next_unit(self, claim: Claim, runs: Sequence[Run]) -> tuple[str | None, str]: ...

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan: ...

    def read_result(self, run: Run) -> AttemptResult: ...

    def classify(self, run: Run, result: AttemptResult) -> Classification: ...

    def settle(self, claim: Claim, runs: Sequence[Run]) -> Outcome | None: ...

    def presentation(self, claim: Claim) -> ClaimPresentation: ...

    def report_events(self, claim: Claim, run: Run, result: AttemptResult) -> list[ReportEvent]: ...

    def gesture(
        self, claim: Claim, card: ProjectQueueItem, comments: Sequence[IssueComment]
    ) -> Gesture | None: ...

    def limits(self, local: LocalConfig) -> SupervisionLimits: ...

    def window(self, local: LocalConfig) -> ScheduleConfig: ...

    def providers(self, claim: Claim) -> set[str]: ...

    def cleanup(self, claim: Claim, *, board_status: str = "") -> None: ...

    def attach_store(self, store: ClaimStore) -> None: ...

    def attempt_message(
        self, run: Run, stored_result: Mapping[str, object], *, stage: str
    ) -> str: ...

    def refs_text(self, claim: Claim) -> str | None: ...

    def frozen_inputs_event(self, claim: Claim) -> str | None: ...


def card_status(shared: SharedConfig, card: ProjectQueueItem) -> str:
    """Logical board status (Ready, Running, ...) of a card from its option id."""
    value = card.fields.get(shared.project.status.id)
    return next(
        (key.title() for key, option in shared.project.status.options.items() if value == option),
        "",
    )


def mapping(value: object) -> Mapping[str, object]:
    """Coerce a loosely typed JSON value to a mapping, treating anything else as empty."""
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def providers_from_roles(roles: Mapping[str, object]) -> set[str]:
    """Provider names from ``cli:model:effort`` role profiles."""
    return {value.split(":", 1)[0] for value in roles.values() if isinstance(value, str) and value}


def claim_is_idle(store: ClaimStore | None, claim: Claim) -> bool:
    """True when no attempt of the claim is reserved, running, or observing."""
    from agent_factory.store import NONTERMINAL_RUN_STATUSES

    if store is None:
        return True
    return not any(run.status in NONTERMINAL_RUN_STATUSES for run in store.runs_for_claim(claim.id))
