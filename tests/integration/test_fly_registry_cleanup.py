"""INT-005: registry deletion requires current and exclusive claim ownership."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from agent_factory.fly.api import FlyMachinesClient
from agent_factory.fly.registry_cleanup import reconcile_claim_image
from agent_factory.store import ClaimDraft, ClaimStore
from tests.fixtures.fly.api import FakeMachinesApi


def _claim(
    store: ClaimStore, tmp_path: Path, digest: str, *, terminal: bool = True
) -> tuple[str, str]:
    claim = store.create_claim(ClaimDraft("example/repo", 1, "I1", "P1", "eval", "fp", {}))
    tag = f"claim-{claim.id[:12]}"
    run = store.reserve_run(
        claim.id, "rep-1", lane="low", reason="initial", evidence_path=str(tmp_path)
    )
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
        reconcile_claim_image(store, claim, client, now + timedelta(minutes=1))
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
        reconcile_claim_image(store, claim, client, now + timedelta(days=1))
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
        run = store.reserve_run(
            claim_id, "rep-2", lane="low", reason="initial", evidence_path=str(tmp_path)
        )
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
        reconcile_claim_image(store, saved, client, datetime.now(UTC) + timedelta(days=1))
        saved = store.get_claim(claim_id)
        assert saved is not None
        records = cast(dict[str, dict[str, object]], saved.cleanup["registry"])
        assert "untagged" in str(records[older]["skipped"])
        assert older in api.registry_manifests


@pytest.mark.parametrize("scripted,deleted", [(404, False), ("404-gone", True)])
def test_delete_404_requires_the_digest_to_be_gone(
    tmp_path: Path, scripted: int | str, deleted: bool
) -> None:
    digest = "sha256:" + "7" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        claim_id, tag = _claim(store, tmp_path, digest)
        api.registry_enabled = True
        api.registry_tags = {tag: digest}
        api.registry_delete_failures.append(scripted)
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        reconcile_claim_image(store, store.get_claim(claim_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        saved = store.get_claim(claim_id)
        assert saved is not None
        record = cast(dict[str, dict[str, object]], saved.cleanup["registry"])[digest]
        assert bool(record.get("deleted_at")) is deleted
        assert bool(record.get("error")) is not deleted


def test_registry_gates_no_build_and_reopened_store_idempotence(tmp_path: Path) -> None:
    digest = "sha256:" + "8" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    db = tmp_path / "state.sqlite3"
    with closing(ClaimStore(db)) as store, FakeMachinesApi() as api:
        api.registry_enabled = True
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        empty = store.create_claim(ClaimDraft("example/repo", 2, "I2", "P2", "eval", "fp", {}))
        store.set_claim_lifecycle(empty.id, "settled", {})
        reconcile_claim_image(store, empty, client, datetime.now(UTC))
        assert not api.requests and "registry" not in store.get_claim(empty.id).cleanup  # type: ignore[union-attr]
        claim_id, tag = _claim(store, tmp_path, digest)
        run = store.reserve_run(
            claim_id, "rep-2", lane="low", reason="retry", evidence_path=str(tmp_path)
        )
        store.update_progress(
            run.id,
            {"image_build": {"repository": "registry.fly.io/app", "tag": tag, "digest": digest}},
        )
        store.finish_run(run.id, execution_status="completed", result={})
        store.set_claim_lifecycle(claim_id, "settled", {})
        api.registry_tags = {tag: digest, "base": "sha256:" + "9" * 64}
        reconcile_claim_image(store, store.get_claim(claim_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        deletes = [r for r in api.requests if r["method"] == "DELETE"]
        assert len(deletes) == 1
        before = len(api.requests)
    with closing(ClaimStore(db)) as reopened, FakeMachinesApi() as api:
        api.registry_enabled = True
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        saved = reopened.get_claim(claim_id)
        assert saved is not None
        reconcile_claim_image(reopened, saved, client, datetime.now(UTC))
        assert not api.requests
    assert before > 0


def test_registry_held_by_waiting_claim_or_failed_machine_record(tmp_path: Path) -> None:
    digest = "sha256:" + "6" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        api.registry_enabled = True
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        waiting_id, waiting_tag = _claim(store, tmp_path, digest, terminal=False)
        run = store.runs_for_claim(waiting_id)[0]
        store.set_setting("runtime", f"fly:machine:{run.id}", {"machine_id": "stopped"})
        api.registry_tags = {waiting_tag: digest}
        reconcile_claim_image(store, store.get_claim(waiting_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        assert not api.requests
        held_id, held_tag = _claim(store, tmp_path, "sha256:" + "5" * 64)
        api.registry_tags[held_tag] = "sha256:" + "5" * 64
        held_run = store.runs_for_claim(held_id)[0]
        store.set_setting("runtime", "fly:cleanup-failed", {"machines": [{"run_id": held_run.id}]})
        reconcile_claim_image(store, store.get_claim(held_id), client, datetime.now(UTC))  # type: ignore[arg-type]
        assert not api.requests


def test_superseded_claim_rechecks_shared_tag_on_next_poll(tmp_path: Path) -> None:
    old_digest = "sha256:" + "3" * 64
    new_digest = "sha256:" + "4" * 64
    token = tmp_path / "token"
    token.write_text("secret")
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store, FakeMachinesApi() as api:
        old_id, old_tag = _claim(store, tmp_path, old_digest)
        store.set_claim_lifecycle(old_id, "superseded", {})
        _new_id, new_tag = _claim(store, tmp_path, new_digest, terminal=False)
        api.registry_enabled = True
        api.registry_tags = {
            old_tag: old_digest,
            new_tag: new_digest,
            "deployment-one": old_digest,
        }
        client = FlyMachinesClient(
            "app", token, base_url=api.base_url, registry_base_url=api.base_url
        )
        now = datetime.now(UTC)
        reconcile_claim_image(store, store.get_claim(old_id), client, now)  # type: ignore[arg-type]
        saved = store.get_claim(old_id)
        assert saved is not None
        record = cast(dict[str, dict[str, object]], saved.cleanup["registry"])[old_digest]
        assert "shared" in str(record["skipped"])
        assert api.registry_tags[new_tag] == new_digest
        reconcile_claim_image(store, saved, client, now + timedelta(hours=1))
        assert cast(dict[str, dict[str, object]], store.get_claim(old_id).cleanup["registry"])[  # type: ignore[union-attr]
            old_digest
        ]["skipped"]
        assert not any(r["method"] == "DELETE" for r in api.requests)
        del api.registry_tags["deployment-one"]
        reconcile_claim_image(store, store.get_claim(old_id), client, now + timedelta(hours=2))  # type: ignore[arg-type]
        assert old_tag not in api.registry_tags
        assert api.registry_tags[new_tag] == new_digest
        assert cast(dict[str, dict[str, object]], store.get_claim(old_id).cleanup["registry"])[  # type: ignore[union-attr]
            old_digest
        ]["deleted_at"]
