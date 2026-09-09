"""Compose one configured poll from the existing controller and suite components."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from contextlib import closing, suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    Controller,
    ExecutionPlan,
    RequestSnapshot,
    advisory_lock,
)
from agent_factory.github import (
    AppCredentials,
    GitHubClient,
    InstallationTokenProvider,
    ProjectQueueItem,
    SubprocessGhRunner,
)
from agent_factory.operations import doctor, model_authentication
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.suites.and_scene import (
    AndSceneAdapter,
    GitWorktreeManager,
    PreparedWorktrees,
    ReadinessError,
    RecoveryStateError,
    SourceRepositories,
    WorktreeCleanup,
    WorktreeError,
)
from agent_factory.supervisor import SupervisionLimits, launch_supervisor
from agent_factory.work_kinds.eval import EvalDefaults, ParsedRequest, parse_request


def cycle(state: Path, config_path: Path) -> None:
    local = LocalConfig.from_file(config_path)
    shared = SharedConfig.from_file(local.shared_config)
    if not shared.bot_login:
        raise ValueError("shared github.bot_login is required for report reconciliation")
    runner = SubprocessGhRunner()
    client = GitHubClient(
        runner,
        InstallationTokenProvider(
            AppCredentials(shared.app_id, shared.installation_id, local.credentials.github_app_key),
            runner,
        ),
    )
    sources = SourceRepositories(
        local.repositories.agent_runner,
        local.repositories.agent_skills,
        local.repositories.agent_evals,
    )
    manager = GitWorktreeManager(local.storage_root, sources)
    adapter = AndSceneAdapter(environment_file=local.credentials.suite_environment)
    with advisory_lock(state, "cycle"), closing(ClaimStore(state)) as store:
        controller = Controller(
            store,
            client,
            eval_defaults(shared),
            harness_sha=shared.eval.harness_sha,
            suite=shared.eval.suite,
            factory_login=shared.bot_login,
            artifact_root=local.storage_root / "artifacts",
        )
        cleanup = WorktreeCleanup(store, manager)
        client.validate_project(shared.project)
        cards = client.list_project_items(shared.project.id)
        _consume_results(
            store,
            controller,
            adapter,
            fallback_seconds=local.limits.codex_reset_fallback_seconds,
        )
        for card in cards:
            claims = store.claims_for_item(card.id)
            if not claims:
                _repair_unclaimed(store, client, shared, card)
            for claim in claims:
                if claim.lifecycle == "superseded":
                    continue
                if card.source.state.lower() == "closed" and claim.lifecycle != "settled":
                    controller.cancel(claim.id)
                _report(store, controller, client, shared, card, claim.id)
                cleanup.reconcile(claim.id, board_status=_logical_status(shared, card))
        # Feedback and reconciliation also work while paused or outside the window.
        now = datetime.now(local.schedule.timezone)
        ready = (
            not store.is_paused()
            and local.schedule.allows_admission(now)
            and not store.nonterminal_runs()
        )
        quota = store.get_setting("admission", "quota")
        if quota is not None:
            until = datetime.fromisoformat(str(quota["until"]))
            ready = ready and datetime.now(UTC) >= until
        prerequisites: str | None = None
        for card in cards:
            snapshot = _snapshot(client, shared, card)
            if snapshot is None:
                continue
            try:
                parse_request(snapshot.body, eval_defaults(shared))
            except ValueError:
                # Existing controller supplies durable corrective comment feedback.
                controller.accept(snapshot, resolve=lambda _: ("", ""))
                client.set_attention_label(snapshot.repository, snapshot.issue_number, True)
                continue
            client.set_attention_label(snapshot.repository, snapshot.issue_number, False)
            if not ready:
                continue
            if prerequisites is None:
                failures = [d for d in doctor(local) if not d.available]
                prerequisites = "; ".join(f"{d.name}: {d.detail}" for d in failures)
            if prerequisites:
                store.set_setting("runtime", "readiness", {"reason": prerequisites})
                break
            store.set_setting("runtime", "readiness", {})
            claim = controller.accept(
                snapshot,
                resolve=lambda request: _resolve(sources, request),
                fresh=_fresh_requested(store, shared, snapshot),
            )
            if claim is None or claim.lifecycle in {"settled", "cancelled", "superseded"}:
                continue
            try:
                worktrees = _prepare_claim_worktrees(claim, manager, cleanup, adapter)
                run = controller.reserve_next(claim.id, readiness=lambda: None)
                if run is None:
                    continue
                try:
                    plan = _plan_attempt(store, adapter, claim, run, worktrees)
                    launch_supervisor(
                        state,
                        run.id,
                        plan,
                        SupervisionLimits(
                            local.limits.inactivity_seconds,
                            local.limits.execution_seconds,
                            local.limits.total_seconds,
                        ),
                        config_path=config_path,
                    )
                except (OSError, ReadinessError, RecoveryStateError) as error:
                    controller.record_result(
                        run.id, AttemptResult("failed", None, {"reason": str(error)})
                    )
                _report(store, controller, client, shared, card, claim.id)
                break
            except (WorktreeError, ReadinessError) as error:
                store.set_hold(claim.id, "readiness", {"reason": str(error)})
                store.set_claim_lifecycle(claim.id, "waiting", {"verdict": "infra-error"})
                store.record_event(claim.id, f"readiness:{error}", f"Waiting: {error}")
                _report(store, controller, client, shared, card, claim.id)


def _prepare_claim_worktrees(
    claim: Claim, manager: GitWorktreeManager, cleanup: WorktreeCleanup, adapter: AndSceneAdapter
) -> PreparedWorktrees:
    worktrees = manager.prepare(claim.id, _mapping(claim.frozen_spec["revisions"]))
    if not claim.preparation:
        cleanup.record(claim.id, worktrees)
    roles = _mapping(_mapping(claim.frozen_spec.get("settings")).get("roles"))
    auth = model_authentication({key: str(value) for key, value in roles.items()})
    failures = [check.detail for check in auth if not check.available]
    if failures:
        raise ReadinessError("; ".join(failures))
    reason = adapter.readiness(worktrees)
    if reason:
        raise ReadinessError(reason)
    return worktrees


def _plan_attempt(
    store: ClaimStore,
    adapter: AndSceneAdapter,
    claim: Claim,
    run: Run,
    worktrees: PreparedWorktrees,
) -> ExecutionPlan:
    previous = store.runs_for_claim(claim.id)[:-1]
    stopped_before_checkpoint = bool(
        previous and previous[-1].result.get("reason") == "suite launch failed"
    )
    return adapter.plan(
        claim.frozen_spec,
        worktrees,
        Path(run.evidence_path),
        recovery=run.reason != "initial",
        pre_checkpoint_proven=stopped_before_checkpoint,
    )


def eval_defaults(shared: SharedConfig) -> EvalDefaults:
    values = shared.eval.defaults
    return EvalDefaults(
        str(values.get("agent_runner_ref", "main")),
        str(values.get("agent_skills_ref", "main")),
        {role: str(values.get(role, "")) for role in ("lead", "implementor", "reviewer")},
        bool(values.get("skip_validator", False)),
        shared.eval.repetitions,
    )


def _resolve(sources: SourceRepositories, request: ParsedRequest) -> tuple[str, str]:
    result: list[str] = []
    for source, key in ((sources.runner, "agent_runner_ref"), (sources.skills, "agent_skills_ref")):
        subprocess.run(
            ["git", "-C", str(source), "fetch", "--quiet", "--tags", "origin"],
            capture_output=True,
            check=False,
            timeout=60,
        )
        revision = str(request.settings[key])
        result.append(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(source),
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    revision + "^{commit}",
                ],
                text=True,
            ).strip()
        )
    return result[0], result[1]


def _repair_unclaimed(
    store: ClaimStore, client: GitHubClient, shared: SharedConfig, card: ProjectQueueItem
) -> None:
    if (
        card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
        or _logical_status(shared, card) != "Running"
        or card.source.state.lower() == "closed"
    ):
        return
    option = shared.project.status.option("ready")
    client.set_single_select_field(shared.project.id, card.id, shared.project.status.id, option)
    card.fields[shared.project.status.id] = option
    if not store.get_setting("status-repair", card.id):
        marker = f"<!-- agent-factory:status-repair:{card.id}:unclaimed -->"
        delivered = any(
            comment.author == shared.bot_login and marker in comment.body
            for comment in client.list_comment_records(card.source.repository, card.source.number)
        )
        if not delivered:
            client.create_comment(
                card.source.repository,
                card.source.number,
                marker + "\nStatus restored to Ready because no evaluation is running.",
            )
        store.set_setting("status-repair", card.id, {"complete": True})


def _logical_status(shared: SharedConfig, card: ProjectQueueItem) -> str:
    value = card.fields.get(shared.project.status.id)
    return next(
        (key.title() for key, option in shared.project.status.options.items() if value == option),
        "",
    )


def _snapshot(
    client: GitHubClient, shared: SharedConfig, card: ProjectQueueItem
) -> RequestSnapshot | None:
    source = card.source
    if (
        source.repository != shared.routing.eval_source
        or source.state.lower() == "closed"
        or source.issue_type != shared.routing.eval_type
        or shared.routing.eval_label not in source.labels
        or card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
        or _logical_status(shared, card) != "Ready"
    ):
        return None
    permission = client.get_permission(source.repository, source.author)
    if permission not in {"write", "maintain", "admin"}:
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


def _fresh_requested(store: ClaimStore, shared: SharedConfig, snapshot: RequestSnapshot) -> bool:
    if snapshot.verdict is not None:
        return False
    claims = store.claims_for_item(snapshot.project_item_id)
    return bool(
        claims
        and store.get_setting("field-delivery", f"{claims[-1].id}:{shared.project.verdict.id}")
    )


def _consume_results(
    store: ClaimStore,
    controller: Controller,
    adapter: AndSceneAdapter,
    *,
    fallback_seconds: int = 18000,
) -> None:
    for claim in store.all_claims():
        if claim.lifecycle in {"cancelled", "superseded"}:
            continue
        for run in store.runs_for_claim(claim.id):
            if run.status in NONTERMINAL_RUN_STATUSES or store.get_setting(
                "consumed-results", run.id
            ):
                continue
            result = AttemptResult(
                "interrupted" if run.status == "timed_out" else run.status, None, run.result
            )
            if (Path(run.evidence_path) / "result.json").exists() and run.status != "timed_out":
                with suppress(ReadinessError):
                    result = adapter.read_result(Path(run.evidence_path))
            if result.execution_status != "completed" and result.product_verdict not in {
                "failed",
                "fail",
            }:
                deadline = adapter.failure_quota_until(
                    Path(run.evidence_path), result.result, fallback_seconds=fallback_seconds
                )
                if deadline is not None:
                    result = replace(result, quota_until=deadline)
            observed = run.progress.get("container")
            if isinstance(observed, Mapping):
                result = replace(
                    result,
                    result={
                        **result.result,
                        "container": dict(cast(Mapping[str, object], observed)),
                    },
                )
            controller.record_result(run.id, result)
            handoff = _review_command(adapter, claim, run, result)
            if handoff:
                store.record_event(claim.id, f"{run.unit_key}:review-command", handoff)
            store.set_setting("consumed-results", run.id, {"complete": True})


def _review_command(
    adapter: AndSceneAdapter, claim: Claim, run: Run, result: AttemptResult
) -> str | None:
    paths = _mapping(claim.preparation.get("worktrees", {}))
    evals = _mapping(paths.get("evals", {})).get("path")
    if not isinstance(evals, str):
        return None
    return adapter.review_handoff(
        result.result,
        Path(evals) / "evals/agent-runner/and-scene/human-review.sh",
        Path(run.evidence_path),
    )


def _report(
    store: ClaimStore,
    controller: Controller,
    client: GitHubClient,
    shared: SharedConfig,
    card: ProjectQueueItem,
    claim_id: str,
) -> None:
    claim = store.get_claim(claim_id)
    if claim is None or card.fields.get(shared.project.owner.id) != shared.project.owner.option(
        "factory"
    ):
        return
    active = any(r.status in NONTERMINAL_RUN_STATUSES for r in store.runs_for_claim(claim_id))
    current = _logical_status(shared, card)
    desired = controller.presentation(claim_id)
    status = desired.status if active or claim.lifecycle in {"settled", "cancelled"} else "Ready"
    # A reviewed Done card releases worktrees; never bounce it back to Review.
    if not (current == "Done" and claim.lifecycle == "settled"):
        option = shared.project.status.option(status.lower())
        if card.fields.get(shared.project.status.id) != option:
            client.set_single_select_field(
                shared.project.id, card.id, shared.project.status.id, option
            )
            card.fields[shared.project.status.id] = option
            if active and status == "Running" and current in {"Ready", "Review", "Done"}:
                store.record_event(
                    claim_id,
                    f"status-repair:{current}:{len(store.runs_for_claim(claim_id))}",
                    "Status restored to Running because this evaluation is still active.",
                )
    if desired.verdict:
        field = shared.project.verdict.id
        receipt = store.get_setting("field-delivery", f"{claim_id}:{field}")
        if receipt is None or receipt.get("value") != desired.verdict:
            client.set_single_select_field(
                shared.project.id, card.id, field, shared.project.verdict.option(desired.verdict)
            )
            store.set_setting("field-delivery", f"{claim_id}:{field}", {"value": desired.verdict})
            card.fields[field] = shared.project.verdict.option(desired.verdict)
    revisions = _mapping(claim.frozen_spec.get("revisions", {}))
    if revisions and not store.get_setting("field-delivery", f"{claim_id}:refs"):
        client.set_text_field(
            shared.project.id,
            card.id,
            shared.project.refs.id,
            " ".join(f"{key}@{str(revisions[key])[:7]}" for key in ("runner", "skills", "evals")),
        )
        store.set_setting("field-delivery", f"{claim_id}:refs", {"complete": True})
        store.record_event(
            claim_id,
            "frozen-inputs",
            "Frozen evaluation inputs:\n```json\n"
            + json.dumps(claim.frozen_spec, indent=2)
            + "\n```",
        )
    controller.deliver_reports(claim_id)


def _mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}
