"""Fix work-kind handler: bug admission, sandboxed launch, and outcome mapping."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_factory.config import FixTarget, LocalConfig, ScheduleConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    ClaimPresentation,
    ExecutionPlan,
    RequestSnapshot,
)
from agent_factory.github import GitHubClient, IssueComment, ProjectQueueItem
from agent_factory.operations import Diagnostic
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimDraft, ClaimStore, Run
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.supervisor import SupervisionLimits
from agent_factory.work_kinds.base import (
    Classification,
    Feedback,
    Gesture,
    Outcome,
    Preparation,
    ReportEvent,
)
from agent_factory.work_kinds.fix.cleanup import FixCleanup
from agent_factory.work_kinds.fix.outcome import read_outcome
from agent_factory.work_kinds.fix.readiness import check_readiness

_WRITER_PERMISSIONS = frozenset({"write", "maintain", "admin"})

Resolver = Callable[[FixTarget], tuple[str, str, str]]


class FixHandler:
    """Owns every fix-shaped decision: admission, launch, and outcome mapping."""

    kind = "fix"

    def __init__(
        self,
        shared: SharedConfig,
        local: LocalConfig,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._shared = shared
        self._local = local
        self._contract = shared.fix.contract
        self._resolver = resolver
        self._store: ClaimStore | None = None
        self._cleanup: FixCleanup | None = None
        self._installation_token: Callable[[], str] | None = None

    @classmethod
    def from_config(cls, shared: SharedConfig, local: LocalConfig) -> FixHandler:
        return cls(shared, local)

    def attach_store(self, store: ClaimStore) -> None:
        self._store = store
        self._cleanup = FixCleanup(store)

    def attach_installation_token(self, provider: Callable[[], str]) -> None:
        """Let readiness reject a fix credential that is really the App installation token."""
        self._installation_token = provider

    def handles(self, snapshot: RequestSnapshot) -> bool:
        return (
            not snapshot.closed
            and snapshot.owner == "factory"
            and snapshot.status == "Ready"
            and snapshot.issue_type == "Bug"
            and "needs-input" not in snapshot.labels
            and snapshot.author_permission in _WRITER_PERMISSIONS
        )

    def snapshot(
        self, card: ProjectQueueItem, client: object, shared: SharedConfig
    ) -> RequestSnapshot | None:
        source = card.source
        targets = {target.repository for target in shared.fix.targets}
        if (
            source.repository not in targets
            or source.state.lower() == "closed"
            or source.issue_type != "Bug"
            or "needs-input" in source.labels
            or card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
            or _card_status(shared, card) != "Ready"
        ):
            return None
        github = cast(GitHubClient, client)
        permission = github.get_permission(source.repository, source.author)
        if permission not in _WRITER_PERMISSIONS:
            return None
        return RequestSnapshot(
            source.repository,
            source.number,
            source.id,
            card.id,
            source.author,
            permission,
            "Bug",
            source.labels,
            "Ready",
            "factory",
            None,
            source.body,
            False,
        )

    def request_fingerprint(self, snapshot: RequestSnapshot) -> str | Feedback:
        return f"fix:{snapshot.repository}#{snapshot.issue_number}"

    def accept(
        self,
        snapshot: RequestSnapshot,
        store: ClaimStore,
        resolve: object,
    ) -> ClaimDraft | Feedback:
        target = next(
            (t for t in self._shared.fix.targets if t.repository == snapshot.repository), None
        )
        if target is None:
            return Feedback(f"{snapshot.repository} is not a configured fix target")
        resolver = cast(Resolver, resolve)
        target_sha, runner_sha, skills_sha = resolver(target)
        frozen: dict[str, object] = {
            "version": 1,
            "kind": "fix",
            "target": {"repository": target.repository, "branch": target.branch},
            "revisions": {"target": target_sha, "runner": runner_sha, "skills": skills_sha},
            "roles": dict(self._shared.fix.defaults),
            "contract": self._contract,
        }
        fingerprint = self.request_fingerprint(snapshot)
        assert isinstance(fingerprint, str)
        return ClaimDraft(
            snapshot.repository,
            snapshot.issue_number,
            snapshot.issue_id,
            snapshot.project_item_id,
            self.kind,
            fingerprint,
            frozen,
        )

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        token: str | None = None
        if self._installation_token is not None:
            try:
                token = self._installation_token()
            except Exception:
                # Minting failures are reported by the shared GitHub diagnostics; the
                # credential check below still runs its file-shape rules without the
                # equality comparison rather than crashing readiness.
                token = None
        return check_readiness(local, shared, installation_token=token)

    def prepare(self, claim: Claim) -> Preparation:
        return Preparation()

    def next_unit(self, claim: Claim, runs: Sequence[Run]) -> tuple[str | None, str]:
        unit_runs = [run for run in runs if run.unit_key == "fix"]
        if not unit_runs:
            return "fix", "initial"
        latest = unit_runs[-1]
        if latest.status in NONTERMINAL_RUN_STATUSES:
            return None, "initial"
        if _needs_recovery(latest) and latest.reason != "recovery":
            return "fix", "recovery"
        return None, "initial"

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan:
        # Sandbox launch (mirrors, clones, and the sandbox-run.sh invocation) is not
        # wired up yet; fail closed rather than launch nothing observable.
        raise ReadinessError("fix sandbox launch is not yet implemented")

    def read_result(self, run: Run) -> AttemptResult:
        base = AttemptResult(
            "interrupted" if run.status == "timed_out" else run.status, None, run.result
        )
        if run.status == "timed_out":
            return base
        payload = read_outcome(Path(run.evidence_path), self._contract)
        if payload is None:
            return base
        outcome = payload.get("outcome")
        return AttemptResult("completed", outcome if isinstance(outcome, str) else None, payload)

    def classify(self, run: Run, result: AttemptResult) -> Classification:
        if result.product_verdict is None:
            return Classification("technical")
        if result.product_verdict == "needs-input":
            return Classification("blocked")
        return Classification("settled")

    def settle(self, claim: Claim, runs: Sequence[Run]) -> Outcome | None:
        if claim.lifecycle in {"settled", "blocked"}:
            return None
        unit_runs = [run for run in runs if run.unit_key == "fix"]
        if not unit_runs:
            return None
        latest = unit_runs[-1]
        if latest.status not in {"completed", "failed"}:
            return None
        if _needs_recovery(latest):
            return None
        outcome = latest.result.get("outcome")
        if outcome == "needs-input":
            if self._store is not None:
                reasons = _reasons_text(latest.result)
                self._store.set_claim_lifecycle(
                    claim.id, "blocked", {"declined_at": datetime.now(UTC).isoformat()}
                )
                self._store.record_event(
                    claim.id, f"{latest.id}:needs-input", f"Needs input.\n\n{reasons}"
                )
            return None
        if outcome == "pull-request":
            return Outcome("pending-human-review", event_body=_pr_message(latest.result))
        if outcome == "failed":
            return Outcome("failed", event_body=_failed_message(latest.result))
        return None

    def presentation(self, claim: Claim) -> ClaimPresentation:
        if claim.lifecycle == "cancelled":
            return ClaimPresentation("Done", None, ("cancelled",))
        if claim.lifecycle == "settled":
            verdict = claim.outcome.get("verdict")
            return ClaimPresentation("Review", verdict if isinstance(verdict, str) else None, ())
        if claim.lifecycle == "blocked":
            return ClaimPresentation("Running", None, (), labels={"needs-input": True})
        if claim.lifecycle == "waiting":
            verdict = claim.outcome.get("verdict")
            return ClaimPresentation(
                "Ready", verdict if isinstance(verdict, str) else "infra-error", ()
            )
        idle = True
        if self._store is not None:
            idle = not any(
                run.status in NONTERMINAL_RUN_STATUSES
                for run in self._store.runs_for_claim(claim.id)
            )
        return ClaimPresentation("Ready" if idle else "Running", None, ())

    def report_events(self, claim: Claim, run: Run, result: AttemptResult) -> list[ReportEvent]:
        return []

    def gesture(
        self, claim: Claim, card: ProjectQueueItem, comments: Sequence[IssueComment]
    ) -> Gesture | None:
        if claim.lifecycle == "settled":
            return "fresh" if _card_status(self._shared, card) == "Ready" else None
        if claim.lifecycle == "blocked":
            if comments or _card_status(self._shared, card) == "Ready":
                return "unblock"
            return None
        return None

    def limits(self, local: LocalConfig) -> SupervisionLimits:
        return SupervisionLimits(
            local.fix.limits.inactivity_seconds,
            local.fix.limits.execution_seconds,
            local.fix.limits.total_seconds,
        )

    def window(self, local: LocalConfig) -> ScheduleConfig:
        if local.fix.schedule is not None:
            return local.fix.schedule
        return ScheduleConfig.always(local.schedule.timezone, local.schedule.poll_seconds)

    def providers(self, claim: Claim) -> set[str]:
        roles = _mapping(claim.frozen_spec.get("roles"))
        providers: set[str] = set()
        for value in roles.values():
            if isinstance(value, str) and value:
                providers.add(value.split(":", 1)[0])
        return providers

    def cleanup(self, claim: Claim, *, board_status: str = "") -> None:
        if self._cleanup is not None:
            self._cleanup.reconcile(claim.id, board_status=board_status)


def _card_status(shared: SharedConfig, card: ProjectQueueItem) -> str:
    value = card.fields.get(shared.project.status.id)
    return next(
        (key.title() for key, option in shared.project.status.options.items() if value == option),
        "",
    )


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _needs_recovery(run: Run) -> bool:
    return run.status in {"failed", "interrupted"} and run.result.get("outcome") is None


def _reasons_text(result: Mapping[str, object]) -> str:
    reasons = result.get("reasons")
    if isinstance(reasons, list):
        return "\n".join(f"- {reason}" for reason in cast(list[object], reasons))
    return ""


def _pr_message(result: Mapping[str, object]) -> str:
    pr = _mapping(result.get("pr"))
    url = pr.get("url")
    return f"Pull request opened: {url}" if isinstance(url, str) else "Pull request opened."


def _failed_message(result: Mapping[str, object]) -> str:
    reasons = _reasons_text(result)
    pr = _mapping(result.get("pr"))
    url = pr.get("url")
    lines = ["Fix attempt failed."]
    if reasons:
        lines.append(reasons)
    if isinstance(url, str):
        lines.append(f"Pull request: {url}")
    return "\n\n".join(lines)
