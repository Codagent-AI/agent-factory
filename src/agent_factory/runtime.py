"""Compose one configured poll from the existing controller and suite components."""

from __future__ import annotations

import functools
import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Generator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory import audit, job_cap, notify, retention, terminal, watch, work_kinds
from agent_factory.backends.resolve import backend_for
from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import (
    AttemptResult,
    Controller,
    advisory_lock,
    hold_active,
    quota_deadline,
)
from agent_factory.github import (
    AppCredentials,
    GitHubApiError,
    GitHubClient,
    InstallationTokenProvider,
    ProjectQueueItem,
    SubprocessGhRunner,
)
from agent_factory.operations import Diagnostic, doctor
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.suites.and_scene import (
    ReadinessError,
    RecoveryStateError,
    WorktreeError,
)
from agent_factory.supervisor import launch_supervisor
from agent_factory.work_kinds.base import Feedback, Preparation, WorkKindHandler, card_status
from agent_factory.work_kinds.eval.publication import publish_eval_results

logger = logging.getLogger(__name__)


@dataclass
class CycleView:
    cards: list[ProjectQueueItem] | None = None


@contextmanager
def _watch_finally(
    store: ClaimStore,
    client: GitHubClient,
    shared: SharedConfig,
    local: LocalConfig,
    config_path: Path,
    token_provider: InstallationTokenProvider,
) -> Generator[CycleView, None, None]:
    view = CycleView()
    try:
        notify.begin(store, shared)
    except Exception:
        logger.exception("notify begin failed")
    try:
        yield view
    finally:
        try:
            watch.step(store, client, shared, local, config_path, token_provider)
        finally:
            notify.step(store, client, shared, local, view.cards)


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
    for handler in registered.values():
        handler.attach_github(client, token_provider)
    with (
        advisory_lock(state, "cycle"),
        closing(ClaimStore(state, job_cap=shared.job_cap)) as store,
        _watch_finally(store, client, shared, local, config_path, token_provider) as view,
    ):
        controller = Controller(
            store,
            client,
            registered,
            factory_login=shared.bot_login,
            artifact_root=local.storage_root / "artifacts",
        )
        # Backfill before any tick write can advance a legacy claim's updated_at.
        terminal.backfill_terminal_times(store)
        _reconcile_backends(store, local)
        client.validate_project(shared.project)
        cards = client.list_project_items(shared.project.id, priority_id=shared.project.priority_id)
        view.cards = cards
        permission_cache: dict[tuple[str, str], str | None] = {}
        seen: dict[str, str] = {}
        sync_cache: dict[str, bool] = {}
        for card in cards:
            for handler in registered.values():
                handler.ready_handoff(card, shared, permission_cache)
        _consume_results(store, controller, local)
        job_cap.observe(
            store,
            store.job_cap_state(datetime.now(local.schedule.timezone)),
            datetime.now(local.schedule.timezone),
        )
        # Results are captured whether or not anyone reviews them; a failure is
        # reported on the item and retried next tick, never blocking the cycle.
        publish_eval_results(store, client, shared)
        # Feedback and reconciliation also work while paused or outside the window.
        now = datetime.now(local.schedule.timezone)
        artifact_root = local.storage_root / "artifacts"

        # The Docker memory probe only runs when a sandbox kind is actually a candidate
        # for admission this tick, and its result is cached so it runs at most once.
        @functools.cache
        def sandbox_memory() -> Diagnostic:
            from agent_factory.backends.docker import DockerContainerBackend

            probed = DockerContainerBackend().memory_readiness(local.limits.memory_reservation_gib)
            store.set_setting(
                "runtime", "memory", {} if probed.available else {"reason": probed.detail}
            )
            return probed

        @functools.cache
        def shared_eval_diagnostics() -> list[Diagnostic]:
            return doctor(local, include_fix=False, include_informational=False)

        kind_failure_cache: dict[str, list[Diagnostic]] = {}

        def kind_failures(candidate_handler: WorkKindHandler) -> list[Diagnostic]:
            if candidate_handler.kind not in kind_failure_cache:
                failures = _kind_failures(
                    candidate_handler, local, shared, shared_eval_diagnostics(), sandbox_memory
                )
                mismatch = _fly_mismatch_diagnostic(store, local)
                if candidate_handler.kind == "eval" and mismatch is not None:
                    failures.append(mismatch)
                kind_failure_cache[candidate_handler.kind] = failures
            return kind_failure_cache[candidate_handler.kind]

        def kind_ready(candidate_handler: WorkKindHandler) -> bool:
            failures = kind_failures(candidate_handler)
            reason = "; ".join(f"{d.name}: {d.detail}" for d in failures)
            store.set_setting(
                "runtime",
                f"readiness:{candidate_handler.kind}",
                {"reason": reason} if reason else {},
            )
            return not reason

        for card in cards:
            claims = store.claims_for_item(card.id)
            if not claims:
                _repair_unclaimed(store, client, shared, card)
            for claim in claims:
                seen[claim.id] = card_status(shared, card)
                handler = controller.handler(claim.kind)
                retention.observe_done(store, claim, card_status(shared, card), now)
                claim = store.get_claim(claim.id) or claim
                if claim.lifecycle == "superseded":
                    continue
                if card.source.state.lower() == "closed" and _should_cancel(claim):
                    controller.cancel(claim.id)
                    claim = store.get_claim(claim.id) or claim
                if claim.lifecycle == "blocked" and handler is not None:
                    memory_available = (
                        not handler.needs_sandbox_memory(local) or sandbox_memory().available
                    )
                    admitted = handler.unblock(
                        store,
                        client,
                        shared,
                        local,
                        card,
                        claim,
                        bot_login=shared.bot_login,
                        artifact_root=artifact_root,
                        now=now,
                        memory_available=memory_available,
                    )
                    claim = store.get_claim(claim.id) or claim
                    if admitted is not None:
                        run, preparation = admitted
                        try:
                            _launch(
                                state,
                                config_path,
                                store,
                                controller,
                                handler,
                                local,
                                claim,
                                run,
                                preparation,
                            )
                        except (WorktreeError, ReadinessError) as error:
                            _hold_for_readiness(store, claim.id, error)
                        claim = store.get_claim(claim.id) or claim
                if handler is not None and claim.lifecycle == "settled":
                    handler.merge_sync(
                        store,
                        client,
                        local,
                        claim,
                        bot_login=shared.bot_login,
                        card_done=card_status(shared, card) == "Done",
                    )
                    claim = store.get_claim(claim.id) or claim
                if handler is not None:
                    admitted = handler.review_round(
                        store,
                        client,
                        claim,
                        bot_login=shared.bot_login,
                        artifact_root=artifact_root,
                        now=now,
                        local=local,
                        memory_available=(
                            not handler.needs_sandbox_memory(local) or sandbox_memory().available
                        ),
                        readiness=lambda selected=handler: kind_ready(selected),
                    )
                    if admitted is not None:
                        run, preparation = admitted
                        claim = store.get_claim(claim.id) or claim
                        _launch(
                            state,
                            config_path,
                            store,
                            controller,
                            handler,
                            local,
                            claim,
                            run,
                            preparation,
                        )
                        continue
                gesture = handler.gesture(claim, card, []) if handler is not None else None
                if not (gesture == "fresh" and claim.lifecycle == "settled") and _presents_card(
                    claim, issue_state=card.source.state
                ):
                    _report(store, controller, client, shared, card, claim.id, handler)
                if handler is not None and (
                    (claim.lifecycle == "settled" and card_status(shared, card) != "Done")
                    or (
                        claim.cleanup.get("complete") is not True
                        and (
                            claim.lifecycle != "settled"
                            or claim.cleanup.get("review_observed") is True
                        )
                        and terminal.quiescent(
                            store, store.get_claim(claim.id) or claim, client, sync_cache
                        )
                    )
                ):
                    handler.cleanup(claim, board_status=card_status(shared, card))
        terminal.sweep(store, controller, client, registered, local, seen, now, sync_cache)
        paused = store.is_paused()
        quota_holds = store.get_settings_by_prefix("admission", "quota:")
        quota_error = _quota_hold_error(quota_holds)
        store.set_setting("runtime", "quota-error", {"reason": quota_error} if quota_error else {})

        # The loop breaks after the first reservation, so slot state cannot change mid-loop.
        slot_free = {kind: not store.nonterminal_runs(kind=kind) for kind in registered}
        for card in cards:
            snapshot = None
            handler: WorkKindHandler | None = None
            candidate_card = _readiness_labelled(store, client, shared, card)
            for candidate in registered.values():
                snapshot = candidate.snapshot(candidate_card, client, shared)
                if snapshot is not None:
                    handler = candidate
                    break
            if snapshot is None or handler is None:
                continue
            factory_readiness_label = (
                handler.kind in {"fix", "feature"}
                and "needs-input" in card.source.labels
                and "needs-input" not in candidate_card.source.labels
            )
            parsed = handler.request_fingerprint(snapshot)
            if isinstance(parsed, Feedback):
                # Existing controller supplies durable corrective comment feedback.
                controller.accept(snapshot, resolve=lambda _: ("", ""))
                client.set_attention_label(snapshot.repository, snapshot.issue_number, True)
                continue
            if not factory_readiness_label:
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
            if not kind_ready(handler):
                continue
            try:
                existing = store.claims_for_item(snapshot.project_item_id)
                fresh = bool(existing and handler.gesture(existing[-1], card, []) == "fresh")
                cap_state = store.job_cap_state(now)
                if cap_state.reached:
                    selected = controller.select_existing(snapshot, fresh=fresh)
                    if selected is not None:
                        unit, _ = handler.next_unit(selected, store.runs_for_claim(selected.id))
                        if unit is not None:
                            job_cap.hold_claim(store, selected.id, cap_state, now)
                            _report(store, controller, client, shared, card, selected.id, handler)
                    else:
                        episode = job_cap.open_episode(store, cap_state, now)
                        key = f"{snapshot.repository}:{snapshot.issue_number}"
                        receipt = store.get_setting("job-cap-card", key)
                        if receipt is None or receipt.get("episode") != episode:
                            draft = controller.preflight(snapshot, resolve=handler.resolve_request)
                            if draft is None:
                                continue
                            holds = store.get_settings_by_prefix("admission", "quota:")
                            if any(
                                (hold := holds.get(f"quota:{provider}")) is not None
                                and hold_active(hold, now)
                                for provider in handler.providers_for_spec(draft.frozen_spec)
                            ):
                                continue
                        if (
                            receipt is None
                            or receipt.get("episode") != episode
                            or not receipt.get("comment_id")
                        ):
                            job_cap.notify_card(
                                store,
                                client,
                                shared.bot_login,
                                snapshot.repository,
                                snapshot.issue_number,
                                episode,
                                cap_state,
                            )
                    continue
                claim = controller.accept(
                    snapshot,
                    resolve=handler.resolve_request,
                    fresh=fresh,
                )
            except ReadinessError as error:
                if not factory_readiness_label:
                    key = f"{snapshot.repository}:{snapshot.issue_number}"
                    store.set_setting(
                        "request-readiness",
                        key,
                        {**(store.get_setting("request-readiness", key) or {}), "label": "factory"},
                    )
                    client.set_attention_label(snapshot.repository, snapshot.issue_number, True)
                controller.report_request_readiness(snapshot, str(error), factory_label=True)
                continue
            if factory_readiness_label and claim is not None:
                client.set_attention_label(snapshot.repository, snapshot.issue_number, False)
            if claim is not None or not factory_readiness_label:
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
                _launch(
                    state, config_path, store, controller, handler, local, claim, run, preparation
                )
                _report(store, controller, client, shared, card, claim.id, handler)
                break
            except (WorktreeError, ReadinessError) as error:
                _hold_for_readiness(store, claim.id, error)
                _report(store, controller, client, shared, card, claim.id, handler)


def _readiness_labelled(
    store: ClaimStore, client: GitHubClient, shared: SharedConfig, card: ProjectQueueItem
) -> ProjectQueueItem:
    """Let a pre-claim readiness label through admission only while the factory owns it."""
    key = f"{card.source.repository}:{card.source.number}"
    receipt = store.get_setting("request-readiness", key)
    if not receipt or receipt.get("label") != "factory":
        return card
    if "needs-input" not in card.source.labels:
        store.set_setting(
            "request-readiness", key, {k: v for k, v in receipt.items() if k != "label"}
        )
        return card
    if card.source.issue_type in {shared.routing.bug_type, shared.routing.feature_type}:
        try:
            factory_added = (
                client.attention_label_actor(card.source.repository, card.source.number)
                == shared.bot_login
            )
        except GitHubApiError:
            return card
    else:
        factory_added = True
    if not factory_added:
        store.set_setting(
            "request-readiness", key, {k: v for k, v in receipt.items() if k != "label"}
        )
        return card
    source = replace(card.source, labels=card.source.labels - {"needs-input"})
    return replace(card, source=source)


def _hold_for_readiness(store: ClaimStore, claim_id: str, error: Exception) -> None:
    """Wait on a readiness failure unless a recorded pre-suite failure already stopped the claim."""
    current = store.get_claim(claim_id)
    if current is not None and current.lifecycle == "settled":
        return
    store.set_hold(claim_id, "readiness", {"reason": str(error)})
    store.set_claim_lifecycle(claim_id, "waiting", {"verdict": "infra-error"})
    store.record_event(claim_id, f"readiness:{error}", f"Waiting: {error}")


def _launch(
    state: Path,
    config_path: Path,
    store: ClaimStore,
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
                {
                    "reason": str(error),
                    "error_type": type(error).__name__,
                    "failure_stage": "pre-suite",
                    "stage": "planning",
                },
            ),
        )
        # Preserve worktree readiness handling and unexpected error tracebacks.
        if not isinstance(error, (OSError, ReadinessError, RecoveryStateError)):
            raise
        return
    # The attempt launched, so an earlier readiness failure no longer blocks the claim.
    store.clear_setting("claim-hold", f"{claim.id}:readiness")
    store.clear_setting("claim-hold", f"{claim.id}:job-cap")


def _quota_hold_error(holds: Mapping[str, Mapping[str, object]]) -> str | None:
    """Surface a malformed quota hold for operator repair; valid holds gate per provider."""
    for hold in holds.values():
        try:
            quota_deadline(hold)
        except ValueError as exc:
            return f"Admission held: {exc}. Repair the saved quota reset timestamp."
    return None


def _resolve_revision(source: Path, revision: str, *, fetch: bool = True) -> str:  # pyright: ignore[reportUnusedFunction]
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


def _fly_mismatch_diagnostic(store: ClaimStore, local: LocalConfig) -> Diagnostic | None:
    """A saved mismatch holds evals only under Fly, where reconciliation can clear it."""
    if getattr(local, "eval_execution", "docker") != "fly":
        return None
    mismatch = store.get_setting("runtime", "fly:mismatch")
    if not mismatch:
        return None
    machine = mismatch.get("machine_id", "unknown")
    remedy = mismatch.get("remedy", "wait for the Machine deadline")
    return Diagnostic(
        "Fly ownership mismatch", False, f"Machine {machine}: {remedy}", str(remedy), "eval-fly"
    )


def _kind_failures(
    handler: WorkKindHandler,
    local: LocalConfig,
    shared: SharedConfig,
    diagnostics: list[Diagnostic],
    sandbox_memory: Callable[[], Diagnostic],
) -> list[Diagnostic]:
    """Only the groups applicable to this kind under its configured mode can hold it."""
    if handler.kind == "eval":
        mode_group = (
            "eval-fly" if getattr(local, "eval_execution", "docker") == "fly" else "eval-sandbox"
        )
        failures = [
            d
            for d in diagnostics
            if d.group in {"shared", "eval", mode_group}
            and not d.available
            # This checkout is needed only to freeze a new Fly claim. Existing claims
            # already carry their revisions and may launch without the checkout.
            and d.name != "Agent Validator checkout"
        ]
        if getattr(local, "eval_execution", "docker") == "fly":
            mismatch = next(
                (
                    d
                    for d in diagnostics
                    if d.group == "eval-fly" and d.name == "Fly ownership mismatch"
                ),
                None,
            )
            if mismatch is not None:
                failures.append(mismatch)
            return failures
        memory = sandbox_memory()
        return failures + ([memory] if not memory.available else [])
    failures = [d for d in diagnostics if d.group == "shared" and not d.available]
    if not handler.needs_sandbox_memory(local):
        return failures + [d for d in handler.readiness(local, shared) if not d.available]
    docker_diagnostic = next((d for d in diagnostics if d.name == "Docker"), None)
    if docker_diagnostic is not None and not docker_diagnostic.available:
        failures.append(docker_diagnostic)
    memory = sandbox_memory()
    if not memory.available:
        failures.append(memory)
    readiness = handler.readiness(local, shared, docker_diagnostic=docker_diagnostic)
    return failures + [d for d in readiness if not d.available]


def _should_cancel(claim: Claim) -> bool:
    """Closure cancels only unfinished execution; a settled claim keeps its recorded outcome."""
    return claim.lifecycle != "settled"


def _presents_card(claim: Claim, *, issue_state: str) -> bool:
    """A claim cancelled by closure stops owning the card once its issue is reopened."""
    return not (claim.lifecycle == "cancelled" and issue_state.lower() != "closed")


def _consume_results(
    store: ClaimStore, controller: Controller, local: LocalConfig | None = None
) -> None:
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
            backend = backend_for(run.plan)
            if backend is not None:
                provenance = backend.provenance(backend.identity_from_plan(run.plan, run) or {})
                if provenance:
                    result = replace(result, result={**result.result, **provenance})
            controller.record_result(run.id, result)
            _settle_audit(store, claim, run)
            _dispose_result(store, handler, run, result, local)
            for event in handler.report_events(claim, run, result):
                store.record_event(claim.id, event.key, event.body)
            store.set_setting("consumed-results", run.id, {"complete": True})


def _settle_audit(store: ClaimStore, claim: Claim, run: Run) -> None:
    """Skip settlement while audits are off; otherwise report undelivered metrics.

    While audits are on, every factory run is audited: a host attempt audits inside its
    launch wrapper, and an eval's sandbox-assembled reports are delivered from here. The
    attempt's own result is never changed by its audit.
    """
    if not audit.AUDIT_ENABLED:
        return
    hints = cast(Mapping[str, object], run.plan).get("ownership_hints")
    suite = cast(Mapping[str, object], hints).get("suite") if isinstance(hints, Mapping) else None
    evidence = Path(run.evidence_path)
    summary = audit.settle(
        evidence,
        eval_suite=suite == "and-scene",
        runner=shutil.which("agent-runner"),
    )
    body = audit.event_body(summary, evidence) if summary is not None else None
    if body is not None:
        try:
            store.record_event(
                claim.id, f"{run.unit_key}:attempt-{run.attempt_number}:post-run-audit", body
            )
        except Exception:  # noqa: BLE001 - an audit report must not stop result consumption
            logger.exception("could not record the post-run audit event for run %s", run.id)


def _dispose_result(
    store: ClaimStore,
    handler: WorkKindHandler,
    run: Run,
    result: AttemptResult,
    local: LocalConfig | None,
) -> None:
    """Dispose after classification; collection/result normalization has already completed."""
    backend = backend_for(run.plan)
    if backend is None:
        return
    if backend.name == "fly-machine":
        from agent_factory.fly.backend import FlyMachineBackend

        backend = FlyMachineBackend(local=local)
    identity = backend.identity_from_plan(run.plan, run)
    if identity is None:
        return
    classification = handler.classify(run, result).kind
    if result.execution_status == "cancelled" or run.status == "cancelled":
        return
    if result.quota_until is not None or classification == "quota":
        decision = "stop"
    elif result.result.get("failure_stage") == "pre-suite":
        # Only a Machine the failed attempt created goes; a recovery attempt's
        # retained Machine holds the checkpoint its relaunch resumes from.
        decision = "destroy" if run.reason == "initial" else "keep"
    elif classification == "technical" and run.reason != "recovery":
        decision = "keep"
    else:
        # Includes a lost Machine: the backend re-probes and only destroys when
        # it still owns the matching resource.
        decision = "destroy"
    backend.dispose(identity, decision, store)


def _reconcile_backends(store: ClaimStore, local: LocalConfig) -> None:
    """Reconcile each configured or still-owned backend before admitting work."""
    names = {"fly-machine" if local.eval_execution == "fly" else "docker", local.fix.execution}
    fly_sources: set[tuple[str, Path]] = set()
    if local.fly is not None and local.eval_execution == "fly":
        fly_sources.add((local.fly.app, local.fly.token_file))
    for run in store.runs_requiring_backend_reconciliation():
        backend = backend_for(run.plan)
        if backend is None:
            continue
        names.add(backend.name)
        if backend.name == "fly-machine":
            hints = run.plan.get("ownership_hints")
            artifact = (
                cast(Mapping[str, object], hints).get("artifact_path")
                if isinstance(hints, Mapping)
                else None
            )
            path = Path(artifact) if isinstance(artifact, str) else Path(run.evidence_path)
            try:
                manifest = json.loads((path / ".factory" / "manifest.json").read_text())
            except (OSError, json.JSONDecodeError):
                continue
            fly = (
                cast(Mapping[str, object], manifest).get("fly")
                if isinstance(manifest, dict)
                else None
            )
            if isinstance(fly, dict):
                values = cast(Mapping[str, object], fly)
                app = values.get("app")
                token = values.get("token_file")
                if isinstance(app, str) and app and isinstance(token, str) and token:
                    fly_sources.add((app, Path(token)))
    for name in sorted(names):
        if name == "fly-machine":
            from agent_factory.fly.backend import FlyMachineBackend

            for app, token_file in sorted(fly_sources):
                FlyMachineBackend(app=app, token_file=token_file, local=local).reconcile(store)
            if not fly_sources:
                FlyMachineBackend(local=local).reconcile(store)
        else:
            plan = {"ownership_hints": {"backend": name}}
            backend = backend_for(plan)
            if backend is not None:
                backend.reconcile(store)


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
        status_key = f"{claim_id}:{shared.project.status.id}"
        delivered = store.get_setting("field-delivery", status_key)
        if card.fields.get(shared.project.status.id) != option:
            client.set_single_select_field(
                shared.project.id, card.id, shared.project.status.id, option
            )
            card.fields[shared.project.status.id] = option
            # Only a card the factory already showed as Running was moved by someone
            # else; admission's own move out of a queued status needs no explanation.
            if (
                (active or claim.lifecycle == "blocked")
                and status == "Running"
                and current in {"Ready", "Review", "Done"}
                and delivered is not None
                and delivered.get("value") == "Running"
            ):
                store.record_event(
                    claim_id,
                    f"status-repair:{current}:{len(runs)}",
                    "Status restored to Running because this evaluation is still active.",
                )
        if delivered is None or delivered.get("value") != status:
            store.set_setting("field-delivery", status_key, {"value": status})
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
