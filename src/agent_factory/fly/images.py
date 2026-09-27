"""Deletes an eval claim's Fly registry image once no work can use it.

Kept apart from ``FlyMachineBackend`` so a registry failure can never delay, fail, or
change Machine disposal or reconciliation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig
from agent_factory.fly.api import FlyApiError, FlyMachinesClient
from agent_factory.fly.transport import image_repository
from agent_factory.retention import TERMINAL, CleanupBudget, machine_recorded, take
from agent_factory.store import NONTERMINAL_RUN_STATUSES, Claim, ClaimStore

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_BACKOFF = timedelta(minutes=5)
_BACKOFF_CAP = timedelta(hours=6)
# Authentication, rate limiting, and server errors may clear on their own; a network
# error or timeout carries no status.
_TRANSIENT = frozenset({401, 403, 429})


def reconcile_claim_image(
    store: ClaimStore,
    claim: Claim,
    local: LocalConfig,
    *,
    client_factory: Callable[[], FlyMachinesClient],
    now: datetime,
    budget: CleanupBudget | None = None,
) -> None:
    """Verify each recorded digest against the claim's own tag, then delete it by digest."""
    if claim.kind != "eval" or local.fly is None:
        return
    saved = claim.cleanup.get("fly_image")
    state = dict(cast(Mapping[str, object], saved)) if isinstance(saved, Mapping) else {}
    if state.get("state") in {"complete", "none"} or state.get("persistent") is True:
        return
    # Judged before the records: a run still building its image has not recorded it yet.
    if claim.lifecycle not in TERMINAL:
        return
    if any(run.status in NONTERMINAL_RUN_STATUSES for run in store.runs_for_claim(claim.id)):
        return
    if machine_recorded(store, claim.id):
        return
    tag = f"claim-{claim.id[:12]}"
    records = _records(store, claim, tag)
    if not records:
        _save(store, claim.id, {"state": "none"})
        return
    retry_after = _moment(state.get("retry_after"))
    if retry_after is not None and now < retry_after:
        return
    if not take(budget, "registry"):
        return
    expected = image_repository(local.fly.image)
    digests = {
        str(key): str(value)
        for key, value in cast(Mapping[str, object], state.get("digests") or {}).items()
    }
    errors: list[str] = []
    persistent = False
    client: FlyMachinesClient | None = None
    for digest, repository in records.items():
        if digests.get(digest) in {"deleted", "gone"}:
            continue
        if repository != expected:
            digests[digest] = "mismatch"
            errors.append(f"{digest} was recorded for {repository}, not {expected}")
            persistent = True
            continue
        client = client or client_factory()
        try:
            digests[digest] = _delete(client, repository, tag, digest)
        except FlyApiError as error:
            unsupported = error.status == 405 or error.reason == "UNSUPPORTED"
            digests[digest] = "unsupported" if unsupported else "error"
            errors.append(str(error))
            persistent = persistent or unsupported or not _transient(error)
            continue
        if digests[digest] == "mismatch":
            errors.append(f"{repository}:{tag} no longer resolves to the recorded {digest}")
            persistent = True
    if not errors:
        _save(store, claim.id, {"state": "complete", "digests": digests, "error": None})
        return
    result = failure(state, "; ".join(errors), now, persistent=persistent)
    _save(store, claim.id, {**result, "digests": digests})


def failure(
    state: Mapping[str, object], error: str, now: datetime, *, persistent: bool = False
) -> dict[str, object]:
    """A failed state; a retryable one waits 5 minutes, doubling up to 6 hours."""
    previous = state.get("attempts")
    attempts = (previous if isinstance(previous, int) else 0) + 1
    result: dict[str, object] = {
        **state,
        "state": "failed",
        "error": error,
        "persistent": persistent,
        "attempts": attempts,
    }
    result.pop("retry_after", None)
    if not persistent:
        delay = min(_BACKOFF * 2 ** (attempts - 1), _BACKOFF_CAP)
        result["retry_after"] = (now + delay).isoformat()
    return result


def _delete(client: FlyMachinesClient, repository: str, tag: str, digest: str) -> str:
    """Deleting by digest removes every tag on it, so the claim's own tag must match first."""
    try:
        resolved = client.resolve_manifest(f"{repository}:{tag}")
    except FlyApiError as error:
        if error.status == 404:
            return "gone"
        raise
    if resolved != digest:
        return "mismatch"
    client.delete_manifest(repository, digest)
    return "deleted"


def _moment(value: object) -> datetime | None:
    """A recorded time; an operator's hand-edited value without a zone is taken as UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _transient(error: FlyApiError) -> bool:
    return error.status is None or error.status in _TRANSIENT or error.status >= 500


def _records(store: ClaimStore, claim: Claim, tag: str) -> dict[str, str]:
    """The claim's recorded digests for its own tag, by digest, with their repository."""
    records: dict[str, str] = {}
    for run in store.runs_for_claim(claim.id):
        record = run.progress.get("image_build")
        if not isinstance(record, Mapping):
            try:
                record = json.loads(
                    (Path(run.evidence_path) / ".factory" / "image-build.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, ValueError):
                continue
        if not isinstance(record, Mapping):
            continue
        values = cast(Mapping[str, object], record)
        digest, repository = values.get("digest"), values.get("repository")
        if (
            isinstance(digest, str)
            and _DIGEST.fullmatch(digest)
            and values.get("tag") == tag
            and isinstance(repository, str)
        ):
            records.setdefault(digest, repository)
    return records


def _save(store: ClaimStore, claim_id: str, state: Mapping[str, object]) -> None:
    current = store.get_claim(claim_id)
    if current is not None:
        store.set_cleanup(claim_id, {**current.cleanup, "fly_image": dict(state)})
