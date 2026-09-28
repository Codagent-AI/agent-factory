"""Delete only a terminal eval claim's uniquely owned registry manifests."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import cast

from agent_factory.fly.api import FlyApiError, FlyMachinesClient
from agent_factory.store import NONTERMINAL_RUN_STATUSES, TERMINAL_LIFECYCLES, Claim, ClaimStore


def _resolve(client: FlyMachinesClient, image: str) -> str | None:
    try:
        return client.resolve_manifest(image)
    except FlyApiError as error:
        if error.status == 404:
            return None
        raise


def reconcile_claim_image(
    store: ClaimStore,
    claim: Claim,
    client: FlyMachinesClient | None,
    now: datetime,
    cache: dict[str, dict[str, str]] | None = None,
) -> None:
    runs = store.runs_for_claim(claim.id)
    images: dict[str, tuple[str, str]] = {}
    for run in runs:
        build = run.progress.get("image_build")
        if not isinstance(build, Mapping):
            continue
        record = cast(Mapping[str, object], build)
        repository, tag, digest = (record.get(key) for key in ("repository", "tag", "digest"))
        if (
            isinstance(repository, str)
            and isinstance(tag, str)
            and isinstance(digest, str)
            and tag == f"claim-{claim.id[:12]}"
        ):
            images[digest] = (repository, tag)
    if not images:
        return
    raw_registry = claim.cleanup.get("registry")
    registry = (
        dict(cast(Mapping[str, object], raw_registry)) if isinstance(raw_registry, Mapping) else {}
    )
    if all(
        isinstance(registry.get(d), Mapping)
        and cast(Mapping[str, object], registry[d]).get("deleted_at")
        for d in images
    ):
        return
    if claim.lifecycle not in TERMINAL_LIFECYCLES or any(
        run.status in NONTERMINAL_RUN_STATUSES for run in runs
    ):
        return
    from agent_factory.terminal import machines_held

    if machines_held(store, claim):
        return
    cache = cache if cache is not None else {}
    for digest, (repository, tag) in images.items():
        prior = registry.get(digest)
        if isinstance(prior, Mapping) and cast(Mapping[str, object], prior).get("deleted_at"):
            continue
        result: dict[str, object] = {"repository": repository, "tag": tag, "digest": digest}
        try:
            if client is None:
                raise RuntimeError("no Fly configuration to delete registry image")
            if repository.removeprefix("registry.fly.io/") != client.app:
                raise RuntimeError("recorded image is outside the configured Fly app")
            if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                raise RuntimeError("recorded image digest is invalid")
            if repository not in cache:
                cache[repository] = {
                    other: resolved
                    for other in client.list_tags(repository)
                    if (resolved := _resolve(client, f"{repository}:{other}")) is not None
                }
            tags = cache[repository]
            current = _resolve(client, f"{repository}:{tag}")
            shared = next(
                (other for other, value in tags.items() if other != tag and value == digest),
                None,
            )
            if shared:
                result["skipped"] = f"shared with {shared}"
            elif current == digest:
                deleted = client.delete_manifest(repository, digest)
                if (
                    deleted
                    or _resolve(client, f"{repository}:{tag}") is None
                    and _resolve(client, f"{repository}@{digest}") is None
                ):
                    result["deleted_at"] = now.isoformat()
                    cache.pop(repository, None)
                else:
                    result["error"] = "delete returned not-found but the image still resolves"
            elif _resolve(client, f"{repository}@{digest}") is None:
                result["deleted_at"] = now.isoformat()
            elif current is not None:
                result["skipped"] = f"claim tag now points to {current}"
            else:
                result["skipped"] = "untagged; ownership cannot be proven"
        except (FlyApiError, OSError, RuntimeError, ValueError) as error:
            result["error"] = str(error)
            result["at"] = now.isoformat()
        registry[digest] = result
        current_claim = store.get_claim(claim.id) or claim
        store.set_cleanup(claim.id, {**current_claim.cleanup, "registry": registry})
