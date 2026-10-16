"""The eval `crosscheck` role: intake, freezing, suite invocation, and readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.config import SharedConfig
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, parse_request
from tests.integration.test_and_scene_adapter import (
    _git,  # pyright: ignore[reportPrivateUsage]
    _sources,  # pyright: ignore[reportPrivateUsage]
)
from tests.integration.test_fly_operations import (  # noqa: F401
    SHARED,
    Site,
    site,  # pyright: ignore[reportUnusedImport]
)
from tests.integration.test_fly_readiness import (
    _dry_run_suite,  # pyright: ignore[reportPrivateUsage]
)

_ROLES = {
    "lead": "claude:claude-sonnet-5-5:high",
    "crosscheck": "codex:gpt-6.1-sol:high",
    "implementor": "codex:gpt-6-luna:medium",
    "tester": "codex:gpt-6-luna:medium",
}
_HARNESS_WITH_CROSSCHECK = (
    "#!/bin/sh\ncase $1 in\n    --lead-cli) ;;\n    --crosscheck-cli) ;;\nesac\n"
)


def _defaults(execution: str = "docker", **roles: str) -> EvalDefaults:
    return EvalDefaults("main", "main", {**_ROLES, **roles}, False, 1, execution=execution)


# -- intake ------------------------------------------------------------------


def test_shipped_eval_defaults_include_a_complete_crosscheck_profile() -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))

    assert shared.eval.defaults["crosscheck"] == "codex:gpt-6.1-sol:high"


def test_crosscheck_override_replaces_only_the_crosscheck_default() -> None:
    request = parse_request('```eval\ncrosscheck = "claude:claude-opus-5-5:high"\n```', _defaults())

    assert request.settings["roles"] == {**_ROLES, "crosscheck": "claude:claude-opus-5-5:high"}
    assert request.settings["repetitions"] == 1


def test_omitted_crosscheck_uses_the_configured_default() -> None:
    request = parse_request("```eval\nrepetitions = 2\n```", _defaults())

    roles = request.settings["roles"]
    assert isinstance(roles, dict)
    assert roles["crosscheck"] == "codex:gpt-6.1-sol:high"


@pytest.mark.parametrize("value", ['"codex:gpt-6.1-sol"', '"codex::high"', "3"])
def test_incomplete_crosscheck_profile_is_invalid(value: str) -> None:
    with pytest.raises(ValueError, match="crosscheck must contain a complete"):
        parse_request(f"```eval\ncrosscheck = {value}\n```", _defaults())


def test_crosscheck_is_frozen_with_the_other_role_profiles() -> None:
    request = parse_request('```eval\ncrosscheck = "claude:opus:high"\n```', _defaults())
    frozen = request.freeze(
        {"runner": "a" * 40, "skills": "a" * 40, "evals": "a" * 40}, suite="and-scene"
    )

    settings = frozen.payload["settings"]
    assert isinstance(settings, dict)
    assert settings["roles"]["crosscheck"] == "claude:opus:high"  # pyright: ignore[reportUnknownMemberType]


def test_fly_refuses_cursor_crosscheck_from_defaults_and_overrides() -> None:
    with pytest.raises(ValueError, match="crosscheck: Cursor is unavailable on Fly"):
        parse_request("```eval\n# defaults\n```", _defaults("fly", crosscheck="cursor:a:medium"))
    with pytest.raises(ValueError, match="crosscheck: Cursor is unavailable on Fly"):
        parse_request("```eval\ncrosscheck = 'cursor:a:medium'\n```", _defaults("fly"))
    assert parse_request("```eval\ncrosscheck = 'cursor:a:medium'\n```", _defaults("docker"))


def test_handler_defaults_carry_the_configured_crosscheck(site: Site) -> None:  # noqa: F811
    from agent_factory.work_kinds.eval import EvalHandler

    site.shared_path.write_text(
        SHARED.replace('crosscheck = "codex:x:medium"', 'crosscheck = "claude:y:high"'),
        encoding="utf-8",
    )
    handler = EvalHandler.from_config(site.shared(), site.config(evals="docker"))
    request = parse_request("```eval\n# defaults\n```", handler._defaults)  # pyright: ignore[reportPrivateUsage]

    roles = request.settings["roles"]
    assert isinstance(roles, dict)
    assert roles["crosscheck"] == "claude:y:high"


def test_doctor_under_fly_fails_a_cursor_crosscheck_default(site: Site) -> None:  # noqa: F811
    from agent_factory.operations import doctor

    site.shared_path.write_text(
        SHARED.replace(
            'crosscheck = "codex:x:medium"',
            'crosscheck = "cursor:agent:medium"',
        ),
        encoding="utf-8",
    )
    site.stub("cursor", exit_code=0)

    diagnostics = doctor(site.config(evals="fly", fixes="host"))

    compatibility = [d for d in diagnostics if "Cursor is unavailable on Fly" in d.detail]
    assert [d.detail for d in compatibility] == ["crosscheck: Cursor is unavailable on Fly"]
    assert not compatibility[0].available


def test_fly_backend_checks_claude_login_for_a_claude_crosscheck_default(
    site: Site,  # noqa: F811
) -> None:
    from agent_factory.fly.backend import FlyMachineBackend

    site.shared_path.write_text(
        SHARED.replace(
            'crosscheck = "codex:x:medium"',
            'crosscheck = "claude:y:high"',
        ),
        encoding="utf-8",
    )
    diagnostics = FlyMachineBackend().readiness(site.config(evals="fly"), site.shared())

    assert any(item.name == "Fly Claude login" for item in diagnostics)


# -- suite invocation --------------------------------------------------------


def _adapter_and_worktrees(tmp_path: Path, script: str | None):  # type: ignore[no-untyped-def]
    from agent_factory.suites.and_scene import AndSceneAdapter, GitWorktreeManager

    sources, revisions = _sources(tmp_path)
    if script is not None:
        (sources.evals / "evals/agent-runner/and-scene/run.sh").write_text(script)
        _git(sources.evals, "add", ".")
        _git(sources.evals, "commit", "-m", "harness")
        revisions["evals"] = _git(sources.evals, "rev-parse", "HEAD")
    worktrees = GitWorktreeManager(tmp_path / "factory", sources).prepare("claim", revisions)
    environment = tmp_path / "candidate.env"
    environment.write_text("CANDIDATE_TOKEN=test\n")
    return AndSceneAdapter(environment_file=environment), worktrees, revisions


def _frozen(roles: dict[str, str], revisions: dict[str, str]) -> dict[str, object]:
    return {"suite": "and-scene", "settings": {"roles": roles}, "revisions": revisions}


def _role_flags(argv: tuple[str, ...]) -> list[tuple[str, str]]:
    return [(value, argv[index + 1]) for index, value in enumerate(argv) if value.endswith("-cli")]


def test_plan_passes_a_frozen_crosscheck_profile_in_role_order(tmp_path: Path) -> None:
    adapter, worktrees, revisions = _adapter_and_worktrees(tmp_path, _HARNESS_WITH_CROSSCHECK)

    plan = adapter.plan(_frozen(_ROLES, revisions), worktrees, tmp_path / "a", recovery=False)

    assert _role_flags(plan.argv) == [
        ("--lead-cli", "claude"),
        ("--crosscheck-cli", "codex"),
        ("--implementor-cli", "codex"),
        ("--tester-cli", "codex"),
    ]
    assert plan.argv[plan.argv.index("--crosscheck-model") + 1] == "gpt-6.1-sol"
    assert plan.argv[plan.argv.index("--crosscheck-effort") + 1] == "high"


def test_claim_frozen_before_crosscheck_keeps_three_roles(tmp_path: Path) -> None:
    adapter, worktrees, revisions = _adapter_and_worktrees(tmp_path, None)
    roles = {role: profile for role, profile in _ROLES.items() if role != "crosscheck"}

    plan = adapter.plan(_frozen(roles, revisions), worktrees, tmp_path / "a", recovery=False)

    assert [flag for flag, _ in _role_flags(plan.argv)] == [
        "--lead-cli",
        "--implementor-cli",
        "--tester-cli",
    ]
    assert not any(value.startswith("--crosscheck") for value in plan.argv)


def test_invalid_frozen_crosscheck_profile_is_a_readiness_error(tmp_path: Path) -> None:
    from agent_factory.suites.and_scene import ReadinessError

    adapter, worktrees, revisions = _adapter_and_worktrees(tmp_path, _HARNESS_WITH_CROSSCHECK)

    with pytest.raises(ReadinessError, match="frozen crosscheck profile is invalid"):
        adapter.plan(
            _frozen({**_ROLES, "crosscheck": "codex"}, revisions),
            worktrees,
            tmp_path / "a",
            recovery=False,
        )


def test_harness_without_crosscheck_holds_a_frozen_crosscheck(tmp_path: Path) -> None:
    from agent_factory.suites.and_scene import ReadinessError

    adapter, worktrees, revisions = _adapter_and_worktrees(tmp_path, "#!/bin/sh\n")

    reason = adapter.readiness(worktrees, roles=_ROLES.keys())
    assert reason is not None
    assert "--crosscheck-cli" in reason and revisions["evals"][:12] in reason
    with pytest.raises(ReadinessError, match="--crosscheck-cli"):
        adapter.plan(_frozen(_ROLES, revisions), worktrees, tmp_path / "a", recovery=False)
    three = {role: profile for role, profile in _ROLES.items() if role != "crosscheck"}
    assert adapter.readiness(worktrees, roles=three.keys()) is None


def test_eval_handler_prepare_holds_a_frozen_crosscheck_on_an_old_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_factory.suites.and_scene import GitWorktreeManager, ReadinessError
    from agent_factory.work_kinds.eval import EvalHandler

    adapter, _, revisions = _adapter_and_worktrees(tmp_path, "#!/bin/sh\n")

    def authenticated(_profiles: object) -> list[object]:
        return []

    monkeypatch.setattr("agent_factory.work_kinds.eval.handler.model_authentication", authenticated)
    store = ClaimStore(tmp_path / "claims.sqlite3")
    claim = store.create_claim(
        ClaimDraft("repo", 1, "issue", "item", "eval", "f", _frozen(_ROLES, revisions))
    )
    handler = EvalHandler(
        _defaults(),
        adapter=adapter,
        manager=GitWorktreeManager(tmp_path / "factory", _sources_of(tmp_path)),
    )
    with pytest.raises(ReadinessError, match="--crosscheck-cli"):
        handler.prepare(claim)
    store.close()


def _sources_of(tmp_path: Path):  # type: ignore[no-untyped-def]
    from agent_factory.suites.and_scene import SourceRepositories

    return SourceRepositories(tmp_path / "runner", tmp_path / "skills", tmp_path / "evals")


def test_readiness_does_not_inspect_runner_workflow_files(tmp_path: Path) -> None:
    adapter, worktrees, _ = _adapter_and_worktrees(tmp_path, None)
    for workflow in (worktrees.runner / "workflows").rglob("*.yaml"):
        workflow.unlink()

    assert adapter.readiness(worktrees) is None


# -- Fly compatibility -------------------------------------------------------


def _dry_run_roles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accepts_crosscheck: bool
) -> dict[str, str]:
    recorded = tmp_path / "argv"
    case = "case $1 in\n    --crosscheck-cli) ;;\nesac\n" if accepts_crosscheck else ""
    suite, worktrees = _dry_run_suite(
        tmp_path, monkeypatch, f'{case}printf "%s\\n" "$@" > {recorded}\nexit 0'
    )
    assert suite._fly_dry_run(worktrees) is None  # pyright: ignore[reportPrivateUsage]
    arguments = recorded.read_text(encoding="utf-8").split("\n")
    return {
        value.removeprefix("--").removesuffix("-cli"): arguments[index + 1]
        for index, value in enumerate(arguments)
        if value.endswith("-cli")
    }


def test_fly_dry_run_includes_crosscheck_when_the_harness_accepts_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roles = _dry_run_roles(tmp_path, monkeypatch, accepts_crosscheck=True)

    assert list(roles) == ["lead", "crosscheck", "implementor", "tester"]
    assert set(roles.values()) == {"claude", "codex"}


def test_fly_dry_run_keeps_three_roles_for_a_harness_without_crosscheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roles = _dry_run_roles(tmp_path, monkeypatch, accepts_crosscheck=False)

    assert list(roles) == ["lead", "implementor", "tester"]


def test_claude_crosscheck_quota_wait_bounds_the_deadline(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from agent_factory.suites.and_scene import bounded_quota_deadline

    (tmp_path / "factory-suite.log").write_text(
        "Claude crosscheck quota reached; waiting until 2026-09-10T14:00:00Z "
        "before resuming Agent Runner\n"
    )

    assert (
        bounded_quota_deadline(tmp_path, now=datetime(2026, 9, 10, 12, tzinfo=UTC).timestamp())
        is not None
    )


@pytest.mark.parametrize("role", ["unattributed", "null"])
def test_unattributed_claude_quota_wait_bounds_the_deadline(tmp_path: Path, role: str) -> None:
    from datetime import UTC, datetime

    from agent_factory.suites.and_scene import bounded_quota_deadline

    (tmp_path / "factory-suite.log").write_text(
        f"Claude {role} quota reached; waiting until 2026-09-10T14:00:00Z "
        "before resuming Agent Runner\n"
    )

    assert (
        bounded_quota_deadline(tmp_path, now=datetime(2026, 9, 10, 12, tzinfo=UTC).timestamp())
        is not None
    )


@pytest.mark.parametrize("line", ["", 'crosscheck = "codex:x"'])
def test_doctor_holds_evals_while_the_crosscheck_default_is_incomplete(
    site: Site,  # noqa: F811
    line: str,
) -> None:
    from agent_factory.operations import doctor

    site.shared_path.write_text(
        SHARED.replace('crosscheck = "codex:x:medium"', line), encoding="utf-8"
    )

    diagnostics = doctor(site.config(evals="docker", fixes="host"))

    held = [d for d in diagnostics if d.name == "Eval crosscheck default"]
    assert [(d.available, d.group) for d in held] == [(False, "eval")]


def test_doctor_accepts_a_complete_crosscheck_default(site: Site) -> None:  # noqa: F811
    from agent_factory.operations import doctor

    diagnostics = doctor(site.config(evals="docker", fixes="host"))

    assert not [d for d in diagnostics if d.name == "Eval crosscheck default"]
