"""Compose one configured poll from the existing controller and suite components."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory import work_kinds
from agent_factory.config import FixTarget, LocalConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    Controller,
    advisory_lock,
    quota_deadline,
)
from agent_factory.github import (
    AppCredentials,
    GitHubClient,
    InstallationTokenProvider,
    ProjectQueueItem,
    SubprocessGhRunner,
)
from agent_factory.operations import check_memory_headroom, doctor
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.suites.and_scene import (
    ReadinessError,
    RecoveryStateError,
    SourceRepositories,
    WorktreeError,
)
from agent_factory.supervisor import launch_supervisor
from agent_factory.work_kinds.base import Feedback, Preparation, WorkKindHandler, card_status
from agent_factory.work_kinds.eval import ParsedRequest
from agent_factory.work_kinds.fix.blocked import process_blocked_claim
from agent_factory.work_kinds.fix.handler import FixHandler
from agent_factory.work_kinds.fix.sync import sync_claim


def cycle(state: Path, config_path: Path) -> None:
    local = LocalConfig.from_file(config_path)
    shared = SharedConfig.from_file(local.shared_config)
    if not shared.bot_login:
        raise ValueError("shared github.bot_login is required for report reconciliation")
    runner = SubprocessGhRunner()
    token_provider = InstallationTokenProvider(
        AppCredentials(shared.app_id, shared.installation_id, local.credentials.github_app_key),
    )
    client = GitHubClient(runner, token_provider)
    registered = work_kinds.handlers(shared, local)
    fix_handler = registered.get("fix")
    if isinstance(fix_handler, FixHandler):
        fix_handler.attach_installation_token(token_provider)
        fix_handler.attach_github(client)
    with advisory_lock(state, "cycle"), closing(ClaimStore(state)) as store:
        controller = Controller(
            store,
            client,
            registered,
            factory_login=shared.bot_login,
            artifact_root=local.storage_root / "artifacts",
        )
        client.validate_project(shared.project)
        cards = client.list_project_items(shared.project.id)
        _consume_results(store, controller)
        # Feedback and reconciliation also work while paused or outside the window.
        now = datetime.now(local.schedule.timezone)
        artifact_root = local.storage_root / "artifacts"
        memory = check_memory_headroom(local.limits.memory_reservation_gib)
        memory_setting = {} if memory.available else {"reason": memory.detail}
        store.set_setting("runtime", "memory", memory_setting)
        for card in cards:
            claims = store.claims_for_item(card.id)
            if not claims:
                _repair_unclaimed(store, client, shared, card)
            for claim in claims:
                if claim.lifecycle == "superseded":
                    continue
                handler = controller.handler(claim.kind)
                if card.source.state.lower() == "closed" and _should_cancel(claim):
                    controller.cancel(claim.id)
                    claim = store.get_claim(claim.id) or claim
                if (
                    claim.lifecycle == "blocked"
                    and isinstance(handler, FixHandler)
                    and memory.available
                ):
                    admitted = process_blocked_claim(
                        store,
                        client,
                        handler,
                        shared,
                        local,
                        card,
                        claim,
                        bot_login=shared.bot_login,
                        artifact_root=artifact_root,
                        now=now,
                    )
                    claim = store.get_claim(claim.id) or claim
                    if admitted is not None:
                        run, preparation = admitted
                        try:
                            _launch(
                                state,
                                config_path,
                                controller,
                                handler,
                                local,
                                claim,
                                run,
                                preparation,
                            )
                        except (WorktreeError, ReadinessError) as error:
                            store.set_hold(claim.id, "readiness", {"reason": str(error)})
                            store.set_claim_lifecycle(
                                claim.id, "waiting", {"verdict": "infra-error"}
                            )
                            store.record_event(claim.id, f"readiness:{error}", f"Waiting: {error}")
                        claim = store.get_claim(claim.id) or claim
                if claim.kind == "fix" and claim.lifecycle == "settled":
                    sync_claim(
                        store,
                        client,
                        local,
                        claim,
                        bot_login=shared.bot_login,
                        card_done=card_status(shared, card) == "Done",
                    )
                    claim = store.get_claim(claim.id) or claim
                gesture = handler.gesture(claim, card, []) if handler is not None else None
                if not (gesture == "fresh" and claim.lifecycle == "settled"):
                    _report(store, controller, client, shared, card, claim.id, handler)
                if handler is not None:
                    handler.cleanup(claim, board_status=card_status(shared, card))
        paused = store.is_paused()
        quota_holds = store.get_settings_by_prefix("admission", "quota:")
        quota_error = _quota_hold_error(quota_holds)
        store.set_setting("runtime", "quota-error", {"reason": quota_error} if quota_error else {})
        prerequisites: str | None = None
        kind_readiness: dict[str, str] = {}
        # The loop breaks after the first reservation, so slot state cannot change mid-loop.
        slot_free = {kind: not store.nonterminal_runs(kind=kind) for kind in registered}
        for card in cards:
            snapshot = None
            handler: WorkKindHandler | None = None
            for candidate in registered.values():
                snapshot = candidate.snapshot(card, client, shared)
                if snapshot is not None:
                    handler = candidate
                    break
            if snapshot is None or handler is None:
                continue
            parsed = handler.request_fingerprint(snapshot)
            if isinstance(parsed, Feedback):
                # Existing controller supplies durable corrective comment feedback.
                controller.accept(snapshot, resolve=lambda _: ("", ""))
                client.set_attention_label(snapshot.repository, snapshot.issue_number, True)
                continue
            client.set_attention_label(snapshot.repository, snapshot.issue_number, False)
            # Admission is per kind: this kind's slot and window gate independently.
            # Quota holds are provider-scoped and enforced in Controller.reserve_next
            # against the specific claim's providers, not pre-filtered here.
            ready = (
                not paused
                and handler.window(local).allows_admission(now)
                and slot_free.get(handler.kind, False)
            )
            if not ready:
                continue
            if not memory.available:
                continue
            if prerequisites is None:
                failures = [d for d in doctor(local, include_fix=False) if not d.available]
                prerequisites = "; ".join(f"{d.name}: {d.detail}" for d in failures)
            if prerequisites:
                store.set_setting("runtime", f"readiness:{handler.kind}", {"reason": prerequisites})
                break
            if handler.kind not in kind_readiness:
                kind_failures = [d for d in handler.readiness(local, shared) if not d.available]
                kind_readiness[handler.kind] = "; ".join(
                    f"{d.name}: {d.detail}" for d in kind_failures
                )
            kind_reason = kind_readiness[handler.kind]
            if kind_reason:
                store.set_setting("runtime", f"readiness:{handler.kind}", {"reason": kind_reason})
                continue
            store.set_setting("runtime", f"readiness:{handler.kind}", {})
            try:
                existing = store.claims_for_item(snapshot.project_item_id)
                fresh = bool(existing and handler.gesture(existing[-1], card, []) == "fresh")
                claim = controller.accept(
                    snapshot,
                    resolve=lambda request, chosen=handler: _resolve_for(chosen, request),
                    fresh=fresh,
                )
            except ReadinessError as error:
                controller.report_request_readiness(snapshot, str(error))
                client.set_attention_label(snapshot.repository, snapshot.issue_number, True)
                continue
            store.set_setting(
                "request-readiness", f"{snapshot.repository}:{snapshot.issue_number}", {}
            )
            if claim is None or claim.lifecycle in {"settled", "cancelled", "superseded"}:
                continue
            try:
                preparation = handler.prepare(claim)
                run = controller.reserve_next(claim.id, readiness=lambda: None)
                if run is None:
                    # Preparation may have settled the claim (an earlier attempt's PR was
                    # found); present that immediately rather than on the next poll.
                    _report(store, controller, client, shared, card, claim.id, handler)
                    continue
                _launch(state, config_path, controller, handler, local, claim, run, preparation)
                _report(store, controller, client, shared, card, claim.id, handler)
                break
            except (WorktreeError, ReadinessError) as error:
                store.set_hold(claim.id, "readiness", {"reason": str(error)})
                store.set_claim_lifecycle(claim.id, "waiting", {"verdict": "infra-error"})
                store.record_event(claim.id, f"readiness:{error}", f"Waiting: {error}")
                _report(store, controller, client, shared, card, claim.id, handler)


def _launch(
    state: Path,
    config_path: Path,
    controller: Controller,
    handler: WorkKindHandler,
    local: LocalConfig,
    claim: Claim,
    run: Run,
    preparation: Preparation,
) -> None:
    """Plan and detach one reserved attempt, releasing the slot if either step fails."""
    try:
        plan = handler.plan(claim, run, preparation)
        launch_supervisor(state, run.id, plan, handler.limits(local), config_path=config_path)
    except Exception as error:
        # Planning and launch failures must release the reserved execution slot.
        controller.record_result(
            run.id,
            AttemptResult(
                "failed",
                None,
                {"reason": str(error), "error_type": type(error).__name__},
            ),
        )
        # Preserve worktree readiness handling and unexpected error tracebacks.
        if not isinstance(error, (OSError, ReadinessError, RecoveryStateError)):
            raise


def _quota_hold_error(holds: Mapping[str, Mapping[str, object]]) -> str | None:
    """Surface a malformed quota hold for operator repair; valid holds gate per provider."""
    for hold in holds.values():
        try:
            quota_deadline(hold)
        except ValueError as exc:
            return f"Admission held: {exc}. Repair the saved quota reset timestamp."
    return None


def _resolve_for(handler: WorkKindHandler, request: object) -> tuple[str, ...]:
    if isinstance(handler, FixHandler):
        if not isinstance(request, FixTarget):
            raise ReadinessError("fix handler cannot resolve a non-target request")
        return handler.resolve(request)
    sources = getattr(handler, "sources", None)
    if not isinstance(sources, SourceRepositories) or not isinstance(request, ParsedRequest):
        raise ReadinessError("eval handler cannot resolve pinned revisions")
    return _resolve(sources, request)


def _resolve(sources: SourceRepositories, request: ParsedRequest) -> tuple[str, str]:
    return (
        _resolve_revision(sources.runner, str(request.settings["agent_runner_ref"])),
        _resolve_revision(sources.skills, str(request.settings["agent_skills_ref"])),
    )


def _resolve_revision(source: Path, revision: str, *, fetch: bool = True) -> str:
    try:
        if fetch:
            fetched = subprocess.run(
                ["git", "-C", str(source), "fetch", "--quiet", "--prune", "--tags", "origin"],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if fetched.returncode != 0:
                # Git stderr may contain credential-bearing remote URLs; report context, not
                # secrets.
                raise ReadinessError(
                    f"Cannot fetch {source} from origin (git exit {fetched.returncode}); "
                    "check remote access."
                )
        if re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
            candidates = (revision,)
        elif revision.startswith("refs/heads/"):
            candidates = ("refs/remotes/origin/" + revision.removeprefix("refs/heads/"),)
        elif revision.startswith(("refs/tags/", "refs/remotes/origin/")):
            candidates = (revision,)
        else:
            branch = revision.removeprefix("origin/")
            candidates = (f"refs/remotes/origin/{branch}", f"refs/tags/{revision}")
        for candidate in candidates:
            resolved = subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "rev-parse",
                    "--verify",
                    "--end-of-options",
                    candidate + "^{commit}",
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if resolved.returncode == 0:
                return resolved.stdout.strip()
        raise ReadinessError(
            f"Cannot resolve revision {revision!r} in {source}; "
            "check the branch, tag, or commit SHA."
        )
    except subprocess.TimeoutExpired as error:
        raise ReadinessError(
            f"Git revision lookup timed out for {source}; check remote access."
        ) from error
    except OSError as error:
        raise ReadinessError(
            f"Git revision lookup could not run for {source} (OS error {error.errno})."
        ) from error


def _git_show(source: Path, sha: str, path: str) -> str:  # pyright: ignore[reportUnusedFunction]
    """Read one file's text at a commit, or raise ReadinessError if it is absent."""
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "show", f"{sha}:{path}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise ReadinessError(f"Cannot read {path} at {sha[:7]} in {source}: {error}") from error
    if result.returncode != 0:
        raise ReadinessError(f"{path} does not exist at {sha[:7]} in {source}.")
    return result.stdout


def _repair_unclaimed(
    store: ClaimStore, client: GitHubClient, shared: SharedConfig, card: ProjectQueueItem
) -> None:
    if (
        card.fields.get(shared.project.owner.id) != shared.project.owner.option("factory")
        or card_status(shared, card) != "Running"
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


def _should_cancel(claim: Claim) -> bool:
    """Closure cancels only unfinished execution; a settled claim keeps its recorded outcome."""
    return claim.lifecycle != "settled"


def _consume_results(store: ClaimStore, controller: Controller) -> None:
    """Record every finished-but-unconsumed attempt through its kind's handler."""
    for claim in store.all_claims():
        if claim.lifecycle in {"cancelled", "superseded"}:
            continue
        handler = controller.handler(claim.kind)
        for run in store.runs_for_claim(claim.id):
            if run.status in NONTERMINAL_RUN_STATUSES or store.get_setting(
                "consumed-results", run.id
            ):
                continue
            if handler is None:
                _hold_for_missing_handler(store, claim, run)
                continue
            result = handler.read_result(run)
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
            for event in handler.report_events(claim, run, result):
                store.record_event(claim.id, event.key, event.body)
            store.set_setting("consumed-results", run.id, {"complete": True})


def _hold_for_missing_handler(store: ClaimStore, claim: Claim, run: Run) -> None:
    """Report config drift once per run and park the claim for operator repair."""
    if store.get_setting("missing-handler", run.id):
        return
    store.record_event(
        claim.id,
        f"{run.unit_key}:attempt-{run.attempt_number}:missing-handler",
        f"Cannot consume result: no work-kind handler is registered for "
        f"kind {claim.kind!r}. Claim held for operator repair.",
    )
    store.set_claim_lifecycle(claim.id, "waiting", {"verdict": "infra-error"})
    store.set_setting("missing-handler", run.id, {"reported": True})


def _report(
    store: ClaimStore,
    controller: Controller,
    client: GitHubClient,
    shared: SharedConfig,
    card: ProjectQueueItem,
    claim_id: str,
    handler: WorkKindHandler | None,
) -> None:
    claim = store.get_claim(claim_id)
    if claim is None or card.fields.get(shared.project.owner.id) != shared.project.owner.option(
        "factory"
    ):
        return
    runs = store.runs_for_claim(claim_id)
    active = any(r.status in NONTERMINAL_RUN_STATUSES for r in runs)
    current = card_status(shared, card)
    desired = controller.presentation(claim_id)
    status = desired.status
    # A reviewed Done card releases worktrees; never bounce it back to Review.
    if not (current == "Done" and claim.lifecycle == "settled"):
        option = shared.project.status.option(status.lower())
        if card.fields.get(shared.project.status.id) != option:
            client.set_single_select_field(
                shared.project.id, card.id, shared.project.status.id, option
            )
            card.fields[shared.project.status.id] = option
            if (
                (active or claim.lifecycle == "blocked")
                and status == "Running"
                and current in {"Ready", "Review", "Done"}
            ):
                store.record_event(
                    claim_id,
                    f"status-repair:{current}:{len(runs)}",
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
    elif active and card.fields.get(shared.project.verdict.id) is not None:
        client.clear_field(shared.project.id, card.id, shared.project.verdict.id)
        card.fields.pop(shared.project.verdict.id, None)
        store.set_setting("field-delivery", f"{claim_id}:{shared.project.verdict.id}", {})
    refs = handler.refs_text(claim) if handler is not None else None
    if refs is not None and not store.get_setting("field-delivery", f"{claim_id}:refs"):
        client.set_text_field(
            shared.project.id,
            card.id,
            shared.project.refs.id,
            refs,
        )
        store.set_setting("field-delivery", f"{claim_id}:refs", {"complete": True})
        frozen_body = handler.frozen_inputs_event(claim) if handler is not None else None
        if frozen_body is not None:
            store.record_event(claim_id, "frozen-inputs", frozen_body)
    for label, needed in desired.labels.items():
        receipt_key = f"{claim_id}:label:{label}"
        receipt = store.get_setting("field-delivery", receipt_key)
        if receipt is not None and receipt.get("value") == needed:
            continue
        if label == "needs-input":
            client.set_attention_label(claim.repository, claim.issue_number, needed)
        store.set_setting("field-delivery", receipt_key, {"value": needed})
    controller.deliver_reports(claim_id)
