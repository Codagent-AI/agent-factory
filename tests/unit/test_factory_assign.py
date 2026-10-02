"""The assignment helper must recognize every supported Factory work kind."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

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
