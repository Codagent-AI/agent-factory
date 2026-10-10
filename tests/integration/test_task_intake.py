"""Task handoff uses writer permissions and an independent store slot."""

# pyright: reportPrivateUsage=false

from dataclasses import replace
from pathlib import Path

from agent_factory.config import FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.controller import RequestSnapshot
from agent_factory.github import IssueComment, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore, NonterminalRunError
from agent_factory.work_kinds import handlers
from tests.integration.test_fix_config import _LOCAL_BASE, _SHARED_BASE


def snapshot(permission: str = "write") -> RequestSnapshot:
    return RequestSnapshot(
        "example/work",
        12,
        "I12",
        "P12",
        "author",
        permission,
        "Task",
        frozenset(),
        "Ready",
        "factory",
        None,
        "request",
        False,
    )


def test_task_admission_requires_configuration_writer_and_fix_target() -> None:
    local = LocalConfig.from_toml(_LOCAL_BASE)
    disabled = replace(
        SharedConfig.from_toml(_SHARED_BASE), fix=FixConfig(targets=(FixTarget("example/work"),))
    )
    enabled = replace(SharedConfig.from_toml(_SHARED_BASE + "\n[task]\n"), fix=disabled.fix)
    assert not handlers(disabled, local)["task"].handles(snapshot())
    task = handlers(enabled, local)["task"]
    assert task.handles(snapshot())
    assert not task.handles(snapshot("read"))
    assert not task.handles(replace(snapshot(), repository="other/repo"))
    assert not task.handles(replace(snapshot(), labels=frozenset({"needs-input"})))


def test_non_writer_task_handoff_comment_is_deduplicated() -> None:
    shared = replace(
        SharedConfig.from_toml(_SHARED_BASE + "\n[task]\n"),
        fix=FixConfig(targets=(FixTarget("example/work"),)),
    )
    task = handlers(shared, LocalConfig.from_toml(_LOCAL_BASE))["task"]

    class GitHub:
        def __init__(self) -> None:
            self.comments: list[IssueComment] = []

        def get_permission(self, repository: str, login: str) -> str:
            return "read"

        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return self.comments

        def create_comment(self, repository: str, number: int, body: str) -> str:
            self.comments.append(IssueComment("1", body, "factory"))
            return "1"

    github = GitHub()
    task.attach_github(github)  # pyright: ignore[reportArgumentType]
    card = ProjectQueueItem(
        "P12",
        "I12",
        {shared.project.status.id: shared.project.status.option("ready")},
        SourceItem("I12", "example/work", 12, "reader", frozenset(), "Task", "OPEN"),
    )
    task.ready_handoff(card, shared, {})
    task.ready_handoff(card, shared, {})
    assert len(github.comments) == 1
    assert "agent-factory-task-handoff:v1" in github.comments[0].body
    assert shared.project.owner.id not in card.fields


def test_task_slot_is_independent_of_fix_and_feature(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    try:
        for number, kind in ((1, "fix"), (2, "feature"), (3, "task")):
            claim = store.create_claim(
                ClaimDraft(
                    "example/work", number, f"I{number}", f"P{number}", kind, f"fp-{kind}", {}
                )
            )
            store.reserve_run(
                claim.id, kind, lane="low", reason="initial", evidence_path=str(tmp_path / kind)
            )
        assert {run.kind for run in store.nonterminal_runs()} == {"fix", "feature", "task"}
        extra = store.create_claim(ClaimDraft("example/work", 4, "I4", "P4", "task", "fp-4", {}))
        try:
            store.reserve_run(
                extra.id,
                "task",
                lane="low",
                reason="initial",
                evidence_path=str(tmp_path / "extra"),
            )
        except NonterminalRunError:
            pass
        else:
            raise AssertionError("a second Task occupied the Task slot")
    finally:
        store.close()
