"""Removes the bulk that finished attempts leave on disk once nothing can use it.

Retention keeps evidence for days. Clones, installed dependencies, and Runner source snapshots
are rebuilt by the next attempt, so they go as soon as no attempt of the claim can use them.
What a clone holds that git cannot rebuild (validator logs, uncommitted changes) is first
copied into the attempt's evidence, where retention covers it.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
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

# Installed dependencies only: rescore hashes acceptance artifacts elsewhere in the checkout,
# and human review serves `dist`, so nothing else of an eval repetition is removed early.
_CANDIDATE_DEPENDENCIES = (
    ".runtime/candidate-worktree/node_modules",
    ".runtime/candidate-worktree/*/node_modules",
)
_AUDIT_SNAPSHOTS = "attempt-*/audit-*/snapshot/runner-source"
CLONE_VALIDATOR_LOGS = "validator_logs"
CLONE_STATE = "clone-state.patch"
_CLONE_STATE_LIMIT = 5 * 1024 * 1024


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
    targets: list[tuple[Path, Path]] = []
    errors: list[dict[str, str]] = []
    if claim.kind == "eval":
        # An open eval claim may recover a repetition from its checkpoint in the same directory.
        if claim.lifecycle not in TERMINAL_LIFECYCLES:
            return
        for run in pending:
            rep_dir = Path(run.evidence_path)
            for pattern in _CANDIDATE_DEPENDENCIES:
                targets.extend((path, rep_dir) for path in sorted(rep_dir.glob(pattern)))
    else:
        definition = next((item for item in registered() if item.kind == claim.kind), None)
        unit_runs = [run for run in runs if definition and run.unit_key == definition.unit_key]
        for clone in finished_clones(claim, runs, clones_root):
            try:
                _preserve_clone_evidence(clone, unit_runs)
            except OSError as error:
                # Keep the clone until its evidence is safe; retry on the next tick.
                errors.append({"path": str(clone), "error": f"preserve: {error}"})
                continue
            targets.append((clone, clone.parent))
        for run in pending:
            evidence = Path(run.evidence_path)
            targets.extend((path, evidence) for path in sorted(evidence.glob(_AUDIT_SNAPSHOTS)))
    if not pending and not targets and not errors:
        return
    for path, root in dict.fromkeys(targets):
        try:
            _remove_within(path, root)
        except OSError as error:
            errors.append({"path": str(path), "error": str(error)})
    if not errors:
        record["runs"] = sorted(done | {run.id for run in pending})
        record["slimmed_at"] = now.isoformat()
    elif errors == record.get("errors") and not targets:
        return
    record["errors"] = errors
    fresh = store.get_claim(claim.id) or claim
    store.set_cleanup(claim.id, {**fresh.cleanup, "slimmed": record})


def finished_clones(claim: Claim, runs: list[Run], clones_root: Path) -> list[Path]:
    """Clone directories of attempts whose run exists; every new attempt cuts fresh clones.

    Attempt N's clones are cut before its run is reserved, numbered by the unit's run count,
    so a directory numbered at or past that count may belong to an attempt being prepared.
    """
    definition = next((item for item in registered() if item.kind == claim.kind), None)
    directory = clones_root / safe_name(claim.id)
    if definition is None or not directory.is_dir() or directory.is_symlink():
        return []
    started = len([run for run in runs if run.unit_key == definition.unit_key])
    return sorted(
        entry for entry in directory.iterdir() if entry.name.isdigit() and int(entry.name) < started
    )


def _preserve_clone_evidence(clone: Path, runs: list[Run]) -> None:
    """Copy the target clone's validator logs and uncommitted changes into attempt evidence."""
    attempt = int(clone.name)
    run = next((item for item in runs if item.attempt_number == attempt), None)
    # `runs` holds only the claim's own unit, whose attempt numbers are unique.
    repo = clone / "repo"
    if run is None or not repo.is_dir():
        return
    attempt_dir = Path(run.evidence_path) / f"attempt-{attempt + 1}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    logs = repo / CLONE_VALIDATOR_LOGS
    copied = attempt_dir / CLONE_VALIDATOR_LOGS
    if logs.is_dir() and not logs.is_symlink() and not copied.exists():
        shutil.copytree(logs, copied, symlinks=True)
    state = attempt_dir / CLONE_STATE
    if not state.exists():
        state.write_text(_clone_state(repo), encoding="utf-8")


def _clone_state(repo: Path) -> str:
    sections: list[str] = []
    for arguments in (["status", "--porcelain=v1", "--branch"], ["diff", "HEAD"]):
        try:
            completed = subprocess.run(
                ["git", "-C", str(repo), *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            output = completed.stdout if completed.returncode == 0 else completed.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            output = f"git could not run: {error}\n"
        sections.append(f"$ git {' '.join(arguments)}\n{output[:_CLONE_STATE_LIMIT]}")
    return "\n".join(sections)


def _remove_within(path: Path, root: Path) -> None:
    """Remove `path` only when its real parent lies inside `root`, so links cannot redirect it."""
    real_root = root.resolve()
    parent = path.parent.resolve()
    if parent != real_root and real_root not in parent.parents:
        raise OSError(f"refusing to remove {path}: it resolves outside {real_root}")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        remove_tree(str(path))
