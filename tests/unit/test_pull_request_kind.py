"""The shared pull-request definition is data, including reconciliation policy."""

from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.github import BranchInfo, PullRequestInfo
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import ReadinessError


def test_fix_definition_preserves_the_registered_fix_contract() -> None:
    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    fix = kinds.FIX
    assert fix.kind == fix.unit_key == "fix"
    assert fix.noun == "Fix" and fix.item_noun == "bug"
    assert fix.workflow_name == "factory-fix"
    assert fix.workflow_file == "factory-fix-v1.0.yaml"
    assert fix.outcome_file == "fix-outcome.json"
    assert fix.branch_prefix == "factory/fix"
    assert fix.sync_marker == "fix-sync"
    assert fix.allowed_modes == ("docker", "host")
    assert fix.roles == ("lead", "implementor", "tester")
    assert fix.doctor_groups == {"docker": "fix-sandbox", "host": "fix-host"}
    assert fix.reconcile is kinds.ReconcilePolicy.SETTLE_ON_OPEN_PR


def test_kind_definition_cannot_mutate_doctor_groups() -> None:
    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    with pytest.raises(TypeError):
        kinds.FIX.doctor_groups["host"] = "changed"  # type: ignore[index]


def test_both_reconcile_policies_are_available() -> None:
    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    assert {policy.name for policy in kinds.ReconcilePolicy} == {
        "SETTLE_ON_OPEN_PR",
        "RESUME_FROM_OWN_BRANCH",
    }


@pytest.mark.parametrize(
    ("policy", "draft", "settles"),
    [
        ("SETTLE_ON_OPEN_PR", True, True),
        ("RESUME_FROM_OWN_BRANCH", True, False),
        ("RESUME_FROM_OWN_BRANCH", False, True),
    ],
)
def test_reconciliation_policy_controls_open_own_pull_request(
    tmp_path: Path, policy: str, draft: bool, settles: bool
) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))
    handler = PullRequestHandler(
        replace(kinds.FIX, reconcile=getattr(kinds.ReconcilePolicy, policy)), shared, local
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp", {}))
    handler.attach_store(store)
    branch = handler.branch_name(claim)
    pull = PullRequestInfo("https://example.test/pr/2", 2, "a" * 40, draft)

    class GitHub:
        def get_branch(self, repository: str, name: str) -> object:
            return None

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            assert name == branch
            return [pull]

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    handler.attach_github(GitHub())  # pyright: ignore[reportArgumentType]
    found = handler.reconcile(claim)
    assert (found is not None) is settles
    assert (store.get_claim(claim.id).lifecycle == "settled") is settles  # pyright: ignore[reportOptionalMemberAccess]


def test_resume_policy_holds_ambiguous_own_pull_requests(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))
    handler = PullRequestHandler(
        replace(kinds.FIX, reconcile=kinds.ReconcilePolicy.RESUME_FROM_OWN_BRANCH), shared, local
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp", {}))
    handler.attach_store(store)

    class GitHub:
        def get_branch(self, repository: str, name: str) -> object:
            return None

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            return [PullRequestInfo("a", 1, "a" * 40), PullRequestInfo("b", 2, "b" * 40)]

    handler.attach_github(GitHub())  # pyright: ignore[reportArgumentType]
    with pytest.raises(ReadinessError, match="ambiguous"):
        handler.reconcile(claim)


def test_resume_policy_settles_on_other_open_factory_pr_for_issue(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))
    handler = PullRequestHandler(
        replace(kinds.FIX, reconcile=kinds.ReconcilePolicy.RESUME_FROM_OWN_BRANCH), shared, local
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp", {}))
    handler.attach_store(store)

    class GitHub:
        def get_branch(self, repository: str, name: str) -> object:
            return None

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            return []

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return [PullRequestInfo("https://example.test/pr/3", 3, "b" * 40)]

    handler.attach_github(GitHub())  # pyright: ignore[reportArgumentType]
    assert handler.reconcile(claim) is not None


def test_resume_policy_records_own_branch_as_resume_point(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))
    handler = PullRequestHandler(
        replace(kinds.FIX, reconcile=kinds.ReconcilePolicy.RESUME_FROM_OWN_BRANCH), shared, local
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp", {}))
    handler.attach_store(store)

    class GitHub:
        def get_branch(self, repository: str, name: str) -> BranchInfo:
            return BranchInfo(name, "a" * 40)

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            return []

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    handler.attach_github(GitHub())  # pyright: ignore[reportArgumentType]
    assert handler.reconcile(claim) is None
    recorded = store.get_claim(claim.id)
    assert recorded is not None
    assert recorded.preparation["resume"] == {
        "branch": handler.branch_name(claim),
        "head_sha": "a" * 40,
    }


def test_resume_policy_holds_two_nondraft_factory_prs(tmp_path: Path) -> None:
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler

    kinds = import_module("agent_factory.work_kinds.pull_request.kinds")
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    local = LocalConfig.from_file(Path("config/local.example.toml"))
    handler = PullRequestHandler(
        replace(kinds.FIX, reconcile=kinds.ReconcilePolicy.RESUME_FROM_OWN_BRANCH), shared, local
    )
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp", {}))
    handler.attach_store(store)
    own = PullRequestInfo("https://example.test/pr/2", 2, "a" * 40)
    other = PullRequestInfo("https://example.test/pr/3", 3, "b" * 40)

    class GitHub:
        def get_branch(self, repository: str, name: str) -> None:
            return None

        def list_open_pull_requests_for_head(
            self, repository: str, name: str
        ) -> list[PullRequestInfo]:
            return [own]

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return [other]

    handler.attach_github(GitHub())  # pyright: ignore[reportArgumentType]
    with pytest.raises(ReadinessError, match="ambiguous"):
        handler.reconcile(claim)
