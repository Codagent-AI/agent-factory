"""Policy-only claim controller.

The controller is intentionally ignorant of suite command details.  A caller
supplies pinned revisions and executes reserved runs; this module persists the
admission decision, normalized observations, and report delivery state.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from agent_factory.github import GitHubApiError, IssueComment
from agent_factory.store import Claim, ClaimDraft, ClaimStore, Run
from agent_factory.work_kinds.eval import EvalDefaults, ParsedRequest, parse_request

_WRITER_PERMISSIONS = frozenset({"write", "maintain", "admin"})


@dataclass(frozen=True)
class RequestSnapshot:
    repository: str
    issue_number: int
    issue_id: str
    project_item_id: str
    author: str
    author_permission: str | None
    issue_type: str | None
    labels: frozenset[str]
    status: str
    owner: str | None
    verdict: str | None
    body: str
    closed: bool


@dataclass(frozen=True)
class WorkUnit:
    key: str
    evidence_path: str


@dataclass(frozen=True)
class ExecutionPlan:
    argv: tuple[str, ...]
    working_directory: str
    allowed_environment: Mapping[str, str]
    credential_files: tuple[str, ...]
    progress_sources: tuple[str, ...]
    ownership_hints: Mapping[str, str]
    resume: bool


@dataclass(frozen=True)
class Observation:
    running: bool
    progress_changed: bool
    quota_until: datetime | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class AttemptResult:
    execution_status: str
    product_verdict: str | None
    result: Mapping[str, object]
    quota_until: datetime | None = None
    resumable: bool | None = None


@dataclass(frozen=True)
class ClaimPresentation:
    status: str
    verdict: str | None
    events: tuple[str, ...]


class ReportingClient(Protocol):
    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]: ...

    def create_comment(self, repository: str, number: int, body: str) -> str | None: ...


class Controller:
    """Serializes admission while allowing supervisors to own running attempts."""

    _cycle_lock = threading.Lock()

    def __init__(
        self,
        store: ClaimStore,
        github: ReportingClient,
        defaults: EvalDefaults,
        *,
        harness_sha: str,
        suite: str = "and-scene",
        factory_login: str = "codagent-factory[bot]",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._github = github
        self._defaults = defaults
        self._harness_sha = harness_sha
        self._suite = suite
        self._factory_login = factory_login
        self._now = now or (lambda: datetime.now(UTC))

    def pause(self) -> None:
        self._store.set_paused(True)

    def resume(self) -> None:
        self._store.set_paused(False)

    def paused(self) -> bool:
        return self._store.is_paused()

    def accept(
        self,
        snapshot: RequestSnapshot,
        *,
        resolve: Callable[[ParsedRequest], tuple[str, str]],
    ) -> Claim | None:
        """Validate and freeze a new request, never creating a claim for bad input."""
        if not self._eligible(snapshot):
            return None
        try:
            request = parse_request(snapshot.body, self._defaults)
        except ValueError as error:
            self._invalid_feedback(snapshot, str(error))
            return None
        old_claims = self._store.claims_for_item(snapshot.project_item_id)
        current = next(
            (
                claim
                for claim in reversed(old_claims)
                if claim.lifecycle not in {"superseded", "cancelled"}
            ),
            None,
        )
        if current is not None and self._is_active(current):
            return current
        if current is not None and current.request_fingerprint == request.fingerprint:
            return current
        runner_sha, skills_sha = resolve(request)
        frozen = request.freeze(
            runner_sha=runner_sha,
            skills_sha=skills_sha,
            harness_sha=self._harness_sha,
            suite=self._suite,
        )
        draft = ClaimDraft(
            snapshot.repository,
            snapshot.issue_number,
            snapshot.issue_id,
            snapshot.project_item_id,
            "eval",
            request.fingerprint,
            frozen.payload,
        )
        claim = (
            self._store.create_claim(draft)
            if current is None
            else self._store.supersede_and_create(current.id, draft)
        )
        self._store.set_claim_lifecycle(claim.id, "active", {})
        self._store.record_event(claim.id, "accepted", "Evaluation inputs accepted and frozen.")
        return cast(Claim, self._store.get_claim(claim.id))

    def reserve_next(self, claim_id: str, *, readiness: Callable[[], str | None]) -> Run | None:
        """Reserve exactly one ready work unit after all launch-time controls pass."""
        with self._cycle_lock:
            claim = self._required_claim(claim_id)
            if claim.lifecycle in {"settled", "cancelled", "superseded"} or self.paused():
                return None
            issue = readiness()
            if issue is not None:
                self._store.set_hold(claim.id, "readiness", {"reason": issue})
                self._store.record_event(claim.id, f"readiness:{issue}", f"Waiting: {issue}")
                return None
            quota = self._store.get_hold(claim.id, "quota")
            if quota is not None and _hold_active(quota, self._now()):
                return None
            repetitions = _repetitions(claim)
            next_unit, reason = self._next_unit(claim, repetitions)
            if next_unit is None:
                self._settle_if_complete(claim)
                return None
            run = self._store.reserve_run(
                claim.id,
                next_unit,
                reason=reason,
                evidence_path=f"artifacts/{claim.id}-{next_unit}",
            )
            self._store.set_claim_lifecycle(claim.id, "active", {})
            self._store.record_event(
                claim.id,
                f"{next_unit}:attempt-{run.attempt_number}:start",
                f"Starting {next_unit}, attempt {run.attempt_number + 1}.",
            )
            return run

    def record_result(self, run_id: str, result: AttemptResult) -> None:
        """Persist a suite-normalized result before changing aggregate presentation."""
        run = self._required_run(run_id)
        stored_result = dict(result.result)
        if result.product_verdict is not None:
            stored_result["product_verdict"] = result.product_verdict
        if result.resumable is not None:
            stored_result["resumable"] = result.resumable
        if result.quota_until is not None:
            stored_result["quota_until"] = result.quota_until.isoformat()
            self._store.finish_run(run.id, execution_status="deferred", result=stored_result)
            self._store.set_hold(run.claim_id, "quota", {"until": result.quota_until.isoformat()})
            self._store.set_claim_lifecycle(run.claim_id, "waiting", {"verdict": "quota-deferred"})
            self._store.record_event(
                run.claim_id,
                f"{run.unit_key}:attempt-{run.attempt_number}:quota",
                f"{run.unit_key} is waiting for usage reset at {result.quota_until.isoformat()}.",
            )
            return
        self._store.finish_run(
            run.id, execution_status=result.execution_status, result=stored_result
        )
        if _technical_failure(result):
            if run.reason == "recovery":
                self._store.set_claim_lifecycle(
                    run.claim_id,
                    "settled",
                    {"verdict": "infra-error", "failed_unit": run.unit_key},
                )
                self._store.record_event(
                    run.claim_id,
                    f"{run.unit_key}:attempt-{run.attempt_number}:exhausted",
                    f"{run.unit_key} exhausted its technical recovery attempt; "
                    "later repetitions are unstarted.",
                )
            else:
                self._store.set_claim_lifecycle(run.claim_id, "waiting", {"verdict": "infra-error"})
                self._store.record_event(
                    run.claim_id,
                    f"{run.unit_key}:attempt-{run.attempt_number}:retry",
                    f"{run.unit_key} failed technically and will use its one recovery attempt.",
                )
            return
        self._store.record_event(
            run.claim_id,
            f"{run.unit_key}:attempt-{run.attempt_number}:complete",
            _completion_message(run.unit_key, stored_result),
        )
        self._settle_if_complete(self._required_claim(run.claim_id))

    def presentation(self, claim_id: str) -> ClaimPresentation:
        claim = self._required_claim(claim_id)
        if claim.lifecycle == "cancelled":
            return ClaimPresentation("Done", None, ("cancelled",))
        verdict = claim.outcome.get("verdict")
        if claim.lifecycle == "settled":
            return ClaimPresentation("Review", verdict if isinstance(verdict, str) else None, ())
        if claim.lifecycle == "waiting":
            return ClaimPresentation(
                "Ready", verdict if isinstance(verdict, str) else "infra-error", ()
            )
        return ClaimPresentation("Running", None, ())

    def cancel(self, claim_id: str) -> None:
        claim = self._required_claim(claim_id)
        for run in self._store.runs_for_claim(claim.id):
            if run.status in {"reserved", "running", "observing"}:
                self._store.request_cancellation(run.id)
        self._store.set_claim_lifecycle(claim.id, "cancelled", {})
        self._store.record_event(
            claim.id, "cancelled", "Issue closed; execution cancelled and evidence retained."
        )

    def deliver_reports(self, claim_id: str) -> None:
        claim = self._required_claim(claim_id)
        comments = self._github.list_comment_records(claim.repository, claim.issue_number)
        for event in self._store.pending_events(claim.id):
            marker = _marker(claim.id, event.key)
            existing = next(
                (
                    comment
                    for comment in comments
                    if comment.author == self._factory_login and marker in comment.body
                ),
                None,
            )
            if existing is not None:
                self._store.acknowledge_event(claim.id, event.key, existing.id)
                continue
            try:
                comment_id = self._github.create_comment(
                    claim.repository, claim.issue_number, f"{marker}\n{event.body}"
                )
            except GitHubApiError as error:
                # The next cycle searches all pages before considering a retry.
                self._store.record_delivery_failure(claim.id, event.key, error)
                continue
            self._store.acknowledge_event(claim.id, event.key, comment_id or "acknowledged")

    def _eligible(self, snapshot: RequestSnapshot) -> bool:
        return (
            not snapshot.closed
            and snapshot.owner == "factory"
            and snapshot.status == "Ready"
            and snapshot.issue_type == "Eval"
            and snapshot.author_permission in _WRITER_PERMISSIONS
        )

    def _invalid_feedback(self, snapshot: RequestSnapshot, explanation: str) -> None:
        key = f"{snapshot.repository}:{snapshot.issue_number}"
        fingerprint = hashlib.sha256(f"{snapshot.body}\0{explanation}".encode()).hexdigest()
        receipt = self._store.get_setting("invalid-input", key)
        if receipt is not None and receipt.get("fingerprint") == fingerprint:
            return
        marker = f"<!-- agent-factory:needs-input:{fingerprint} -->"
        comment_id = self._github.create_comment(
            snapshot.repository,
            snapshot.issue_number,
            f"{marker}\nneeds-input: {explanation}",
        )
        self._store.set_setting(
            "invalid-input",
            key,
            {"fingerprint": fingerprint, "comment_id": comment_id or "acknowledged"},
        )

    def _next_unit(self, claim: Claim, repetitions: int) -> tuple[str | None, str]:
        runs = self._store.runs_for_claim(claim.id)
        for number in range(1, repetitions + 1):
            key = f"rep-{number}"
            unit_runs = [run for run in runs if run.unit_key == key]
            if not unit_runs:
                return key, "initial"
            latest = unit_runs[-1]
            if latest.status in {"reserved", "running", "observing"}:
                return None, "initial"
            if latest.status == "deferred":
                return key, "quota"
            if _run_needs_recovery(latest) and latest.reason != "recovery":
                return key, "recovery"
        return None, "initial"

    def _settle_if_complete(self, claim: Claim) -> None:
        if claim.lifecycle == "settled":
            return
        runs = self._store.runs_for_claim(claim.id)
        repetitions = _repetitions(claim)
        settled: list[Run] = []
        for number in range(1, repetitions + 1):
            unit_runs = [run for run in runs if run.unit_key == f"rep-{number}"]
            if not unit_runs:
                return
            latest = unit_runs[-1]
            if latest.status not in {"completed", "failed"}:
                return
            if _run_needs_recovery(latest):
                return
            settled.append(latest)
        failed = any(_product_failed(run) or _nonresumable_workflow(run) for run in settled)
        verdict = "failed" if failed else "pending-human-review"
        self._store.set_claim_lifecycle(claim.id, "settled", {"verdict": verdict})
        self._store.record_event(
            claim.id,
            "handoff",
            f"All repetitions settled; aggregate verdict is {verdict}.",
        )

    def _is_active(self, claim: Claim) -> bool:
        return any(
            run.status in {"reserved", "running", "observing"}
            for run in self._store.runs_for_claim(claim.id)
        )

    def _required_claim(self, claim_id: str) -> Claim:
        claim = self._store.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        return claim

    def _required_run(self, run_id: str) -> Run:
        run = self._store.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run


def _marker(claim_id: str, event_key: str) -> str:
    return f"<!-- agent-factory:event:{claim_id}:{event_key} -->"


def _repetitions(claim: Claim) -> int:
    settings_raw = claim.frozen_spec.get("settings")
    if not isinstance(settings_raw, Mapping):
        raise RuntimeError("claim has no frozen eval settings")
    settings = cast(Mapping[str, object], settings_raw)
    repetitions = settings.get("repetitions")
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise RuntimeError("claim has invalid frozen repetitions")
    return repetitions


def _hold_active(hold: Mapping[str, object], now: datetime) -> bool:
    value = hold.get("until")
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value) > now
    except ValueError:
        return True


def _technical_failure(result: AttemptResult) -> bool:
    if result.execution_status not in {"failed", "interrupted"}:
        return False
    resumable = result.resumable
    if resumable is None:
        reported = result.result.get("resumable")
        resumable = reported if isinstance(reported, bool) else None
    return not _is_nonresumable_workflow(result.result, resumable)


def _run_needs_recovery(run: Run) -> bool:
    if run.status not in {"failed", "interrupted"}:
        return False
    resumable = run.result.get("resumable")
    return not _is_nonresumable_workflow(
        run.result, resumable if isinstance(resumable, bool) else None
    )


def _product_failed(run: Run) -> bool:
    return run.result.get("product_verdict") == "failed"


def _nonresumable_workflow(run: Run) -> bool:
    resumable = run.result.get("resumable")
    return _is_nonresumable_workflow(run.result, resumable if isinstance(resumable, bool) else None)


def _is_nonresumable_workflow(result: Mapping[str, object], resumable: bool | None) -> bool:
    failure_raw = result.get("failure")
    if not isinstance(failure_raw, Mapping):
        return False
    failure = cast(Mapping[str, object], failure_raw)
    return failure.get("owner") == "workflow" and resumable is False


def _completion_message(unit_key: str, result: Mapping[str, object]) -> str:
    verdict = result.get("product_verdict", "unavailable")
    score = result.get("score", "unavailable")
    cost = result.get("cost", "unavailable")
    return f"{unit_key} settled: verdict={verdict}; score={score}; cost={cost}."
