"""Age-based pruning of settled claims' attempt evidence."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.work_kinds.fix.sync import pending_sync

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
)


def reconcile(
    store: ClaimStore, local: LocalConfig, claim: Claim, board_status: str, now: datetime
) -> None:
    """Observe a Done board status and prune eligible evidence, both every poll."""
    cleanup = dict(claim.cleanup)
    if _observe_done(cleanup, board_status, now):
        store.set_cleanup(claim.id, cleanup)
    retention_days = local.limits.evidence_retention_days
    if not _eligible(store, claim, cleanup, board_status, now, retention_days):
        return
    _prune(store, claim, cleanup, now)


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
    claim: Claim,
    cleanup: Mapping[str, object],
    board_status: str,
    now: datetime,
    retention_days: int,
) -> bool:
    # Cheapest checks first: an already-pruned or not-yet-eligible claim costs no queries.
    retention = cleanup.get("retention")
    if isinstance(retention, Mapping) and cast(Mapping[str, object], retention).get("pruned_at"):
        return False
    if board_status != "Done":
        return False
    observed_at = cleanup.get("done_observed_at")
    if not isinstance(observed_at, str):
        return False
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return False
    if now - observed < timedelta(days=retention_days):
        return False
    runs = store.runs_for_claim(claim.id)
    if any(run.status in NONTERMINAL_RUN_STATUSES for run in runs):
        return False
    if store.pending_events(claim.id) or claim.reporting.get("delivery_failures"):
        return False
    if pending_sync(store, claim):
        return False
    # Clone, image, and credential cleanup only ever runs for settled claims (it is the
    # Review-then-Done gate), so its completion gates only them: a superseded or cancelled
    # claim has no cleanup pass that could ever mark it complete.
    return claim.lifecycle != "settled" or cleanup.get("complete") is True


def _prune(store: ClaimStore, claim: Claim, cleanup: dict[str, object], now: datetime) -> None:
    targets = _removal_targets(store, claim)
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
    retention["removed"] = removed
    retention["errors"] = errors
    if not errors:
        retention["pruned_at"] = now.isoformat()
    cleanup["retention"] = retention
    store.set_cleanup(claim.id, cleanup)


def _removal_targets(store: ClaimStore, claim: Claim) -> list[Path]:
    runs = store.runs_for_claim(claim.id)
    if claim.kind == "fix":
        return [t for run in runs if run.unit_key == "fix" for t in _fix_attempt_targets(run)]
    return [t for run in runs for t in _eval_rep_targets(run)]


def _fix_attempt_targets(run: Run) -> list[Path]:
    attempt_dir = Path(run.evidence_path).resolve() / f"attempt-{run.attempt_number + 1}"
    targets = [attempt_dir / name for name in _FIX_ATTEMPT_REMOVE]
    session_dir = run.result.get("session_dir")
    if isinstance(session_dir, str):
        session_path = Path(session_dir).resolve()
        default = attempt_dir / "agent-runner-session"
        # A run's recorded session_dir is only ever trusted when it falls under this
        # attempt's own evidence directory; anything else is ignored rather than pruned,
        # since run.result is suite-controlled output, not a verified factory path.
        if session_path != default and (
            session_path == attempt_dir or attempt_dir in session_path.parents
        ):
            targets.append(session_path)
    return targets


def _eval_rep_targets(run: Run) -> list[Path]:
    rep_dir = Path(run.evidence_path).resolve()
    return [rep_dir / name for name in _EVAL_REP_REMOVE]
