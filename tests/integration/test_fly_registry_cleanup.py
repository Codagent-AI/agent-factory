"""INT-005: registry deletion requires current and exclusive claim ownership."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from agent_factory.fly.api import FlyMachinesClient
from agent_factory.fly.registry_cleanup import reconcile_claim_image
from agent_factory.store import ClaimDraft, ClaimStore
from tests.fixtures.fly.api import FakeMachinesApi


def _claim(
    store: ClaimStore, tmp_path: Path, digest: str, *, terminal: bool = True
) -> tuple[str, str]:
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "eval", "fp", {}))
    tag = f"claim-{claim.id[:12]}"
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path))
    store.update_progress(
        run.id, {"image_build": {"repository": "registry.fly.io/app", "tag": tag, "digest": digest}}
    )
    store.finish_run(run.id, execution_status="completed", result={})
    if terminal:
        store.set_claim_lifecycle(claim.id, "settled", {})
    return claim.id, tag


def test_delete_unique_digest_and_retry_registry_failure(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        claim_id, tag = _claim(store, tmp_path, digest)
        api.registry_enabled = True
        api.registry_tags = {tag: digest, "base": "sha256:" + "b" * 64}
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        api.registry_delete_failures.append(500)
        now = datetime.now(UTC)
        reconcile_claim_image(store, store.get_claim(claim_id), client, now)  # type: ignore[arg-type]
        claim = store.get_claim(claim_id)
        assert claim is not None
        assert "error" in cast(dict[str, dict[str, object]], claim.cleanup["registry"])[digest]
        assert store.get_setting("runtime", "fly:cleanup-failed") is None
        reconcile_claim_image(store, claim, client, now)
        assert tag not in api.registry_tags
        assert "base" in api.registry_tags
        before = len(api.requests)
        reconcile_claim_image(store, store.get_claim(claim_id), client, now)  # type: ignore[arg-type]
        assert len(api.requests) == before


def test_shared_moved_and_held_images_are_not_deleted(tmp_path: Path) -> None:
    digest = "sha256:" + "c" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        claim_id, tag = _claim(store, tmp_path, digest)
        api.registry_enabled = True
        api.registry_tags = {tag: digest, "deployment-1": digest}
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        now = datetime.now(UTC)
        reconcile_claim_image(store, store.get_claim(claim_id), client, now)  # type: ignore[arg-type]
        claim = store.get_claim(claim_id)
        assert claim is not None
        assert "shared" in str(
            cast(dict[str, dict[str, object]], claim.cleanup["registry"])[digest]["skipped"]
        )
        assert api.registry_tags[tag] == digest
        api.registry_tags = {tag: "sha256:" + "d" * 64, "other": digest}
        reconcile_claim_image(store, claim, client, now)
        claim = store.get_claim(claim_id)
        assert claim is not None
        assert "skipped" in cast(dict[str, dict[str, object]], claim.cleanup["registry"])[digest]
        store.set_setting(
            "runtime", f"fly:machine:{store.runs_for_claim(claim_id)[0].id}", {"machine_id": "m"}
        )
        before = len(api.requests)
        reconcile_claim_image(store, claim, client, now)
        assert len(api.requests) == before


def test_registry_missing_tag_and_digest_is_done_without_delete(tmp_path: Path) -> None:
    digest = "sha256:" + "e" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        claim_id, _tag = _claim(store, tmp_path, digest)
        api.registry_enabled = True
        api.registry_tags = {"base": "sha256:" + "b" * 64}
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        reconcile_claim_image(store, store.get_claim(claim_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        saved = store.get_claim(claim_id)
        assert saved is not None
        record = cast(dict[str, dict[str, object]], saved.cleanup["registry"])[digest]
        assert record["deleted_at"]
        assert not any(
            request["method"] == "DELETE" and str(request["path"]).startswith("/v2/")
            for request in api.requests
        )


def test_registry_missing_configuration_and_unreviewed_claim(tmp_path: Path) -> None:
    digest = "sha256:" + "f" * 64
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store:
        claim_id, _tag = _claim(store, tmp_path, digest)
        now = datetime.now(UTC)
        reconcile_claim_image(store, store.get_claim(claim_id), None, now)  # type: ignore[arg-type]
        saved = store.get_claim(claim_id)
        assert saved is not None
        record = cast(dict[str, dict[str, object]], saved.cleanup["registry"])[digest]
        assert "no Fly configuration" in str(record["error"])
        pending_id, _tag = _claim(store, tmp_path, digest, terminal=False)
        reconcile_claim_image(store, store.get_claim(pending_id), None, now)  # type: ignore[arg-type]
        pending = store.get_claim(pending_id)
        assert pending is not None and "registry" not in pending.cleanup


def test_older_build_and_untagged_live_digest_are_visible_skips(tmp_path: Path) -> None:
    older = "sha256:" + "1" * 64
    newer = "sha256:" + "2" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        claim_id, tag = _claim(store, tmp_path, older)
        run = store.reserve_run(claim_id, "rep-2", reason="initial", evidence_path=str(tmp_path))
        store.update_progress(
            run.id,
            {"image_build": {"repository": "registry.fly.io/app", "tag": tag, "digest": newer}},
        )
        store.finish_run(run.id, execution_status="completed", result={})
        store.set_claim_lifecycle(claim_id, "settled", {})
        api.registry_enabled = True
        api.registry_tags = {tag: newer}
        api.registry_manifests = {older, newer}
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        reconcile_claim_image(store, store.get_claim(claim_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        saved = store.get_claim(claim_id)
        assert saved is not None
        records = cast(dict[str, dict[str, object]], saved.cleanup["registry"])
        assert "claim tag now points" in str(records[older]["skipped"])
        assert records[newer]["deleted_at"]
        assert older in api.registry_manifests
        api.registry_tags.clear()
        reconcile_claim_image(store, saved, client, datetime.now(UTC))
        saved = store.get_claim(claim_id)
        assert saved is not None
        records = cast(dict[str, dict[str, object]], saved.cleanup["registry"])
        assert "untagged" in str(records[older]["skipped"])
        assert older in api.registry_manifests
