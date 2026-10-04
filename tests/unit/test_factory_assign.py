"""The assignment helper must recognize every supported Factory work kind."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_factory.config import SharedConfig
from agent_factory.github import ProjectQueueItem
from agent_factory.notify.marker import parse, render
from agent_factory.notify.registry import LiveSession
from agent_factory.routing import ProjectItem, SourceItem
from agent_factory.store import ClaimDraft, ClaimStore

HELPER = Path(__file__).resolve().parents[2] / ".claude/skills/factory-assign/assign.py"
spec = importlib.util.spec_from_file_location("factory_assign_helper", HELPER)
assert spec is not None and spec.loader is not None
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.parametrize(
    ("repository", "issue_type", "expected"),
    [
        ("Codagent-AI/agent-skills", "Feature", "feature"),
        ("Codagent-AI/agent-skills", "Task", "task"),
        ("Codagent-AI/agent-skills", "Bug", "fix"),
        ("Codagent-AI/agent-evals", "Eval", "eval"),
        ("Codagent-AI/other", "Feature", None),
    ],
)
def test_kind_of(repository: str, issue_type: str, expected: str | None) -> None:
    factory = helper.Factory.__new__(helper.Factory)
    factory.shared = SimpleNamespace(
        routing=SimpleNamespace(
            eval_source="Codagent-AI/agent-evals",
            eval_type="Eval",
            bug_type="Bug",
            feature_type="Feature",
            task_type="Task",
        )
    )
    factory.targets = {"Codagent-AI/agent-skills"}
    factory.feature_targets = {"Codagent-AI/agent-skills"}
    factory.task_targets = {"Codagent-AI/agent-skills"}
    source = SimpleNamespace(repository=repository, issue_type=issue_type)
    assert factory.kind_of(source) == expected
    assert factory.wanted_type("feature") == "Feature"
    assert factory.wanted_type("task") == "Task"


def test_feature_disabled_has_no_targets() -> None:
    factory = helper.Factory.__new__(helper.Factory)
    factory.shared = SimpleNamespace(
        routing=SimpleNamespace(
            eval_source="none",
            eval_type="Eval",
            bug_type="Bug",
            feature_type="Feature",
            task_type="Task",
        )
    )
    factory.targets = {"Codagent-AI/agent-skills"}
    factory.feature_targets = set()
    factory.task_targets = set()
    assert (
        factory.kind_of(
            SimpleNamespace(repository="Codagent-AI/agent-skills", issue_type="Feature")
        )
        is None
    )


def test_apply_replaces_marker_before_board_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    first = "c2ae018f-230c-437f-bd07-ff9f49ab6a82"
    second = "c2ae018f-230c-437f-bd07-ff9f49ab6a83"
    body = "Issue prose\n\n" + render(first, "prior") + "\n"
    writes: list[str] = []
    source = SourceItem("I", "o/r", 12, "author", frozenset(), "Feature", "open", body)
    item = ProjectItem("P", "I", {})

    class App:
        def list_project_items(
            self, project_id: str, *, priority_id: str = ""
        ) -> list[ProjectQueueItem]:
            return []

        def get_source_item(self, repository: str, number: int) -> SourceItem:
            return source

        def get_permission(self, repository: str, author: str) -> str:
            return "write"

        def find_project_item(self, project_id: str, issue_id: str) -> ProjectItem:
            return item

        def set_single_select_field(
            self, project_id: str, item_id: str, field_id: str, option_id: str
        ) -> None:
            writes.append("board")

    class Paul:
        def get_source_item(self, repository: str, number: int) -> SourceItem:
            return SourceItem(
                "I",
                "o/r",
                12,
                "author",
                frozenset(),
                "Feature",
                "open",
                source.body + "\nConcurrent edit",
            )

        def ensure_issue_select_default(self, issue_id: str, field_id: str, option_id: str) -> None:
            pass

    def fake_gh(*args: str) -> str:
        nonlocal source
        writes.append("body")
        payload = json.loads(Path(args[-1]).read_text())
        source = SourceItem(
            "I", "o/r", 12, "author", frozenset(), "Feature", "open", payload["body"]
        )
        return ""

    monkeypatch.setattr(helper, "paul_gh", fake_gh)

    def live_session(sid: str) -> LiveSession:
        return LiveSession(sid, "current", 1)

    monkeypatch.setattr(helper.registry, "resolve", live_session)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", second)
    path = tmp_path / "state.sqlite3"
    ClaimStore(path).close()
    factory = helper.Factory.__new__(helper.Factory)
    factory.shared = shared
    factory.local = SimpleNamespace(state_path=path)
    factory.app = App()
    factory.paul = Paul()
    factory.targets = {"o/r"}
    factory.feature_targets = {"o/r"}
    factory.task_targets = {"o/r"}
    factory.handlers = {}
    helper.apply(factory, "o/r", 12, "feature", False)
    assert writes[0] == "body"
    assert parse(source.body)["session_id"] == second  # type: ignore[index]
    assert source.body.count("codagent-session:") == 1
    assert "Issue prose" in source.body
    assert "Concurrent edit" in source.body
    helper.report(factory, "o/r", 12, "feature")
    assert f"session: current ({second})" in capsys.readouterr().out
    writes.clear()
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    helper.apply(factory, "o/r", 12, "feature", False)
    assert "body" not in writes
    store = ClaimStore(path)
    store.create_claim(ClaimDraft("o/r", 12, "I", "P", "feature", "fp", {}))
    store.close()
    writes.clear()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", first)
    with pytest.raises(SystemExit, match="refused: claim"):
        helper.apply(factory, "o/r", 12, "feature", False)
    assert not writes


def test_apply_without_notify_package_hands_off_without_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A release that predates session notifications has no agent_factory.notify.
    monkeypatch.setitem(sys.modules, "agent_factory.notify", None)
    old_spec = importlib.util.spec_from_file_location("factory_assign_old_release", HELPER)
    assert old_spec is not None and old_spec.loader is not None
    old = importlib.util.module_from_spec(old_spec)
    old_spec.loader.exec_module(old)
    assert old.marker is None
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    body = "Issue prose\n"
    writes: list[str] = []
    source = SourceItem("I", "o/r", 12, "author", frozenset(), "Feature", "open", body)

    class App:
        def list_project_items(
            self, project_id: str, *, priority_id: str = ""
        ) -> list[ProjectQueueItem]:
            return []

        def get_source_item(self, repository: str, number: int) -> SourceItem:
            return source

        def get_permission(self, repository: str, author: str) -> str:
            return "write"

        def find_project_item(self, project_id: str, issue_id: str) -> ProjectItem:
            return ProjectItem("P", "I", {})

        def set_single_select_field(
            self, project_id: str, item_id: str, field_id: str, option_id: str
        ) -> None:
            writes.append("board")

    class Paul:
        def ensure_issue_select_default(self, issue_id: str, field_id: str, option_id: str) -> None:
            pass

    def fake_gh(*args: str) -> str:
        writes.append("body")
        return ""

    monkeypatch.setattr(old, "paul_gh", fake_gh)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "c2ae018f-230c-437f-bd07-ff9f49ab6a82")
    path = tmp_path / "state.sqlite3"
    ClaimStore(path).close()
    factory = old.Factory.__new__(old.Factory)
    factory.shared = shared
    factory.local = SimpleNamespace(state_path=path)
    factory.app = App()
    factory.paul = Paul()
    factory.targets = {"o/r"}
    factory.feature_targets = {"o/r"}
    factory.task_targets = {"o/r"}
    factory.handlers = {}
    old.apply(factory, "o/r", 12, "feature", False)
    assert writes == ["board", "board"]
    assert source.body == body
    old.report(factory, "o/r", 12, "feature")
    output = capsys.readouterr().out
    assert "session not recorded: this release has no agent_factory.notify" in output
    assert "session: none" in output
