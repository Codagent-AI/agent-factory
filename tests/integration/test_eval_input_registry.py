"""A request supplied only by a registry entry crosses the ordinary-input path."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from agent_factory import runtime
from agent_factory.config import FlyLocalConfig
from agent_factory.controller import RequestSnapshot
from agent_factory.fly.guest import job_script
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import (
    AndSceneAdapter,
    GitWorktreeManager,
    PreparedWorktrees,
    ReadinessError,
    inputs,
)
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler, parse_request
from tests.integration.test_and_scene_adapter import (
    _git,  # pyright: ignore[reportPrivateUsage]
    _repository,  # pyright: ignore[reportPrivateUsage]
    _sources,  # pyright: ignore[reportPrivateUsage]
)


def test_ordinary_registry_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sources, revisions = _sources(tmp_path)
    sample, sample_sha = _repository(tmp_path, "sample", {"data.txt": "sample\n"})
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "remote", "add", "origin", str(source))
        _git(source, "fetch", "origin")
    sample_origin = "https://github.com/Codagent-AI/sample.git"
    _git(sample, "remote", "add", "origin", sample_origin)
    _git(sample, "config", f"url.{sample}.insteadOf", sample_origin)
    _git(sample, "fetch", "origin")
    branch = _git(sample, "branch", "--show-current")
    script = sources.evals / "evals/agent-runner/and-scene/run.sh"
    script.write_text("#!/bin/sh\ncase $1 in\n --sample-ref) ;;\nesac\n", encoding="utf-8")
    _git(sources.evals, "add", ".")
    _git(sources.evals, "commit", "-m", "sample flag")
    revisions["evals"] = _git(sources.evals, "rev-parse", "HEAD")
    sample_input = inputs.RevisionInput(
        name="sample",
        noun="sample",
        required=False,
        setting="sample_ref",
        requestable=True,
        has_default=False,
        admission_rank=5,
        resolve=lambda checkout, ref: runtime._resolve_revision(cast(Path, checkout), ref),  # pyright: ignore[reportPrivateUsage]
        suite_arguments=lambda sha: ("--sample-ref", sha),
        suite_flags=("--sample-ref",),
        report_line=lambda sha: f"Sample: {sha}",
    )
    monkeypatch.setattr(inputs, "EVAL_INPUTS", inputs.EVAL_INPUTS + (sample_input,))
    all_sources = replace(sources, extra={"sample": sample})
    defaults = EvalDefaults(
        branch,
        branch,
        {role: "codex:model:medium" for role in ("lead", "implementor", "tester")},
        False,
        1,
    )
    request = parse_request(f'```eval\nsample_ref = "{branch}"\n```', defaults)
    store = ClaimStore(tmp_path / "claims.sqlite3")
    handler = EvalHandler(defaults, harness_ref=branch, sources=all_sources)
    resolution = handler.resolve_request(request)
    assert resolution.revisions["sample"] == sample_sha
    draft = handler.accept(
        RequestSnapshot(
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
            f'```eval\nsample_ref = "{branch}"\n```',
            False,
        ),
        store,
        handler.resolve_request,
    )
    assert isinstance(draft, ClaimDraft)
    claim = store.create_claim(draft)
    handler.attach_store(store)
    assert list(cast(dict[str, str], draft.frozen_spec["revisions"]))[-1] == "sample"
    refs = handler.refs_text(claim)
    assert refs is not None and refs.endswith(f"sample@{sample_sha[:7]}")
    assert "sample" in handler.frozen_inputs_event(claim)
    run = store.reserve_run(
        claim.id, "rep-1", reason="initial", evidence_path=str(tmp_path / "evidence")
    )
    assert f"Sample: {sample_sha}" in handler.attempt_message(
        run, {"execution_status": "settled", "product_verdict": "passed"}, stage="settled"
    )
    worktrees = GitWorktreeManager(tmp_path / "factory", all_sources).prepare(
        claim.id, cast(dict[str, object], claim.frozen_spec["revisions"])
    )
    environment = tmp_path / "candidate.env"
    environment.write_text("CANDIDATE_TOKEN=test\n", encoding="utf-8")
    docker = AndSceneAdapter(environment_file=environment)
    assert docker.readiness(worktrees, pinned={"sample"}) is None
    docker_plan = docker.plan(
        claim.frozen_spec, worktrees, tmp_path / "docker-artifact", recovery=False
    )
    assert docker_plan.argv[docker_plan.argv.index("--sample-ref") + 1] == sample_sha
    monkeypatch.setattr("agent_factory.fly.launcher.executable", lambda: "/fake/launcher")

    def dry_run_ready(_adapter: AndSceneAdapter, _worktrees: PreparedWorktrees) -> str | None:
        return None

    monkeypatch.setattr(AndSceneAdapter, "_fly_dry_run", dry_run_ready)
    fly = AndSceneAdapter(
        environment_file=environment,
        execution="fly",
        fly=FlyLocalConfig("factory", "registry.fly.io/factory:base", tmp_path / "fly-token"),
    )
    fly_plan = fly.plan(claim.frozen_spec, worktrees, tmp_path / "fly-artifact", recovery=False)
    assert fly_plan.argv[fly_plan.argv.index("--sample-ref") + 1] == sample_sha
    manifest = json.loads((tmp_path / "fly-artifact" / ".factory" / "manifest.json").read_text())
    assert "sample" not in manifest["commits"]
    assert "--sample-ref" in job_script(manifest, " ".join(fly_plan.argv))
    monkeypatch.setattr(sys, "argv", ["agent-factory", "honored-revisions"])
    from agent_factory import cli

    cli.main()
    assert capsys.readouterr().out.splitlines()[-1] == "sample"
    absent = parse_request("```eval\nrepetitions = 1\n```", defaults)
    assert "sample" not in handler.resolve_request(absent).revisions
    (worktrees.evals / "evals/agent-runner/and-scene/run.sh").write_text("#!/bin/sh\n")
    assert "does not accept --sample-ref" in str(docker.readiness(worktrees, pinned={"sample"}))
    assert GitWorktreeManager(tmp_path / "factory", all_sources).remove(worktrees) == {}
    store.close()


def test_requested_fixture_without_checkout_is_not_admitted(tmp_path: Path) -> None:
    sources, _revisions = _sources(tmp_path)
    for source in (sources.runner, sources.skills, sources.evals):
        _git(source, "remote", "add", "origin", str(source))
    branch = _git(sources.runner, "branch", "--show-current")
    store = ClaimStore(tmp_path / "claims.sqlite3")
    defaults = EvalDefaults(branch, branch, {}, False, 1)
    handler = EvalHandler(defaults, harness_ref=branch, sources=sources)
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
    with pytest.raises(ReadinessError, match=r"and-scene checkout None: is missing; clone"):
        handler.accept(snapshot, store, handler.resolve_request)
    assert store.claims_for_item("item") == []
    store.close()
