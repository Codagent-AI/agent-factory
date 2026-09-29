"""Delete only a terminal eval claim's uniquely owned registry manifests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import cast

from agent_factory.fly.api import DIGEST_PATTERN, FlyApiError, FlyMachinesClient
from agent_factory.store import NONTERMINAL_RUN_STATUSES, TERMINAL_LIFECYCLES, Claim, ClaimStore
from agent_factory.work_kinds.base import mapping


def _resolve(client: FlyMachinesClient, image: str) -> str | None:
    try:
        return client.resolve_manifest(image)
    except FlyApiError as error:
        if error.status == 404:
            return None
        raise


def image_deleted(record: object) -> bool:
    return bool(mapping(record).get("deleted_at"))


def ownership_skip(
    digest: str, current: str | None, tags: Mapping[str, str], claim_tag: str
) -> str | None:
    if current != digest:
        return (
            f"claim tag now points to {current}"
            if current is not None
            else "untagged; ownership cannot be proven"
        )
    other = next((tag for tag, value in tags.items() if tag != claim_tag and value == digest), None)
    return f"shared with {other}" if other is not None else None


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
    registry = dict(mapping(claim.cleanup.get("registry")))
    if all(image_deleted(registry.get(digest)) for digest in images):
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
        if image_deleted(registry.get(digest)):
            continue
        result: dict[str, object] = {"repository": repository, "tag": tag, "digest": digest}
        try:
            if client is None:
                raise RuntimeError("no Fly configuration to delete registry image")
            if repository.removeprefix("registry.fly.io/") != client.app:
                raise RuntimeError("recorded image is outside the configured Fly app")
            if not DIGEST_PATTERN.fullmatch(digest):
                raise RuntimeError("recorded image digest is invalid")
            current = _resolve(client, f"{repository}:{tag}")
            tags: Mapping[str, str] = {}
            if current == digest:
                if repository not in cache:
                    cache[repository] = {
                        other: resolved
                        for other in client.list_tags(repository)
                        if (resolved := _resolve(client, f"{repository}:{other}")) is not None
                    }
                tags = cache[repository]
                # Listing can take time; the claim tag must still prove ownership.
                current = _resolve(client, f"{repository}:{tag}")
            skip = ownership_skip(digest, current, tags, tag)
            if current == digest and skip:
                result["skipped"] = skip
            elif current == digest:
                deleted = client.delete_manifest(repository, digest)
                if deleted or (
                    _resolve(client, f"{repository}:{tag}") is None
                    and _resolve(client, f"{repository}@{digest}") is None
                ):
                    result["deleted_at"] = now.isoformat()
                    cache.pop(repository, None)
                else:
                    result["error"] = "delete returned not-found but the image still resolves"
            elif _resolve(client, f"{repository}@{digest}") is None:
                result["deleted_at"] = now.isoformat()
            else:
                assert skip is not None
                result["skipped"] = skip
        except (FlyApiError, OSError, RuntimeError, ValueError) as error:
            result["error"] = str(error)
            result["at"] = now.isoformat()
        registry[digest] = result
        current_claim = store.get_claim(claim.id) or claim
        store.set_cleanup(claim.id, {**current_claim.cleanup, "registry": registry})
