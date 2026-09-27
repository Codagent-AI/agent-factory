"""Age-based pruning of terminal claims' attempt evidence, and the idle clock it shares."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.config import LimitsConfig, LocalConfig
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore, Run
from agent_factory.work_kinds.base import WorkKindHandler
from agent_factory.work_kinds.pull_request.cleanup import remove_tree
from agent_factory.work_kinds.pull_request.kinds import registered
from agent_factory.work_kinds.pull_request.sync import pending_sync

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
    # The candidate checkout the suite built; a standalone clone with its own `.git`.
    ".runtime/candidate-worktree",
)

TERMINAL = frozenset({"settled", "cancelled", "superseded"})
# Facts about one terminal period; a claim that becomes active again starts a fresh cycle.
# `paths`, `fly_image`, and `done_observed_at` describe finished work and are kept.
_CYCLE_KEYS = (
    "terminal_observed_at",
    "complete",
    "released_by",
    "last_error",
    "review_observed",
    "retention",
    "results_final",
    "size_estimate",
)


@dataclass
class CleanupBudget:
    """Heavy cleanup work one tick may do, shared by the on-board loop and the sweep."""

    removals: int = 5  # workspace releases and evidence prunes, combined
    registry: int = 5  # claims whose Fly image deletion makes registry calls
    measurements: int = 2  # size estimates

    def take(self, kind: str) -> bool:
        left = cast(int, getattr(self, kind))
        if left <= 0:
            return False
        setattr(self, kind, left - 1)
        return True


def take(budget: CleanupBudget | None, kind: str) -> bool:
    """A missing budget is unlimited, as for callers outside the tick."""
    return budget is None or budget.take(kind)


def observe_terminal(cleanup: dict[str, object], lifecycle: str, now: datetime) -> bool:
    """Record the first terminal observation; on a non-terminal lifecycle, reset the cycle."""
    if lifecycle in TERMINAL:
        if cleanup.get("terminal_observed_at") is None:
            cleanup["terminal_observed_at"] = now.isoformat()
            return True
        return False
    if cleanup.get("terminal_observed_at") is None:
        return False
    for key in _CYCLE_KEYS:
        cleanup.pop(key, None)
    return True


def idle_due(claim: Claim, *, card_done: bool, now: datetime, limits: LimitsConfig) -> bool:
    """Whether a terminal claim has been idle for its lifecycle's period.

    A settled claim whose card is Done is idle-due only while its Done cleanup never ran,
    as when its card was never observed in Review.
    """
    if claim.lifecycle in {"cancelled", "superseded"}:
        days = limits.abandoned_retention_days
    elif claim.lifecycle == "settled":
        if card_done and claim.cleanup.get("complete") is True:
            return False
        days = limits.settled_retention_days
    else:
        return False
    observed = _parse(claim.cleanup.get("terminal_observed_at"))
    return observed is not None and now - observed >= timedelta(days=days)


def machine_recorded(store: ClaimStore, claim_id: str) -> bool:
    """A Fly Machine record, kept until disposal is confirmed, including a quota-hold stop."""
    return any(
        record.get("claim_id") == claim_id
        for record in store.get_settings_by_prefix("runtime", "fly:machine:").values()
    )


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # A value an operator edited by hand without a zone is taken as UTC.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def reconcile(
    store: ClaimStore,
    local: LocalConfig,
    claim: Claim,
    board_status: str,
    now: datetime,
    *,
    handler: WorkKindHandler | None = None,
    on_board: bool = True,
    capture_settled: Callable[[Claim], bool] | None = None,
    budget: CleanupBudget | None = None,
) -> None:
    """Observe Done and terminal lifecycles and prune eligible evidence, both every poll.

    An off-board claim is not currently Done, so its Done observation is cleared.
    """
    cleanup = dict(claim.cleanup)
    changed = _observe_done(cleanup, board_status if on_board else "", now)
    changed = observe_terminal(cleanup, claim.lifecycle, now) or changed
    if changed:
        store.set_cleanup(claim.id, cleanup)
    claim = replace(claim, cleanup=cleanup)
    card_done = on_board and board_status == "Done"
    if not _eligible(store, local, claim, card_done, now, handler, capture_settled):
        return
    if not take(budget, "removals"):
        return
    _prune(store, claim, cleanup, now, handler)


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
    card_done: bool,
    now: datetime,
    handler: WorkKindHandler | None,
    capture_settled: Callable[[Claim], bool] | None,
) -> bool:
    # Cheapest checks first: an already-pruned or not-yet-eligible claim costs no queries.
    # Active, waiting, and blocked claims may still need their evidence.
    cleanup = claim.cleanup
    if claim.lifecycle not in TERMINAL:
        return False
    retention = cleanup.get("retention")
    if isinstance(retention, Mapping) and cast(Mapping[str, object], retention).get("pruned_at"):
        return False
    # Retention waits on the claim's workspace release, whichever path released it.
    if cleanup.get("complete") is not True:
        return False
    done_observed = _parse(cleanup.get("done_observed_at"))
    done_path = (
        card_done
        and done_observed is not None
        and now - done_observed >= timedelta(days=local.limits.evidence_retention_days)
    )
    # A settled claim in Done is judged by the Done path alone, so moving a card to Done
    # always grants the full evidence retention period to what remains.
    idle_path = not (claim.lifecycle == "settled" and card_done) and idle_due(
        claim, card_done=card_done, now=now, limits=local.limits
    )
    if not (done_path or idle_path):
        return False
    runs = store.runs_for_claim(claim.id)
    if any(run.status in NONTERMINAL_RUN_STATUSES for run in runs):
        return False
    if store.pending_events(claim.id) or claim.reporting.get("delivery_failures"):
        return False
    if (
        handler.pending_sync(claim)
        if handler is not None
        else any(pending_sync(store, claim, definition) for definition in registered())
    ):
        return False
    if machine_recorded(store, claim.id):
        return False
    return capture_settled is None or capture_settled(claim)


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
            if path.is_dir() and not path.is_symlink():
                remove_tree(path)
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
        cleanup["size_estimate"] = {"bytes": 0, "measured_at": now.isoformat()}
    cleanup["retention"] = retention
    store.set_cleanup(claim.id, cleanup)


def measure(
    store: ClaimStore,
    claim: Claim,
    handler: WorkKindHandler | None,
    now: datetime,
    budget: CleanupBudget | None,
) -> None:
    """Estimate once the local space a terminal claim still holds, for `status` to read.

    Terminal claims do not grow, so the estimate is never refreshed; a prune zeroes it.
    """
    cleanup = claim.cleanup
    if claim.lifecycle not in TERMINAL or cleanup.get("size_estimate") is not None:
        return
    retention = cleanup.get("retention")
    if isinstance(retention, Mapping) and cast(Mapping[str, object], retention).get("pruned_at"):
        return
    if not take(budget, "measurements"):
        return
    paths = [] if cleanup.get("complete") is True else _release_targets(claim)
    paths.extend(_removal_targets(store, claim, handler))
    size = sum(_tree_size(path) for path in dict.fromkeys(paths))
    current = store.get_claim(claim.id) or claim
    store.set_cleanup(
        claim.id,
        {**current.cleanup, "size_estimate": {"bytes": size, "measured_at": now.isoformat()}},
    )


def _release_targets(claim: Claim) -> list[Path]:
    """Recorded eval worktrees and fix or feature clones, as each kind's release records them."""
    targets: list[Path] = []
    paths = claim.cleanup.get("paths")
    if isinstance(paths, Mapping):
        for record in cast(Mapping[str, object], paths).values():
            path = (
                cast(Mapping[str, object], record).get("path")
                if isinstance(record, Mapping)
                else None
            )
            if isinstance(path, str):
                targets.append(Path(path))
    clones = claim.preparation.get("clones")
    if isinstance(clones, Mapping):
        targets.extend(
            Path(path)
            for path in cast(Mapping[str, object], clones).values()
            if isinstance(path, str)
        )
    return targets


def _tree_size(path: Path) -> int:
    """Bytes under ``path``, never following a symbolic link out of the tree."""
    try:
        if not path.is_dir() or path.is_symlink():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    pending = [str(path)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


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
