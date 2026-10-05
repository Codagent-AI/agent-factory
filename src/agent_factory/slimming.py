"""Removes the regenerable bulk that finished attempts leave on disk.

Retention keeps evidence for days; clones, candidate checkouts, and source snapshots can be
rebuilt from git at any time, so they go as soon as no attempt of the claim can use them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig
from agent_factory.store import (
    NONTERMINAL_RUN_STATUSES,
    TERMINAL_LIFECYCLES,
    Claim,
    ClaimStore,
    Run,
)
from agent_factory.work_kinds.pull_request.cleanup import remove_tree
from agent_factory.work_kinds.pull_request.kinds import registered
from agent_factory.work_kinds.pull_request.workspace import safe_name

logger = logging.getLogger(__name__)

# Human review serves the scored build from `dist`; the rest of the checkout is rebuilt on demand.
_CANDIDATE_WORKTREE = ".runtime/candidate-worktree"
_CANDIDATE_KEEP = frozenset({"dist"})
_EVAL_REMOVE = (".runtime/agent-runner-projects",)
_AUDIT_SNAPSHOTS = "attempt-*/audit-*/snapshot/runner-source"


def sweep(store: ClaimStore, local: LocalConfig, now: datetime) -> None:
    clones_root = local.storage_root.expanduser().resolve() / "clones"
    for claim in store.all_claims():
        # Released and pruned claims hold nothing left to slim; a new run clears the flag.
        if claim.cleanup.get("sweep_complete") is True:
            continue
        try:
            slim_claim(store, claim, clones_root, now)
        except Exception:
            logger.exception("slimming failed for claim %s", claim.id)


def slim_claim(store: ClaimStore, claim: Claim, clones_root: Path, now: datetime) -> None:
    runs = store.runs_for_claim(claim.id)
    if any(run.status in NONTERMINAL_RUN_STATUSES for run in runs):
        return
    record = dict(cast(Mapping[str, object], claim.cleanup.get("slimmed") or {}))
    done = {item for item in cast(list[object], record.get("runs") or []) if isinstance(item, str)}
    pending = [run for run in runs if run.id not in done]
    targets: list[Path] = []
    errors: list[dict[str, str]] = []
    if claim.kind == "eval":
        # An open eval claim may recover a repetition from its checkpoint in the same directory.
        if claim.lifecycle not in TERMINAL_LIFECYCLES:
            return
        for run in pending:
            try:
                targets.extend(eval_targets(run))
            except OSError as error:
                errors.append({"path": run.evidence_path, "error": str(error)})
    else:
        targets.extend(finished_clones(claim, runs, clones_root))
        for run in pending:
            targets.extend(sorted(Path(run.evidence_path).resolve().glob(_AUDIT_SNAPSHOTS)))
    if not pending and not targets:
        return
    for path in dict.fromkeys(targets):
        try:
            _remove(path)
        except OSError as error:
            errors.append({"path": str(path), "error": str(error)})
    fresh = store.get_claim(claim.id) or claim
    if not errors:
        record["runs"] = sorted(done | {run.id for run in pending})
        record["slimmed_at"] = now.isoformat()
    record["errors"] = errors
    store.set_cleanup(claim.id, {**fresh.cleanup, "slimmed": record})


def finished_clones(claim: Claim, runs: list[Run], clones_root: Path) -> list[Path]:
    """Clone directories of attempts whose run exists; every new attempt cuts fresh clones.

    Attempt N's clones are cut before its run is reserved, numbered by the unit's run count,
    so a directory numbered at or past that count may belong to an attempt being prepared.
    """
    definition = next((item for item in registered() if item.kind == claim.kind), None)
    directory = clones_root / safe_name(claim.id)
    if definition is None or not directory.is_dir():
        return []
    started = len([run for run in runs if run.unit_key == definition.unit_key])
    return sorted(
        entry for entry in directory.iterdir() if entry.name.isdigit() and int(entry.name) < started
    )


def eval_targets(run: Run) -> list[Path]:
    rep_dir = Path(run.evidence_path).resolve()
    candidate = rep_dir / _CANDIDATE_WORKTREE
    spare = (
        [entry for entry in candidate.iterdir() if entry.name not in _CANDIDATE_KEEP]
        if candidate.is_dir() and not candidate.is_symlink()
        else []
    )
    return [*sorted(spare), *(rep_dir / name for name in _EVAL_REMOVE)]


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        remove_tree(str(path))
