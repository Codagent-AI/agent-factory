"""Structured Task results survive supervisor fallback and reject bad contracts."""

# pyright: reportPrivateUsage=false

import json
from pathlib import Path

import pytest

from agent_factory.supervisor import _load_result
from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome


@pytest.mark.parametrize("verdict", ["pull-request", "needs-input", "failed"])
def test_task_outcome_contract_is_read_when_result_json_is_absent(
    tmp_path: Path, verdict: str
) -> None:
    outcome: dict[str, object] = {"contract": "factory-task/1", "outcome": verdict}
    if verdict == "pull-request":
        outcome["pr"] = {
            "url": "https://github.com/o/r/pull/7",
            "number": 7,
            "branch": "factory/task-7-claim",
            "head_sha": "f" * 40,
        }
    else:
        outcome["reasons"] = ["decision needed"]
    (tmp_path / "task-outcome.json").write_text(json.dumps(outcome))
    read = read_interpreted_outcome(tmp_path, "factory-task/1")
    assert read.outcome is not None
    assert read.outcome.product_verdict == verdict
    fallback = _load_result(str(tmp_path), kind="task", reason="initial")
    assert fallback.product_verdict == verdict


def test_task_outcome_rejects_wrong_contract_and_unknown_verdict(tmp_path: Path) -> None:
    path = tmp_path / "task-outcome.json"
    for value in (
        {"contract": "factory-fix/1", "outcome": "failed"},
        {"contract": "factory-task/1", "outcome": "unknown"},
    ):
        path.write_text(json.dumps(value))
        assert read_interpreted_outcome(tmp_path, "factory-task/1").outcome is None
        assert _load_result(str(tmp_path), kind="task").product_verdict is None
    path.unlink()
    assert _load_result(str(tmp_path), kind="task").product_verdict is None
