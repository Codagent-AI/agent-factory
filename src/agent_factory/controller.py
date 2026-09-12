"""Policy-only claim controller.

The controller is intentionally ignorant of suite command details.  A caller
supplies pinned revisions and executes reserved runs; this module persists the
admission decision, normalized observations, and report delivery state.
"""

from __future__ import annotations

import fcntl
import hashlib
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from agent_factory.github import GitHubApiError, IssueComment
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.work_kinds.base import Classification, Feedback, WorkKindHandler


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
    labels: Mapping[str, bool] = field(default_factory=lambda: dict[str, bool]())


class ReportingClient(Protocol):
    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]: ...

    def create_comment(self, repository: str, number: int, body: str) -> str | None: ...


class Controller:
    """Serializes admission while allowing supervisors to own running attempts."""

    def __init__(
        self,
        store: ClaimStore,
        github: ReportingClient,
        handlers: Mapping[str, WorkKindHandler],
        *,
        factory_login: str = "codagent-factory[bot]",
        artifact_root: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._github = github
        self._handlers = dict(handlers)
        self._factory_login = factory_login
        self._artifact_root = (
            artifact_root or (Path.home() / ".agent-factory" / "artifacts")
        ).resolve()
        self._now = now or (lambda: datetime.now(UTC))
        for handler in self._handlers.values():
            attach = getattr(handler, "attach_store", None)
            if callable(attach):
                attach(store)

    def handler(self, kind: str) -> WorkKindHandler | None:
        return self._handlers.get(kind)

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
        resolve: Callable[[object], tuple[str, str]],
        fresh: bool = False,
    ) -> Claim | None:
        """Validate and freeze a new request, never creating a claim for bad input."""
        handler = self._handler_for_snapshot(snapshot)
        if handler is None:
            return None
        fingerprint = handler.request_fingerprint(snapshot)
        if isinstance(fingerprint, Feedback):
            self._invalid_feedback(snapshot, fingerprint.explanation)
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
        if current is not None and current.request_fingerprint == fingerprint and not fresh:
            return current
        accepted = handler.accept(snapshot, self._store, resolve)
        if isinstance(accepted, Feedback):
            self._invalid_feedback(snapshot, accepted.explanation)
            return None
        claim = (
            self._store.create_claim(accepted)
            if current is None
            else self._store.supersede_and_create(current.id, accepted)
        )
        self._store.set_claim_lifecycle(claim.id, "active", {})
        self._store.record_event(claim.id, "accepted", "Evaluation inputs accepted and frozen.")
        return cast(Claim, self._store.get_claim(claim.id))

    def reserve_next(self, claim_id: str, *, readiness: Callable[[], str | None]) -> Run | None:
        """Reserve exactly one ready work unit after all launch-time controls pass."""
        with advisory_lock(self._store.path, "admission"):
            claim = self._required_claim(claim_id)
            handler = self._required_handler(claim.kind)
            if claim.lifecycle in {"settled", "cancelled", "superseded"} or self.paused():
                return None
            issue = readiness()
            if issue is not None:
                self._store.set_hold(claim.id, "readiness", {"reason": issue})
                self._store.record_event(claim.id, f"readiness:{issue}", f"Waiting: {issue}")
                return None
            global_quota = self._store.get_setting("admission", "quota")
            if global_quota is not None and _hold_active(global_quota, self._now()):
                return None
            quota = self._store.get_hold(claim.id, "quota")
            if quota is not None and _hold_active(quota, self._now()):
                return None
            next_unit, reason = handler.next_unit(claim, self._store.runs_for_claim(claim.id))
            if next_unit is None:
                self._settle_if_complete(claim)
                return None
            run = self._store.reserve_run(
                claim.id,
                next_unit,
                reason=reason,
                evidence_path=str(self._artifact_root / f"{claim.id}-{next_unit}"),
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
        claim = self._required_claim(run.claim_id)
        handler = self._required_handler(claim.kind)
        stored_result = dict(result.result)
        stored_result["execution_status"] = result.execution_status
        stored_result.setdefault("artifact_path", run.evidence_path)
        if result.product_verdict is not None:
            stored_result["product_verdict"] = result.product_verdict
        if result.resumable is not None:
            stored_result["resumable"] = result.resumable
        persist = (
            self._store.finish_run
            if run.status in NONTERMINAL_RUN_STATUSES
            else self._store.normalize_terminal_result
        )
        classification = handler.classify(run, result)
        if classification.kind == "quota" or result.quota_until is not None:
            deadline = result.quota_until
            if deadline is not None:
                stored_result["quota_until"] = deadline.isoformat()
                persist(run.id, execution_status="deferred", result=stored_result)
                self._store.set_hold(run.claim_id, "quota", {"until": deadline.isoformat()})
                self._store.set_setting("admission", "quota", {"until": deadline.isoformat()})
                self._store.set_claim_lifecycle(
                    run.claim_id, "waiting", {"verdict": "quota-deferred"}
                )
                self._store.record_event(
                    run.claim_id,
                    f"{run.unit_key}:attempt-{run.attempt_number}:quota",
                    f"{run.unit_key} is waiting for usage reset at {deadline.isoformat()}.",
                )
                return
            # A handler classified this as quota without a reset deadline, which is
            # invalid output; fail closed as a technical error instead of losing the run.
            classification = Classification("technical")
        persist(run.id, execution_status=result.execution_status, result=stored_result)
        message = _attempt_message(handler, run, stored_result, classification.kind)
        if classification.kind == "technical":
            if run.reason == "recovery":
                self._store.set_claim_lifecycle(
                    run.claim_id,
                    "settled",
                    {"verdict": "infra-error", "failed_unit": run.unit_key},
                )
                self._store.record_event(
                    run.claim_id,
                    f"{run.unit_key}:attempt-{run.attempt_number}:exhausted",
                    message,
                )
            else:
                self._store.set_claim_lifecycle(run.claim_id, "waiting", {"verdict": "infra-error"})
                self._store.record_event(
                    run.claim_id,
                    f"{run.unit_key}:attempt-{run.attempt_number}:retry",
                    message,
                )
            return
        self._store.record_event(
            run.claim_id,
            f"{run.unit_key}:attempt-{run.attempt_number}:complete",
            message,
        )
        self._settle_if_complete(self._required_claim(run.claim_id))

    def presentation(self, claim_id: str) -> ClaimPresentation:
        claim = self._required_claim(claim_id)
        if claim.lifecycle == "cancelled":
            return ClaimPresentation("Done", None, ("cancelled",))
        return self._required_handler(claim.kind).presentation(claim)

    def cancel(self, claim_id: str) -> None:
        claim = self._required_claim(claim_id)
        for run in self._store.runs_for_claim(claim.id):
            if run.status in NONTERMINAL_RUN_STATUSES:
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

    def report_request_readiness(self, snapshot: RequestSnapshot, reason: str) -> None:
        """Persist pre-claim failures and deliver corrective feedback without accepting inputs."""
        key = f"{snapshot.repository}:{snapshot.issue_number}"
        receipt = self._store.get_setting("request-readiness", key)
        if receipt and receipt.get("reason") == reason and receipt.get("comment_id"):
            return
        self._store.set_setting("request-readiness", key, {"reason": reason})
        digest = hashlib.sha256(reason.encode()).hexdigest()
        marker = f"<!-- agent-factory:request-readiness:{digest} -->"
        existing = next(
            (
                comment
                for comment in self._github.list_comment_records(
                    snapshot.repository, snapshot.issue_number
                )
                if comment.author == self._factory_login and marker in comment.body
            ),
            None,
        )
        comment_id = (
            existing.id
            if existing
            else self._github.create_comment(
                snapshot.repository,
                snapshot.issue_number,
                f"{marker}\nWaiting for revision readiness: {reason}",
            )
        )
        self._store.set_setting(
            "request-readiness", key, {"reason": reason, "comment_id": comment_id or "acknowledged"}
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

    def _settle_if_complete(self, claim: Claim) -> None:
        handler = self._required_handler(claim.kind)
        outcome = handler.settle(claim, self._store.runs_for_claim(claim.id))
        if outcome is None:
            return
        self._store.set_claim_lifecycle(claim.id, "settled", {"verdict": outcome.verdict})
        self._store.record_event(claim.id, outcome.event_key, outcome.event_body)

    def _handler_for_snapshot(self, snapshot: RequestSnapshot) -> WorkKindHandler | None:
        for handler in self._handlers.values():
            if handler.handles(snapshot):
                return handler
        return None

    def _required_handler(self, kind: str) -> WorkKindHandler:
        handler = self._handlers.get(kind)
        if handler is None:
            raise KeyError(kind)
        return handler

    def _is_active(self, claim: Claim) -> bool:
        return any(
            run.status in NONTERMINAL_RUN_STATUSES for run in self._store.runs_for_claim(claim.id)
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


def quota_deadline(hold: Mapping[str, object]) -> datetime:
    """Require a usable reset time; invalid saved holds never authorize admission."""
    value = hold.get("until")
    if not isinstance(value, str):
        raise ValueError("quota hold is missing a string reset timestamp")
    try:
        deadline = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("quota hold has an invalid reset timestamp") from error
    if deadline.utcoffset() is None:
        raise ValueError("quota hold reset timestamp must include a timezone")
    return deadline


def _hold_active(hold: Mapping[str, object], now: datetime) -> bool:
    try:
        return quota_deadline(hold) > now
    except ValueError:
        return True


def _attempt_message(
    handler: WorkKindHandler, run: Run, stored_result: Mapping[str, object], kind: str
) -> str:
    stage = "complete"
    if kind == "technical":
        stage = "exhausted" if run.reason == "recovery" else "retry"
    describe = getattr(handler, "attempt_message", None)
    if callable(describe):
        return cast(str, describe(run, stored_result, stage=stage))
    return f"{run.unit_key} settled."


@contextmanager
def advisory_lock(state: Path, name: str) -> Generator[None, None, None]:
    """Serialize controller entry points across processes sharing this database."""
    directory = state.resolve().parent / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
