"""Eval work-kind handler wrapping parse, freeze, suite execution, and presentation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, ScheduleConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    ClaimPresentation,
    ExecutionPlan,
    RequestSnapshot,
)
from agent_factory.github import GitHubClient, IssueComment, ProjectQueueItem
from agent_factory.operations import Diagnostic, model_authentication
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimDraft, ClaimStore, Run
from agent_factory.suites.and_scene import (
    AndSceneAdapter,
    GitWorktreeManager,
    ReadinessError,
    SourceRepositories,
    WorktreeCleanup,
)
from agent_factory.supervisor import SupervisionLimits
from agent_factory.work_kinds.base import (
    Classification,
    Feedback,
    Gesture,
    Outcome,
    Preparation,
    ReportEvent,
)
from agent_factory.work_kinds.eval import EvalDefaults, ParsedRequest, parse_request

_WRITER_PERMISSIONS = frozenset({"write", "maintain", "admin"})


class EvalHandler:
    """Owns every eval-shaped decision the generic controller used to make inline."""

    kind = "eval"

    def __init__(
        self,
        defaults: EvalDefaults,
        *,
        harness_sha: str,
        suite: str = "and-scene",
        shared: SharedConfig | None = None,
        local: LocalConfig | None = None,
        sources: SourceRepositories | None = None,
        adapter: AndSceneAdapter | None = None,
        manager: GitWorktreeManager | None = None,
        fallback_seconds: int = 18000,
    ) -> None:
        self._defaults = defaults
        self._harness_sha = harness_sha
        self._suite = suite
        self._shared = shared
        self._local = local
        self.sources = sources
        self.adapter = adapter
        self._manager = manager
        self._fallback_seconds = fallback_seconds
        self._store: ClaimStore | None = None
        self._worktree_cleanup: WorktreeCleanup | None = None

    @classmethod
    def from_config(cls, shared: SharedConfig, local: LocalConfig) -> EvalHandler:
        values = shared.eval.defaults
        defaults = EvalDefaults(
            str(values.get("agent_runner_ref", "main")),
            str(values.get("agent_skills_ref", "main")),
            {role: str(values.get(role, "")) for role in ("lead", "implementor", "tester")},
            bool(values.get("skip_validator", False)),
            shared.eval.repetitions,
        )
        sources = SourceRepositories(
            local.repositories.agent_runner,
            local.repositories.agent_skills,
            local.repositories.agent_evals,
        )
        return cls(
            defaults,
            harness_sha=shared.eval.harness_sha,
            suite=shared.eval.suite,
            shared=shared,
            local=local,
            sources=sources,
            adapter=AndSceneAdapter(environment_file=local.credentials.suite_environment),
            manager=GitWorktreeManager(local.storage_root, sources),
            fallback_seconds=local.limits.codex_reset_fallback_seconds,
        )

    def attach_store(self, store: ClaimStore) -> None:
        self._store = store
        if self._manager is not None:
            self._worktree_cleanup = WorktreeCleanup(store, self._manager)

    def handles(self, snapshot: RequestSnapshot) -> bool:
        return (
            not snapshot.closed
            and snapshot.owner == "factory"
            and snapshot.status == "Ready"
            and snapshot.issue_type == "Eval"
            and snapshot.author_permission in _WRITER_PERMISSIONS
        )

    def snapshot(
        self, card: ProjectQueueItem, client: object, shared: SharedConfig
    ) -> RequestSnapshot | None:
        source = card.source
        if (
            source.repository != shared.routing.eval_source
            or source.state.lower() == "closed"
            or source.issue_type != shared.routing.eval_type
            or shared.routing.eval_label not in source.labels
            or card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
            or _card_status(shared, card) != "Ready"
        ):
            return None
        github = cast(GitHubClient, client)
        permission = github.get_permission(source.repository, source.author)
        if permission not in _WRITER_PERMISSIONS:
            return None
        verdict = next(
            (
                key
                for key, value in shared.project.verdict.options.items()
                if card.fields.get(shared.project.verdict.id) == value
            ),
            None,
        )
        return RequestSnapshot(
            source.repository,
            source.number,
            source.id,
            card.id,
            source.author,
            permission,
            "Eval",
            source.labels,
            "Ready",
            "factory",
            verdict,
            source.body,
            False,
        )

    def request_fingerprint(self, snapshot: RequestSnapshot) -> str | Feedback:
        try:
            return parse_request(snapshot.body, self._defaults).fingerprint
        except ValueError as error:
            return Feedback(str(error))

    def accept(
        self,
        snapshot: RequestSnapshot,
        store: ClaimStore,
        resolve: object,
    ) -> ClaimDraft | Feedback:
        try:
            request = parse_request(snapshot.body, self._defaults)
        except ValueError as error:
            return Feedback(str(error))
        resolver = cast(Callable[[ParsedRequest], tuple[str, str]], resolve)
        runner_sha, skills_sha = resolver(request)
        frozen = request.freeze(
            runner_sha=runner_sha,
            skills_sha=skills_sha,
            harness_sha=self._harness_sha,
            suite=self._suite,
        )
        return ClaimDraft(
            snapshot.repository,
            snapshot.issue_number,
            snapshot.issue_id,
            snapshot.project_item_id,
            self.kind,
            request.fingerprint,
            frozen.payload,
        )

    def readiness(self, local: LocalConfig, shared: SharedConfig) -> list[Diagnostic]:
        return []

    def prepare(self, claim: Claim) -> Preparation:
        if self._manager is None or self.adapter is None:
            raise ReadinessError("eval handler is missing worktree sources")
        worktrees = self._manager.prepare(claim.id, _mapping(claim.frozen_spec.get("revisions")))
        if not claim.preparation and self._worktree_cleanup is not None:
            self._worktree_cleanup.record(claim.id, worktrees)
        roles = _mapping(_mapping(claim.frozen_spec.get("settings")).get("roles"))
        auth = model_authentication({key: str(value) for key, value in roles.items()})
        failures = [check.detail for check in auth if not check.available]
        if failures:
            raise ReadinessError("; ".join(failures))
        reason = self.adapter.readiness(worktrees)
        if reason:
            raise ReadinessError(reason)
        return Preparation(worktrees=worktrees)

    def next_unit(self, claim: Claim, runs: Sequence[Run]) -> tuple[str | None, str]:
        count = _unit_count(claim)
        for number in range(1, count + 1):
            key = f"rep-{number}"
            unit_runs = [run for run in runs if run.unit_key == key]
            if not unit_runs:
                return key, "initial"
            latest = unit_runs[-1]
            if latest.status in NONTERMINAL_RUN_STATUSES:
                return None, "initial"
            if latest.status == "deferred":
                return key, "quota"
            if _run_needs_recovery(latest) and latest.reason != "recovery":
                return key, "recovery"
        return None, "initial"

    def plan(self, claim: Claim, run: Run, preparation: Preparation) -> ExecutionPlan:
        from agent_factory import runtime

        if self._store is None or self.adapter is None or preparation.worktrees is None:
            raise ReadinessError("eval execution plan is missing worktrees")
        return runtime._plan_attempt(  # pyright: ignore[reportPrivateUsage]
            self._store, self.adapter, claim, run, preparation.worktrees
        )

    def read_result(self, run: Run) -> AttemptResult:
        if self.adapter is None:
            raise ReadinessError("eval handler is missing the suite adapter")
        result = AttemptResult(
            "interrupted" if run.status == "timed_out" else run.status, None, run.result
        )
        if (Path(run.evidence_path) / "result.json").exists() and run.status != "timed_out":
            result = self.adapter.read_result(Path(run.evidence_path))
        return result

    def classify(self, run: Run, result: AttemptResult) -> Classification:
        if result.quota_until is not None:
            return Classification("quota")
        if _technical_failure(result):
            return Classification("technical")
        return Classification("settled")

    def settle(self, claim: Claim, runs: Sequence[Run]) -> Outcome | None:
        if claim.lifecycle == "settled":
            return None
        count = _unit_count(claim)
        settled: list[Run] = []
        for number in range(1, count + 1):
            unit_runs = [run for run in runs if run.unit_key == f"rep-{number}"]
            if not unit_runs:
                return None
            latest = unit_runs[-1]
            if latest.status not in {"completed", "failed"}:
                return None
            if _run_needs_recovery(latest):
                return None
            settled.append(latest)
        failed = any(_product_failed(run) or _nonresumable_workflow(run) for run in settled)
        verdict = "failed" if failed else "pending-human-review"
        return Outcome(
            verdict,
            event_body=f"All repetitions settled; aggregate verdict is {verdict}.",
        )

    def presentation(self, claim: Claim) -> ClaimPresentation:
        if claim.lifecycle == "cancelled":
            return ClaimPresentation("Done", None, ("cancelled",))
        verdict = claim.outcome.get("verdict")
        if claim.lifecycle == "settled":
            return ClaimPresentation("Review", verdict if isinstance(verdict, str) else None, ())
        idle = True
        if self._store is not None:
            idle = not any(
                run.status in NONTERMINAL_RUN_STATUSES
                for run in self._store.runs_for_claim(claim.id)
            )
        if claim.lifecycle == "waiting":
            return ClaimPresentation(
                "Ready", verdict if isinstance(verdict, str) else "infra-error", ()
            )
        if idle:
            return ClaimPresentation("Ready", None, ())
        return ClaimPresentation("Running", None, ())

    def report_events(self, claim: Claim, run: Run, result: AttemptResult) -> list[ReportEvent]:
        if self.adapter is None:
            return []
        paths = _mapping(claim.preparation.get("worktrees", {}))
        evals = _mapping(paths.get("evals", {})).get("path")
        if not isinstance(evals, str):
            return []
        handoff = self.adapter.review_handoff(
            result.result,
            Path(evals) / "evals/agent-runner/and-scene/human-review.sh",
            Path(run.evidence_path),
        )
        if not handoff:
            return []
        return [ReportEvent(f"{run.unit_key}:review-command", handoff)]

    def gesture(
        self, claim: Claim, card: ProjectQueueItem, comments: Sequence[IssueComment]
    ) -> Gesture | None:
        del comments
        if self._shared is None or self._store is None:
            return None
        shared = self._shared
        if _card_status(shared, card) != "Ready":
            return None
        if card.fields.get(shared.project.verdict.id) is not None:
            return None
        if not self._store.get_setting("field-delivery", f"{claim.id}:{shared.project.verdict.id}"):
            return None
        return "fresh"

    def limits(self, local: LocalConfig) -> SupervisionLimits:
        return SupervisionLimits(
            local.limits.inactivity_seconds,
            local.limits.execution_seconds,
            local.limits.total_seconds,
        )

    def window(self, local: LocalConfig) -> ScheduleConfig:
        return local.schedule

    def providers(self, claim: Claim) -> set[str]:
        roles = _mapping(_mapping(claim.frozen_spec.get("settings")).get("roles"))
        providers: set[str] = set()
        for value in roles.values():
            if isinstance(value, str) and value:
                providers.add(value.split(":", 1)[0])
        return providers

    def cleanup(self, claim: Claim, *, board_status: str = "") -> None:
        if self._worktree_cleanup is not None:
            self._worktree_cleanup.reconcile(claim.id, board_status=board_status)

    def attempt_message(self, run: Run, stored_result: Mapping[str, object], *, stage: str) -> str:
        body = _completion_message(run.unit_key, stored_result)
        if stage == "exhausted":
            return (
                f"{run.unit_key} exhausted its technical recovery attempt; "
                "later repetitions are unstarted.\n\n" + body
            )
        if stage == "retry":
            return (
                f"{run.unit_key} failed technically and will use its one recovery attempt.\n\n"
                + body
            )
        return body

    def refs_text(self, claim: Claim) -> str | None:
        revisions = _mapping(claim.frozen_spec.get("revisions", {}))
        invalid = [
            key
            for key in ("runner", "skills", "evals")
            if not isinstance(revisions.get(key), str) or not revisions.get(key)
        ]
        if invalid:
            if self._store is not None:
                self._store.record_event(
                    claim.id,
                    "invalid-revisions",
                    "Cannot report frozen revisions: missing or invalid "
                    + ", ".join(invalid)
                    + ". Repair the saved claim inputs.",
                )
            return None
        return " ".join(f"{key}@{str(revisions[key])[:7]}" for key in ("runner", "skills", "evals"))

    def frozen_inputs_event(self, claim: Claim) -> str:
        return (
            "Frozen evaluation inputs:\n```json\n"
            + json.dumps(claim.frozen_spec, indent=2)
            + "\n```"
        )


def _card_status(shared: SharedConfig, card: ProjectQueueItem) -> str:
    value = card.fields.get(shared.project.status.id)
    return next(
        (key.title() for key, option in shared.project.status.options.items() if value == option),
        "",
    )


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _unit_count(claim: Claim) -> int:
    settings_raw = claim.frozen_spec.get("settings")
    if not isinstance(settings_raw, Mapping):
        raise RuntimeError("claim has no frozen eval settings")
    settings = cast(Mapping[str, object], settings_raw)
    count = settings.get("repetitions")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise RuntimeError("claim has invalid frozen repetitions")
    return count


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
    return failure.get("owner") in {"workflow", "implementation-workflow"} and resumable is False


def _public_diagnostic(value: str) -> str:
    value = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[redacted]@", value)
    value = re.sub(r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)\b(?:Proxy-)?Authorization\s*:\s*[^\r\n]*",
        "Authorization: [redacted]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
    return re.sub(
        r"(?i)(\b[\w-]*(?:token|secret|password|api[_-]?key)[\w-]*[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]",
        value,
    )


def _completion_message(unit_key: str, result: Mapping[str, object]) -> str:
    def text(value: object) -> str:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return "unavailable"
        return _public_diagnostic(str(value)).replace("\n", " ")[:500] or "unavailable"

    lines = [
        f"{unit_key} settled.",
        f"Execution: {text(result.get('execution_status'))}",
        f"Product verdict: {text(result.get('product_verdict'))}",
    ]
    raw = result.get("report_summary")
    summary = (
        cast(Mapping[str, object], raw)
        if isinstance(raw, Mapping)
        else {
            "Automated score": result.get("score"),
            "Cost": result.get("cost"),
            "Artifacts": result.get("artifact_path"),
        }
    )
    lines.extend(f"{key}: {text(value)}" for key, value in summary.items())
    failure = result.get("failure")
    if isinstance(failure, Mapping):
        details = cast(Mapping[str, object], failure)
        lines.append(
            "Failure: "
            + "; ".join(
                f"{key}={text(details[key])}"
                for key in ("owner", "code", "phase", "reason", "message")
                if key in details
            )
        )
    for key in ("failed_phase", "reason", "error", "timeout"):
        if result.get(key) is not None:
            lines.append(f"{key}: {text(result[key])}")
    return lines[0] + "\n\n" + "\n".join(f"- {line}" for line in lines[1:])
