"""Age-based pruning of settled claims' attempt evidence."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agent_factory.config import LocalConfig
from agent_factory.store import TERMINAL_LIFECYCLES, Claim, ClaimStore, Run
from agent_factory.work_kinds.base import WorkKindHandler
from agent_factory.work_kinds.pull_request.kinds import registered

if TYPE_CHECKING:
    from agent_factory.terminal import PullRequestReader

_FIX_ATTEMPT_REMOVE = (
    "logs",
    "factory-suite.log",
    "agent-runner",
    "agent-runner-session",
    ".runtime",
)
_EVAL_REP_REMOVE = (
    "logs",
    "factory-suite.log",
    ".runtime/agent-runner-projects",
    ".runtime/agent-session-state",
    ".runtime/judge-workspace",
    ".runtime/judge",
    ".runtime/candidate-worktree",
)


def reconcile(
    store: ClaimStore,
    local: LocalConfig,
    claim: Claim,
    board_status: str,
    now: datetime,
    *,
    handler: WorkKindHandler | None = None,
) -> None:
    """Compatibility wrapper for the board observation and pruning paths."""
    observe_done(store, claim, board_status, now)
    prune_due(store, local, store.get_claim(claim.id) or claim, board_status, now, handler=handler)


def observe_done(store: ClaimStore, claim: Claim, board_status: str, now: datetime) -> None:
    cleanup = dict(claim.cleanup)
    if _observe_done(cleanup, board_status, now):
        store.set_cleanup(claim.id, cleanup)


def prune_due(
    store: ClaimStore,
    local: LocalConfig,
    claim: Claim,
    board_status: str | None,
    now: datetime,
    *,
    handler: WorkKindHandler | None = None,
    client: PullRequestReader | None = None,
    sync_cache: dict[str, bool] | None = None,
) -> None:
    cleanup = dict(claim.cleanup)
    if not _eligible(store, local, claim, cleanup, board_status, now, client, sync_cache):
        return
    fresh = store.get_claim(claim.id) or claim
    _prune(store, fresh, dict(fresh.cleanup), now, handler)


def _observe_done(cleanup: dict[str, object], board_status: str, now: datetime) -> bool:
    if board_status == "Done":
        if cleanup.get("done_observed_at") is None:
            cleanup["done_observed_at"] = now.isoformat()
            return True
        return False
    if cleanup.get("done_observed_at") is not None:
        cleanup["done_observed_at"] = None
        return True
    return False


def _eligible(
    store: ClaimStore,
    local: LocalConfig,
    claim: Claim,
    cleanup: Mapping[str, object],
    board_status: str | None,
    now: datetime,
    client: PullRequestReader | None,
    sync_cache: dict[str, bool] | None = None,
) -> bool:
    # Cheapest checks first: an already-pruned or not-yet-eligible claim costs no queries.
    retention = cleanup.get("retention")
    if isinstance(retention, Mapping) and cast(Mapping[str, object], retention).get("pruned_at"):
        return False
    if cleanup.get("complete") is not True:
        return False
    from agent_factory.terminal import idle_and_reported, sync_pending, terminal_time

    if board_status == "Done" and claim.lifecycle == "settled":
        observed_at = cleanup.get("done_observed_at")
        if not isinstance(observed_at, str):
            return False
        try:
            observed = datetime.fromisoformat(observed_at)
        except ValueError:
            return False
        if now - observed < timedelta(days=local.limits.evidence_retention_days):
            return False
    elif claim.lifecycle in TERMINAL_LIFECYCLES:
        days = (
            local.limits.unreviewed_retention_days
            if claim.lifecycle == "settled"
            else local.limits.evidence_retention_days
        )
        if now - terminal_time(store, claim) < timedelta(days=days):
            return False
    else:
        return False
    if not idle_and_reported(store, claim):
        return False
    if claim.lifecycle == "settled":
        if client is not None:
            return not sync_pending(store, claim, client, sync_cache)
        from agent_factory.work_kinds.pull_request.sync import pending_sync

        return not any(pending_sync(store, claim, definition) for definition in registered())
    return True


def _prune(
    store: ClaimStore,
    claim: Claim,
    cleanup: dict[str, object],
    now: datetime,
    handler: WorkKindHandler | None,
) -> None:
    targets = _removal_targets(store, claim, handler)
    errors: list[dict[str, str]] = []
    for path in targets:
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as error:
            errors.append({"path": str(path), "error": str(error)})
    removed = [str(path) for path in targets if not path.exists()]
    retention = dict(cast(Mapping[str, object], cleanup.get("retention") or {}))
    previous = retention.get("removed")
    old = (
        [path for path in cast(list[object], previous) if isinstance(path, str)]
        if isinstance(previous, list)
        else []
    )
    retention["removed"] = list(dict.fromkeys([*old, *removed]))
    retention["errors"] = errors
    if not errors:
        retention["pruned_at"] = now.isoformat()
    cleanup["retention"] = retention
    store.set_cleanup(claim.id, cleanup)


def _removal_targets(
    store: ClaimStore, claim: Claim, handler: WorkKindHandler | None = None
) -> list[Path]:
    runs = store.runs_for_claim(claim.id)
    definition = next((item for item in registered() if item.kind == claim.kind), None)
    if definition is not None:
        kind_runs = [run for run in runs if run.unit_key == definition.unit_key]
        # The supervisor appends each launched process's output to the claim-level suite
        # log, one level above the attempt directories that every attempt shares.
        claim_logs = [Path(run.evidence_path).resolve() / "factory-suite.log" for run in kind_runs]
        attempts = [
            t
            for run in kind_runs
            for t in (
                handler.retention_targets(run) if handler else pull_request_attempt_targets(run)
            )
        ]
        return list(dict.fromkeys([*claim_logs, *attempts]))
    return [
        t
        for run in runs
        for t in (handler.retention_targets(run) if handler else eval_rep_targets(run))
    ]


def pull_request_attempt_targets(run: Run) -> list[Path]:
    attempt_dir = Path(run.evidence_path).resolve() / f"attempt-{run.attempt_number + 1}"
    targets = [attempt_dir / name for name in _FIX_ATTEMPT_REMOVE]
    session_dir = run.result.get("session_dir")
    if isinstance(session_dir, str):
        session_path = Path(session_dir).resolve()
        default = attempt_dir / "agent-runner-session"
        # A run's recorded session_dir is only ever trusted when it falls strictly under
        # this attempt's own evidence directory, never the directory itself; anything else
        # is ignored rather than pruned, since run.result is suite-controlled output, not a
        # verified factory path.
        if session_path != default and attempt_dir in session_path.parents:
            targets.append(session_path)
    return targets


def eval_rep_targets(run: Run) -> list[Path]:
    rep_dir = Path(run.evidence_path).resolve()
    return [rep_dir / name for name in _EVAL_REP_REMOVE]
