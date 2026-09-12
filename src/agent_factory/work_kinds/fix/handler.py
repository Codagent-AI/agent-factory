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
from agent_factory.github import WRITER_PERMISSIONS, GitHubClient, IssueComment, ProjectQueueItem
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
    card_status,
    claim_is_idle,
    mapping,
    providers_from_roles,
)
from agent_factory.work_kinds.fix.cleanup import FixCleanup
from agent_factory.work_kinds.fix.outcome import read_outcome
from agent_factory.work_kinds.fix.readiness import check_readiness

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
            and snapshot.author_permission in WRITER_PERMISSIONS
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
            or card_status(shared, card) != "Ready"
        ):
            return None
        github = cast(GitHubClient, client)
        permission = github.get_permission(source.repository, source.author)
        if permission not in WRITER_PERMISSIONS:
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
        minting_failure: Diagnostic | None = None
        if self._installation_token is not None:
            try:
                token = self._installation_token()
            except Exception as error:  # noqa: BLE001 - any minting failure fails closed
                # Fail closed: without the App token the fix credential cannot be proven
                # distinct from it, so readiness reports the failure as a diagnostic
                # instead of skipping the check or crashing the readiness pass.
                minting_failure = Diagnostic(
                    "fix credential",
                    False,
                    f"cannot mint the App installation token to validate the fix credential: "
                    f"{error}",
                    "Repair the GitHub App key or installation, then rerun doctor.",
                )
        diagnostics = check_readiness(local, shared, installation_token=token)
        if minting_failure is not None:
            diagnostics = [
                minting_failure if d.name == "fix credential" else d for d in diagnostics
            ]
        return diagnostics

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
        # Cancelled claims are presented by the controller before dispatching here.
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
        return ClaimPresentation(
            "Ready" if claim_is_idle(self._store, claim) else "Running", None, ()
        )

    def report_events(self, claim: Claim, run: Run, result: AttemptResult) -> list[ReportEvent]:
        return []

    def gesture(
        self, claim: Claim, card: ProjectQueueItem, comments: Sequence[IssueComment]
    ) -> Gesture | None:
        if claim.lifecycle == "settled":
            return "fresh" if card_status(self._shared, card) == "Ready" else None
        if claim.lifecycle == "blocked":
            if comments or card_status(self._shared, card) == "Ready":
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
        return providers_from_roles(mapping(claim.frozen_spec.get("roles")))

    def attempt_message(self, run: Run, stored_result: Mapping[str, object], *, stage: str) -> str:
        return f"{run.unit_key} settled."

    def refs_text(self, claim: Claim) -> str | None:
        return None

    def frozen_inputs_event(self, claim: Claim) -> str | None:
        return None

    def cleanup(self, claim: Claim, *, board_status: str = "") -> None:
        if self._cleanup is not None:
            self._cleanup.reconcile(claim.id, board_status=board_status)


def _needs_recovery(run: Run) -> bool:
    return run.status in {"failed", "interrupted"} and run.result.get("outcome") is None


def _reasons_text(result: Mapping[str, object]) -> str:
    reasons = result.get("reasons")
    if isinstance(reasons, list):
        return "\n".join(f"- {reason}" for reason in cast(list[object], reasons))
    return ""


def _pr_message(result: Mapping[str, object]) -> str:
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    return f"Pull request opened: {url}" if isinstance(url, str) else "Pull request opened."


def _failed_message(result: Mapping[str, object]) -> str:
    reasons = _reasons_text(result)
    pr = mapping(result.get("pr"))
    url = pr.get("url")
    lines = ["Fix attempt failed."]
    if reasons:
        lines.append(reasons)
    if isinstance(url, str):
        lines.append(f"Pull request: {url}")
    return "\n\n".join(lines)
