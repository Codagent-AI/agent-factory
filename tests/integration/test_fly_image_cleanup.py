"""INT-006: a claim's Fly image is deleted once no work can use it, apart from disposal."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from agent_factory.config import LocalConfig
from agent_factory.fly.api import FlyMachinesClient
from agent_factory.fly.images import reconcile_claim_image
from agent_factory.retention import CleanupBudget
from agent_factory.store import Claim, ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler
from tests.fixtures.fly.api import FakeMachinesApi
from tests.integration.test_fly_disposal import (
    Cycle,
    _reviewable,  # pyright: ignore[reportPrivateUsage]
)

BASE = "sha256:" + "b" * 64
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _digest(n: int) -> str:
    return "sha256:" + f"{n:x}" * 64


class Registry:
    def __init__(self, tmp_path: Path, api: FakeMachinesApi) -> None:
        self.api = api
        token = tmp_path / "token"
        token.write_text("deploy-token\n", encoding="utf-8")
        token.chmod(0o600)
        self.local = LocalConfig.from_toml(
            f'''\
shared_config = "{tmp_path / "shared.toml"}"
storage_root = "{tmp_path / "factory"}"
[repositories]
agent_evals = "{tmp_path / "evals"}"
agent_runner = "{tmp_path / "runner"}"
agent_skills = "{tmp_path / "skills"}"
[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 0
[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 600
total_seconds = 3600
codex_reset_fallback_seconds = 18000
[credentials]
github_app_key = "{tmp_path / "key.pem"}"
suite_environment = "{tmp_path / "suite.env"}"
[eval]
execution = "fly"
[fly]
app = "app"
image = "registry.fly.io/app:base"
token_file = "{token}"
'''
        )
        self.store = ClaimStore(tmp_path / "state.sqlite3")
        self.artifacts = tmp_path / "artifacts"
        self.api.registry = {"app": {"base": BASE}}
        self.claims = 0

    def client(self) -> FlyMachinesClient:
        assert self.local.fly is not None
        return FlyMachinesClient(
            self.local.fly.app,
            self.local.fly.token_file,
            base_url=self.api.base_url,
            registry_base_url=self.api.base_url,
        )

    def claim(
        self,
        lifecycle: str = "settled",
        *,
        digest: str | None = None,
        in_progress: bool = True,
        repository: str = "registry.fly.io/app",
        pushed: str | None = None,
    ) -> Claim:
        """An eval claim whose build recorded ``digest``; the registry holds ``pushed``."""
        self.claims += 1
        claim = self.store.create_claim(
            ClaimDraft("example/evals", self.claims, "I", f"P{self.claims}", "eval", "fp", {})
        )
        tag = f"claim-{claim.id[:12]}"
        artifact = self.artifacts / f"{claim.id}-rep-1"
        (artifact / ".factory").mkdir(parents=True)
        run = self.store.reserve_run(
            claim.id, "rep-1", reason="initial", evidence_path=str(artifact)
        )
        if digest is not None:
            record = {"repository": repository, "tag": tag, "digest": digest}
            if in_progress:
                self.store.update_progress(run.id, {"image_build": record})
            else:
                (artifact / ".factory" / "image-build.json").write_text(json.dumps(record))
            self.api.registry["app"][tag] = pushed or digest
        if lifecycle != "active":
            self.store.finish_run(run.id, execution_status="completed", result={})
            self.store.set_claim_lifecycle(claim.id, lifecycle, {"verdict": "pending-human-review"})
        return self.get(claim.id)

    def get(self, claim_id: str) -> Claim:
        claim = self.store.get_claim(claim_id)
        assert claim is not None
        return claim

    def reconcile(
        self, claim: Claim, *, now: datetime = NOW, budget: CleanupBudget | None = None
    ) -> dict[str, object]:
        reconcile_claim_image(
            self.store,
            self.get(claim.id),
            self.local,
            client_factory=self.client,
            now=now,
            budget=budget,
        )
        return cast(dict[str, object], self.get(claim.id).cleanup.get("fly_image", {}))

    def deletes(self) -> list[str]:
        return [str(r["path"]) for r in self.api.requests if r["method"] == "DELETE"]

    def registry_calls(self) -> int:
        return sum(1 for r in self.api.requests if str(r["path"]).startswith("/v2/"))

    def tag(self, claim: Claim) -> str:
        return f"claim-{claim.id[:12]}"


@pytest.fixture
def registry(tmp_path: Path) -> Iterator[Registry]:
    with FakeMachinesApi() as api:
        harness = Registry(tmp_path, api)
        try:
            yield harness
        finally:
            harness.store.close()


def test_image_is_kept_while_the_claim_is_active(registry: Registry) -> None:
    claim = registry.claim("active", digest=_digest(1))

    assert registry.reconcile(claim) == {}
    assert registry.registry_calls() == 0


@pytest.mark.parametrize("decision", ["destroy", "stop"])
def test_image_is_kept_while_a_machine_is_recorded(registry: Registry, decision: str) -> None:
    claim = registry.claim(digest=_digest(1))
    registry.store.set_setting(
        "runtime",
        "fly:machine:run-1",
        {"claim_id": claim.id, "machine_id": "m1", "decision": decision},
    )

    assert registry.reconcile(claim) == {}
    assert registry.registry_calls() == 0

    registry.store.clear_setting("runtime", "fly:machine:run-1")
    state = registry.reconcile(claim)

    assert state["state"] == "complete"
    assert state["digests"] == {_digest(1): "deleted"}
    assert registry.deletes() == [f"/v2/app/manifests/{_digest(1)}"]
    assert registry.tag(claim) not in registry.api.registry["app"]
    assert registry.api.registry["app"]["base"] == BASE

    calls = registry.registry_calls()
    registry.reconcile(claim)
    assert registry.registry_calls() == calls


@pytest.mark.parametrize("lifecycle", ["cancelled", "superseded"])
def test_an_abandoned_claims_image_is_deleted_at_once(registry: Registry, lifecycle: str) -> None:
    claim = registry.claim(lifecycle, digest=_digest(2), in_progress=False)

    assert registry.reconcile(claim)["state"] == "complete"
    assert registry.deletes() == [f"/v2/app/manifests/{_digest(2)}"]


def test_a_missing_tag_counts_as_already_deleted(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1))
    del registry.api.registry["app"][registry.tag(claim)]

    state = registry.reconcile(claim)

    assert state["state"] == "complete"
    assert state["digests"] == {_digest(1): "gone"}
    assert registry.deletes() == []


def test_a_tag_pointing_elsewhere_is_a_persistent_failure(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1), pushed=_digest(3))

    state = registry.reconcile(claim)

    assert state["state"] == "failed"
    assert state["persistent"] is True
    assert state["digests"] == {_digest(1): "mismatch"}
    assert registry.deletes() == []
    calls = registry.registry_calls()
    registry.reconcile(claim, now=NOW + timedelta(days=30))
    assert registry.registry_calls() == calls


def test_an_unsupported_deletion_is_recorded_once(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1))
    registry.api.manifest_delete_failures.append((405, {"errors": [{"code": "UNSUPPORTED"}]}))

    state = registry.reconcile(claim)

    assert state["state"] == "failed"
    assert state["persistent"] is True
    assert state["digests"] == {_digest(1): "unsupported"}
    assert "UNSUPPORTED" in str(state["error"])
    calls = registry.registry_calls()
    registry.reconcile(claim, now=NOW + timedelta(days=30))
    assert registry.registry_calls() == calls


def test_a_server_error_is_retried_after_a_backoff(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1))
    registry.api.manifest_delete_failures.extend([500, 503])

    first = registry.reconcile(claim)
    assert first["state"] == "failed"
    assert first["persistent"] is False
    assert first["attempts"] == 1
    assert first["retry_after"] == (NOW + timedelta(minutes=5)).isoformat()

    calls = registry.registry_calls()
    registry.reconcile(claim, now=NOW + timedelta(minutes=4))
    assert registry.registry_calls() == calls

    second = registry.reconcile(claim, now=NOW + timedelta(minutes=5))
    assert second["attempts"] == 2
    assert second["retry_after"] == (NOW + timedelta(minutes=15)).isoformat()

    third = registry.reconcile(claim, now=NOW + timedelta(minutes=15))
    assert third["state"] == "complete"
    assert third["error"] is None
    assert registry.tag(claim) not in registry.api.registry["app"]


def test_backoff_is_capped_at_six_hours(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1))
    registry.api.manifest_get_failures.append(503)
    registry.store.set_cleanup(
        claim.id, {"fly_image": {"state": "failed", "attempts": 9, "persistent": False}}
    )

    state = registry.reconcile(claim)

    assert state["attempts"] == 10
    assert state["retry_after"] == (NOW + timedelta(hours=6)).isoformat()


def test_a_record_for_another_repository_is_never_deleted(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1), repository="registry.fly.io/elsewhere")

    state = registry.reconcile(claim)

    assert state["state"] == "failed"
    assert state["persistent"] is True
    assert registry.registry_calls() == 0


def test_a_claim_without_a_recorded_digest_needs_nothing(registry: Registry) -> None:
    claim = registry.claim()

    assert registry.reconcile(claim) == {"state": "none"}
    assert registry.registry_calls() == 0


def test_malformed_digests_and_other_tags_are_ignored(registry: Registry) -> None:
    claim = registry.claim()
    run = registry.store.runs_for_claim(claim.id)[0]
    record = {"repository": "registry.fly.io/app", "tag": "base", "digest": BASE}
    (Path(run.evidence_path) / ".factory" / "image-build.json").write_text(json.dumps(record))

    assert registry.reconcile(claim) == {"state": "none"}
    assert registry.api.registry["app"]["base"] == BASE


def test_at_most_the_budgets_claims_make_registry_calls(registry: Registry) -> None:
    claims = [registry.claim(digest=_digest(n)) for n in range(1, 7)]
    budget = CleanupBudget()

    states = [registry.reconcile(claim, budget=budget) for claim in claims]

    assert [state.get("state") for state in states] == ["complete"] * 5 + [None]
    assert budget.registry == 0
    assert registry.reconcile(claims[-1], budget=CleanupBudget())["state"] == "complete"


def test_nothing_happens_without_fly_or_for_other_kinds(registry: Registry) -> None:
    claim = registry.claim(digest=_digest(1))
    fix = registry.store.create_claim(ClaimDraft("example/work", 9, "I9", "P9", "fix", "fp", {}))
    registry.store.set_claim_lifecycle(fix.id, "settled", {})
    unconfigured = LocalConfig.from_toml(_without_fly(registry.local))

    reconcile_claim_image(
        registry.store, claim, unconfigured, client_factory=registry.client, now=NOW
    )
    assert registry.reconcile(fix) == {}
    assert registry.get(claim.id).cleanup.get("fly_image") is None
    assert registry.registry_calls() == 0


def _without_fly(local: LocalConfig) -> str:
    return f'''\
shared_config = "{local.shared_config}"
storage_root = "{local.storage_root}"
[repositories]
agent_evals = "{local.repositories.agent_evals}"
agent_runner = "{local.repositories.agent_runner}"
agent_skills = "{local.repositories.agent_skills}"
[schedule]
timezone = "UTC"
poll_seconds = 60
start_hour = 0
stop_hour = 0
[limits]
minimum_free_gib = 0
inactivity_seconds = 60
execution_seconds = 600
total_seconds = 3600
codex_reset_fallback_seconds = 18000
[credentials]
github_app_key = "{local.credentials.github_app_key}"
suite_environment = "{local.credentials.suite_environment}"
'''


def test_a_failing_registry_never_affects_machine_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with FakeMachinesApi() as api:
        monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", api.base_url)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
        cycle = Cycle(tmp_path, api)
        try:
            imaged = cycle.claim()
            imaged_run, _ = cycle.finished_run(imaged, "rep-1", _reviewable())
            tag = f"claim-{imaged.id[:12]}"
            cycle.store.set_setting("consumed-results", imaged_run.id, {"complete": True})
            record = {"repository": "registry.fly.io/app", "tag": tag, "digest": _digest(1)}
            factory = Path(imaged_run.evidence_path) / ".factory"
            (factory / "image-build.json").write_text(json.dumps(record))
            cycle.store.set_claim_lifecycle(imaged.id, "settled", {})
            api.registry = {"app": {tag: _digest(1), "base": BASE}}
            api.manifest_get_failures.extend([500] * 5)
            api.manifest_delete_failures.extend([500] * 5)
            assert cycle.local.fly is not None
            fly = cycle.local.fly
            factory_client: Callable[[], FlyMachinesClient] = lambda: FlyMachinesClient(  # noqa: E731
                fly.app, fly.token_file, base_url=api.base_url, registry_base_url=api.base_url
            )
            reconcile_claim_image(
                cycle.store,
                cast(Claim, cycle.store.get_claim(imaged.id)),
                cycle.local,
                client_factory=factory_client,
                now=NOW,
            )
            saved = cast(Claim, cycle.store.get_claim(imaged.id))
            assert cast(dict[str, object], saved.cleanup["fly_image"])["state"] == "failed"

            other = cycle.claim()
            run, machine_id = cycle.finished_run(other, "rep-1", _reviewable())
            cycle.consume()

            assert machine_id not in api.machines
            assert cycle.record(run) is None
        finally:
            cycle.close()


def _eval_handler(registry: Registry) -> EvalHandler:
    handler = EvalHandler(
        EvalDefaults("main", "main", {}, False, 1, execution="fly"), local=registry.local
    )
    handler.attach_store(registry.store)
    return handler


def test_the_eval_handlers_cleanup_deletes_the_image_on_every_visit(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FACTORY_FLY_API_URL", registry.api.base_url)
    monkeypatch.setenv("AGENT_FACTORY_FLY_REGISTRY_URL", registry.api.base_url)
    claim = registry.claim("superseded", digest=_digest(1))

    _eval_handler(registry).cleanup(claim, board_status="", on_board=False)

    state = cast(dict[str, object], registry.get(claim.id).cleanup["fly_image"])
    assert state["state"] == "complete"
    assert registry.tag(claim) not in registry.api.registry["app"]


def test_the_eval_handler_records_an_unexpected_image_error_and_carries_on(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FACTORY_FLY_REGISTRY_URL", "http://registry.example.test")
    claim = registry.claim("settled", digest=_digest(1))

    _eval_handler(registry).cleanup(claim, board_status="Review")

    state = cast(dict[str, object], registry.get(claim.id).cleanup["fly_image"])
    assert state["state"] == "failed"
    assert "HTTPS" in str(state["error"])
    assert registry.api.registry["app"][registry.tag(claim)] == _digest(1)


def test_an_image_still_building_is_deleted_once_the_claim_settles(registry: Registry) -> None:
    """A tick during the remote build sees no record yet; that must not settle as `none`."""
    claim = registry.claim("active")

    assert registry.reconcile(claim) == {}

    run = registry.store.runs_for_claim(claim.id)[0]
    record = {"repository": "registry.fly.io/app", "tag": registry.tag(claim), "digest": _digest(1)}
    registry.store.update_progress(run.id, {"image_build": record})
    registry.api.registry["app"][registry.tag(claim)] = _digest(1)
    registry.store.finish_run(run.id, execution_status="completed", result={})
    registry.store.set_claim_lifecycle(claim.id, "settled", {})

    assert registry.reconcile(claim)["state"] == "complete"


def test_an_unexpected_image_error_backs_off(
    registry: Registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_FACTORY_FLY_REGISTRY_URL", "http://registry.example.test")
    claim = registry.claim("settled", digest=_digest(1))
    handler = _eval_handler(registry)

    handler.cleanup(claim, board_status="Review")
    first = cast(dict[str, object], registry.get(claim.id).cleanup["fly_image"])
    handler.cleanup(registry.get(claim.id), board_status="Review")
    second = cast(dict[str, object], registry.get(claim.id).cleanup["fly_image"])

    assert first["attempts"] == 1
    assert isinstance(first["retry_after"], str)
    assert second == first, "a retry waits for its backoff"
