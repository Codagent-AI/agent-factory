"""Captured outputs from the pre-registry eval implementation.

These fixtures are intentionally immutable after the first commit.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler, parse_request

GOLDENS = Path(__file__).parents[1] / "fixtures" / "eval_inputs_golden"
SHA = "a" * 40
VALIDATOR = "b" * 40
FIXTURE = "c" * 40
SOURCE = "https://github.com/Codagent-AI/agent-validator.git"


def _assert_golden(path: Path, actual: object, *, capture: str = "CAPTURE_EVAL_GOLDENS") -> None:
    if os.environ.get(capture) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert actual == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("shape", "body", "execution", "validator", "fixture"),
    [
        ("default_docker", "repetitions = 1", "docker", False, False),
        ("validator_fly", "repetitions = 1", "fly", True, False),
        ("fixture_docker", 'fixture_ref = "main"', "docker", False, True),
        ("combined_fly", 'fixture_ref = "main"', "fly", True, True),
        (
            "overrides",
            'fixture_ref = "main"\nrepetitions = 2\nskip_validator = true\n'
            'lead = "codex:default:medium"',
            "fly",
            True,
            True,
        ),
    ],
)
def test_frozen_and_reporting_golden(
    tmp_path: Path, shape: str, body: str, execution: str, validator: bool, fixture: bool
) -> None:
    defaults = EvalDefaults(
        "main",
        "main",
        {
            "lead": "claude:default:medium",
            "implementor": "codex:default:medium",
            "tester": "codex:default:medium",
        },
        False,
        1,
        execution=execution,
    )
    request = parse_request(f"```eval\n{body}\n```", defaults)
    frozen = request.freeze(
        {
            "runner": SHA,
            "skills": SHA,
            "evals": SHA,
            **({"validator": VALIDATOR} if validator else {}),
            **({"fixture": FIXTURE} if fixture else {}),
        },
        suite="and-scene",
        sources={"validator": SOURCE} if validator else {},
    )
    store = ClaimStore(tmp_path / "claims.sqlite3")
    claim = store.create_claim(
        ClaimDraft("repo", 1, "issue", "item", "eval", request.fingerprint, frozen.payload)
    )
    handler = EvalHandler(defaults)
    handler.attach_store(store)
    actual = {
        "fingerprint": request.fingerprint,
        "frozen_spec": json.dumps(claim.frozen_spec),
        "refs": handler.refs_text(claim),
        "frozen_inputs": handler.frozen_inputs_event(claim),
    }
    path = GOLDENS / f"{shape}.json"
    _assert_golden(path, actual)
    store.close()


@pytest.mark.parametrize(
    "shape",
    ["default_docker", "validator_fly", "fixture_docker", "combined_fly", "overrides", "legacy"],
)
def test_report_golden(tmp_path: Path, shape: str) -> None:
    saved_shape = "default_docker" if shape == "legacy" else shape
    saved = json.loads((GOLDENS / f"{saved_shape}.json").read_text(encoding="utf-8"))
    frozen_spec = json.loads(saved["frozen_spec"])
    if shape == "legacy":
        del frozen_spec["settings"]["agent_validator_ref"]
    store = ClaimStore(tmp_path / "reports.sqlite3")
    claim = store.create_claim(
        ClaimDraft("repo", 1, "issue", "item", "eval", saved["fingerprint"], frozen_spec)
    )
    handler = EvalHandler(EvalDefaults("main", "main", {}, False, 1))
    handler.attach_store(store)
    run = store.reserve_run(claim.id, "rep-1", reason="initial", evidence_path="/artifact")
    result = {
        "execution_status": "settled",
        "product_verdict": "passed",
        "report_summary": {
            "Automated score": "68/70",
            "Cost": "1.25 USD",
            "Artifacts": "/artifact",
        },
    }
    actual = {
        "refs": handler.refs_text(claim),
        "frozen_inputs": handler.frozen_inputs_event(claim),
        "settled": handler.attempt_message(run, result, stage="settled"),
        "retry": handler.attempt_message(run, result, stage="retry"),
        "exhausted": handler.attempt_message(run, result, stage="exhausted"),
    }
    path = GOLDENS / f"{shape}_report.json"
    _assert_golden(path, actual)
    store.close()


@pytest.mark.parametrize("case", ["missing_harness", "invalid_fixture"])
def test_invalid_revisions_golden(tmp_path: Path, case: str) -> None:
    saved = json.loads((GOLDENS / "fixture_docker.json").read_text(encoding="utf-8"))
    frozen_spec = json.loads(saved["frozen_spec"])
    revisions = frozen_spec["revisions"]
    if case == "missing_harness":
        del revisions["evals"]
    else:
        revisions["fixture"] = "bad"
    store = ClaimStore(tmp_path / "invalid.sqlite3")
    claim = store.create_claim(
        ClaimDraft("repo", 1, "issue", "item", "eval", "fingerprint", frozen_spec)
    )
    handler = EvalHandler(EvalDefaults("main", "main", {}, False, 1))
    handler.attach_store(store)
    refs = handler.refs_text(claim)
    events = store.pending_events(claim.id)
    actual = {"refs": refs, "events": [{"key": event.key, "body": event.body} for event in events]}
    path = GOLDENS / f"{case}.json"
    _assert_golden(path, actual)
    store.close()


@pytest.mark.parametrize(
    ("execution", "fixture", "validator"),
    [
        ("docker", False, False),
        ("docker", True, False),
        ("fly", False, False),
        ("fly", True, False),
        ("fly", True, True),
    ],
)
def test_suite_plan_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, execution: str, fixture: bool, validator: bool
) -> None:
    from agent_factory.config import FlyLocalConfig
    from agent_factory.suites.and_scene import (
        AndSceneAdapter,
        GitWorktreeManager,
        PreparedWorktrees,
        WorktreeCleanup,
    )
    from tests.integration.test_and_scene_adapter import (
        _git,  # pyright: ignore[reportPrivateUsage]
        _sources,  # pyright: ignore[reportPrivateUsage]
    )

    monkeypatch.setenv("GIT_AUTHOR_DATE", "2001-01-01T00:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2001-01-01T00:00:00Z")
    sources, revisions = _sources(tmp_path)
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "remote", "add", "origin", str(source))
    script = sources.evals / "evals/agent-runner/and-scene/run.sh"
    script.write_text(
        "#!/bin/sh\ncase $1 in\n --fixture-ref) ;;\n --repo) ;;\nesac\n", encoding="utf-8"
    )
    _git(sources.evals, "add", ".")
    _git(sources.evals, "commit", "-m", "fixture flags")
    revisions["evals"] = _git(sources.evals, "rev-parse", "HEAD")
    if validator:
        revisions["validator"] = "b" * 40
    frozen = {
        "version": 1,
        "suite": "and-scene",
        "settings": {
            "roles": {role: "codex:model:medium" for role in ("lead", "implementor", "tester")},
            "skip_validator": False,
        },
        "revisions": {**revisions, **({"fixture": "f" * 40} if fixture else {})},
        **({"sources": {"validator": SOURCE}} if validator else {}),
    }
    store = ClaimStore(tmp_path / "plans.sqlite3")
    claim = store.create_claim(
        ClaimDraft("repo", 1, "issue", "item", "eval", "fingerprint", frozen)
    )
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    worktrees = manager.prepare(claim.id, revisions)
    WorktreeCleanup(store, manager).record(claim.id, worktrees)
    environment = tmp_path / "candidate.env"
    environment.write_text("CANDIDATE_TOKEN=test\n", encoding="utf-8")
    if execution == "fly":
        monkeypatch.setattr("agent_factory.fly.launcher.executable", lambda: "/fake/launcher")

        def dry_run_ready(_adapter: AndSceneAdapter, _worktrees: PreparedWorktrees) -> str | None:
            return None

        monkeypatch.setattr(AndSceneAdapter, "_fly_dry_run", dry_run_ready)
    fly = FlyLocalConfig("factory", "registry.fly.io/factory:base", tmp_path / "fly-token")
    adapter = AndSceneAdapter(
        environment_file=environment, execution=execution, fly=fly if execution == "fly" else None
    )
    plan = adapter.plan(
        frozen,
        worktrees,
        tmp_path / "artifact",
        recovery=False,
        claim_id=claim.id,
        run_id="run",
        unit_key="rep-1",
    )
    manifest = None
    if execution == "fly":
        manifest = json.loads(
            (tmp_path / "artifact" / ".factory" / "manifest.json").read_text(encoding="utf-8")
        )
        manifest["nonce"] = "<nonce>"
    recorded = store.get_claim(claim.id)
    assert recorded is not None
    actual = {
        "argv": list(plan.argv),
        "manifest": manifest,
        "preparation": recorded.preparation,
        "cleanup": recorded.cleanup,
    }
    normalized = json.loads(
        json.dumps(actual).replace(str(tmp_path), "<tmp>").replace(claim.id, "<claim>")
    )
    path = GOLDENS / f"plan_{execution}_{fixture}{'_validator' if validator else ''}.json"
    _assert_golden(path, normalized)
    recovery_plans: dict[str, object] = {}
    for name, checkpoint in (("retry", False), ("resume", True)):
        recovery_artifact = tmp_path / name
        if checkpoint:
            recovery_artifact.mkdir()
            (recovery_artifact / "run-state.json").write_text(
                json.dumps({"schema_version": 1}), encoding="utf-8"
            )
        recovery_plan = adapter.plan(
            frozen,
            worktrees,
            recovery_artifact,
            recovery=True,
            pre_checkpoint_proven=not checkpoint,
            claim_id=claim.id,
            run_id=name,
            unit_key="rep-1",
        )
        recovery_manifest = None
        if execution == "fly":
            recovery_manifest = json.loads(
                (recovery_artifact / ".factory" / "manifest.json").read_text(encoding="utf-8")
            )
            recovery_manifest["nonce"] = "<nonce>"
        recovery_plans[name] = {
            "argv": list(recovery_plan.argv),
            "manifest": recovery_manifest,
        }
    recovery_normalized = json.loads(
        json.dumps(recovery_plans).replace(str(tmp_path), "<tmp>").replace(claim.id, "<claim>")
    )
    recovery_path = GOLDENS / (
        f"recovery_{execution}_{fixture}{'_validator' if validator else ''}.json"
    )
    _assert_golden(recovery_path, recovery_normalized, capture="CAPTURE_EVAL_RECOVERY_GOLDENS")
    assert manager.remove(worktrees) == {}
    store.close()


@pytest.mark.parametrize(
    "case",
    [
        "unknown_runner",
        "unknown_skills",
        "unknown_harness",
        "missing_fixture",
        "missing_validator",
        "invalid_validator_origin",
        "wrong_fixture_origin",
        "unknown_fixture",
        "unpublished_fixture",
        "deleted_fixture_branch",
        "runner_before_fixture",
    ],
)
def test_resolution_reason_golden(tmp_path: Path, case: str) -> None:
    from dataclasses import replace

    from agent_factory.suites.and_scene import FIXTURE_REPOSITORY, ReadinessError
    from tests.integration.test_and_scene_adapter import (
        _git,  # pyright: ignore[reportPrivateUsage]
        _sources,  # pyright: ignore[reportPrivateUsage]
    )

    sources, _revisions = _sources(tmp_path)
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "remote", "add", "origin", str(source))
    branch = _git(sources.runner, "branch", "--show-current")
    body = "repetitions = 1"
    harness = branch
    if case == "unknown_runner":
        body = 'agent_runner_ref = "unknown"'
    elif case == "unknown_skills":
        body = 'agent_skills_ref = "unknown"'
    elif case == "unknown_harness":
        harness = "unknown"
    elif case == "missing_fixture":
        body = f'fixture_ref = "{branch}"'
    elif case in {"missing_validator", "invalid_validator_origin"}:
        validator = tmp_path / "validator"
        if case == "invalid_validator_origin":
            validator.mkdir()
            _git(validator, "init", "-b", branch)
            _git(validator, "config", "user.email", "tests@example.invalid")
            _git(validator, "config", "user.name", "Tests")
            _git(validator, "commit", "--allow-empty", "-m", "validator")
            _git(validator, "remote", "add", "origin", str(validator))
        sources = replace(sources, validator=validator)
    elif case in {
        "wrong_fixture_origin",
        "unknown_fixture",
        "unpublished_fixture",
        "deleted_fixture_branch",
        "runner_before_fixture",
    }:
        monkey_env = {
            "GIT_AUTHOR_DATE": "2001-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2001-01-01T00:00:00Z",
        }
        bare = tmp_path / "fixture-origin.git"
        subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
        _git(bare, "symbolic-ref", "HEAD", f"refs/heads/{branch}")
        fixture_source = tmp_path / "fixture-source"
        fixture_source.mkdir()
        _git(fixture_source, "init", "-b", branch)
        _git(fixture_source, "config", "user.email", "tests@example.invalid")
        _git(fixture_source, "config", "user.name", "Tests")
        with pytest.MonkeyPatch.context() as patch:
            for key, value in monkey_env.items():
                patch.setenv(key, value)
            _git(fixture_source, "commit", "--allow-empty", "-m", "base")
        _git(fixture_source, "remote", "add", "origin", str(bare))
        _git(fixture_source, "push", "-u", "origin", branch)
        fixture_checkout = tmp_path / "fixture-checkout"
        subprocess.run(["git", "clone", "--quiet", str(bare), str(fixture_checkout)], check=True)
        origin_url = (
            "https://github.com/Codagent-AI/other.git"
            if case == "wrong_fixture_origin"
            else FIXTURE_REPOSITORY
        )
        _git(fixture_checkout, "remote", "set-url", "origin", origin_url)
        _git(fixture_checkout, "config", f"url.{bare}.insteadOf", origin_url)
        sources = replace(sources, fixture=fixture_checkout)
        fixture_ref = branch
        if case == "unknown_fixture":
            fixture_ref = "unknown"
        elif case == "unpublished_fixture":
            _git(fixture_checkout, "config", "user.email", "tests@example.invalid")
            _git(fixture_checkout, "config", "user.name", "Tests")
            with pytest.MonkeyPatch.context() as patch:
                for key, value in monkey_env.items():
                    patch.setenv(key, value)
                _git(fixture_checkout, "commit", "--allow-empty", "-m", "local")
            fixture_ref = _git(fixture_checkout, "rev-parse", "HEAD")
        elif case == "deleted_fixture_branch":
            _git(fixture_source, "checkout", "-b", "temporary")
            with pytest.MonkeyPatch.context() as patch:
                for key, value in monkey_env.items():
                    patch.setenv(key, value)
                _git(fixture_source, "commit", "--allow-empty", "-m", "temporary")
            fixture_ref = _git(fixture_source, "rev-parse", "HEAD")
            _git(fixture_source, "push", "origin", "temporary")
            _git(fixture_checkout, "fetch", "origin")
            _git(fixture_source, "push", "origin", "--delete", "temporary")
        body = f'fixture_ref = "{fixture_ref}"'
        if case == "runner_before_fixture":
            body += '\nagent_runner_ref = "unknown"'
    defaults = EvalDefaults(
        branch,
        branch,
        {},
        False,
        1,
        execution="fly" if "validator" in case else "docker",
        agent_validator_ref=branch,
    )
    handler = EvalHandler(defaults, harness_ref=harness, sources=sources)
    store = ClaimStore(tmp_path / "reasons.sqlite3")
    from agent_factory.controller import RequestSnapshot

    snapshot = RequestSnapshot(
        "repo",
        1,
        "issue",
        "item",
        "writer",
        "write",
        "Eval",
        frozenset(),
        "Ready",
        "factory",
        None,
        f"```eval\n{body}\n```",
        False,
    )
    with pytest.raises(ReadinessError) as error:
        handler.accept(snapshot, store, handler.resolve_request)
    assert store.claims_for_item("item") == []
    actual = str(error.value).replace(str(tmp_path), "<tmp>")
    path = GOLDENS / f"reason_{case}.json"
    _assert_golden(path, actual)
    store.close()


@pytest.mark.parametrize(
    ("case", "body"),
    [
        ("unsupported_validator", 'agent_validator_ref = "main"'),
        ("empty_runner", 'agent_runner_ref = ""'),
        ("empty_fixture", 'fixture_ref = ""'),
    ],
)
def test_rejection_golden(case: str, body: str) -> None:
    defaults = EvalDefaults("main", "main", {}, False, 1)
    with pytest.raises(ValueError) as error:
        parse_request(f"```eval\n{body}\n```", defaults)
    actual = str(error.value)
    path = GOLDENS / f"rejection_{case}.json"
    _assert_golden(path, actual)


@pytest.mark.parametrize("case", ["fixture_flags", "validator_docker_hold"])
def test_readiness_reason_golden(tmp_path: Path, case: str) -> None:
    from collections.abc import Callable
    from inspect import signature
    from types import SimpleNamespace
    from typing import cast

    from agent_factory.config import LocalConfig
    from agent_factory.suites.and_scene import AndSceneAdapter, GitWorktreeManager, ReadinessError
    from tests.integration.test_and_scene_adapter import (
        _git,  # pyright: ignore[reportPrivateUsage]
        _sources,  # pyright: ignore[reportPrivateUsage]
    )

    monkey_date = "2001-01-01T00:00:00Z"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_AUTHOR_DATE", monkey_date)
        patch.setenv("GIT_COMMITTER_DATE", monkey_date)
        sources, revisions = _sources(tmp_path)
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "remote", "add", "origin", str(source))
    store = ClaimStore(tmp_path / "readiness.sqlite3")
    if case == "fixture_flags":
        worktrees = GitWorktreeManager(tmp_path / "factory", sources).prepare("claim", revisions)
        environment = tmp_path / "candidate.env"
        environment.write_text("CANDIDATE_TOKEN=test\n", encoding="utf-8")
        adapter = AndSceneAdapter(environment_file=environment)
        if "fixture_pinned" in signature(adapter.readiness).parameters:
            legacy_readiness = cast(Callable[..., str | None], adapter.readiness)
            reason = legacy_readiness(worktrees, fixture_pinned=True)
        else:
            reason = adapter.readiness(worktrees, pinned={"fixture"})
    else:
        frozen = {
            "suite": "and-scene",
            "settings": {},
            "revisions": {**revisions, "validator": "b" * 40},
        }
        claim = store.create_claim(
            ClaimDraft("repo", 1, "issue", "item", "eval", "fingerprint", frozen)
        )
        local = cast(LocalConfig, SimpleNamespace(eval_execution="docker"))
        handler = EvalHandler(
            EvalDefaults("main", "main", {}, False, 1),
            local=local,
            manager=GitWorktreeManager(tmp_path / "factory", sources),
            adapter=AndSceneAdapter(environment_file=tmp_path / "unused.env"),
        )
        with pytest.raises(ReadinessError) as error:
            handler.prepare(claim)
        reason = str(error.value)
    assert reason is not None
    actual = reason.replace(str(tmp_path), "<tmp>")
    path = GOLDENS / f"readiness_{case}.json"
    _assert_golden(path, actual)
    store.close()


def test_admission_freezes_the_golden_combined_shape_and_continues(tmp_path: Path) -> None:
    """INT-001 through real Git resolution, `accept`, and an unchanged re-read."""
    from dataclasses import replace

    from agent_factory.controller import Controller, RequestSnapshot
    from agent_factory.suites.and_scene import FIXTURE_REPOSITORY
    from tests.integration.test_and_scene_adapter import (
        _git,  # pyright: ignore[reportPrivateUsage]
        _sources,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.integration.test_controller_reporting import Comments

    sources, _revisions = _sources(tmp_path)
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "branch", "-M", "main")
        _git(source, "remote", "add", "origin", str(source))
    validator = tmp_path / "validator"
    validator.mkdir()
    _git(validator, "init", "-b", "main")
    _git(validator, "config", "user.email", "tests@example.invalid")
    _git(validator, "config", "user.name", "Tests")
    _git(validator, "commit", "--allow-empty", "-m", "validator")
    _git(validator, "remote", "add", "origin", SOURCE)
    _git(validator, "config", f"url.{validator}.insteadOf", SOURCE)
    bare = tmp_path / "fixture-origin.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)
    _git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
    fixture_source = tmp_path / "fixture-source"
    fixture_source.mkdir()
    _git(fixture_source, "init", "-b", "main")
    _git(fixture_source, "config", "user.email", "tests@example.invalid")
    _git(fixture_source, "config", "user.name", "Tests")
    _git(fixture_source, "commit", "--allow-empty", "-m", "fixture")
    _git(fixture_source, "remote", "add", "origin", str(bare))
    _git(fixture_source, "push", "--quiet", "-u", "origin", "main")
    fixture_checkout = tmp_path / "fixture-checkout"
    subprocess.run(["git", "clone", "--quiet", str(bare), str(fixture_checkout)], check=True)
    _git(fixture_checkout, "remote", "set-url", "origin", FIXTURE_REPOSITORY)
    _git(fixture_checkout, "config", f"url.{bare}.insteadOf", FIXTURE_REPOSITORY)
    defaults = EvalDefaults(
        "main",
        "main",
        {
            "lead": "claude:default:medium",
            "implementor": "codex:default:medium",
            "tester": "codex:default:medium",
        },
        False,
        1,
        execution="fly",
    )
    handler = EvalHandler(
        defaults,
        harness_ref="main",
        sources=replace(sources, validator=validator, fixture=fixture_checkout),
    )
    store = ClaimStore(tmp_path / "admission.sqlite3")
    controller = Controller(store, Comments(), {"eval": handler})
    snapshot = RequestSnapshot(
        "repo",
        1,
        "issue",
        "item",
        "writer",
        "write",
        "Eval",
        frozenset(),
        "Ready",
        "factory",
        None,
        '```eval\nfixture_ref = "main"\n```',
        False,
    )

    claim = controller.accept(snapshot, resolve=handler.resolve_request)

    assert claim is not None
    saved = store.get_claim(claim.id)
    assert saved is not None
    raw_revisions = saved.frozen_spec["revisions"]
    assert isinstance(raw_revisions, dict)
    revisions = cast(dict[str, str], raw_revisions)
    assert revisions["runner"] == _git(sources.runner, "rev-parse", "HEAD")
    assert revisions["skills"] == _git(sources.skills, "rev-parse", "HEAD")
    assert revisions["evals"] == _git(sources.evals, "rev-parse", "HEAD")
    assert revisions["validator"] == _git(validator, "rev-parse", "HEAD")
    assert revisions["fixture"] == _git(fixture_source, "rev-parse", "HEAD")
    placeholders = {
        "runner": SHA,
        "skills": SHA,
        "evals": SHA,
        "validator": VALIDATOR,
        "fixture": FIXTURE,
    }
    normalized = {
        **saved.frozen_spec,
        "revisions": {name: placeholders[name] for name in revisions},
    }
    golden = json.loads((GOLDENS / "combined_fly.json").read_text(encoding="utf-8"))
    assert json.dumps(normalized) == golden["frozen_spec"]
    assert saved.request_fingerprint == golden["fingerprint"]

    _git(sources.runner, "commit", "--allow-empty", "-m", "advance")
    again = controller.accept(snapshot, resolve=handler.resolve_request)

    assert again is not None and again.id == claim.id
    assert store.claims_for_item("item") == [store.get_claim(claim.id)]
    assert store.get_claim(claim.id) == saved
    store.close()
