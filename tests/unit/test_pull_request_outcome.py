import json
from pathlib import Path

from agent_factory.work_kinds.pull_request.outcome import read_interpreted_outcome


def test_feature_outcome_accepts_well_formed_extra_fields(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "needs-input",
                "reasons": ["choose"],
                "stopped_step": "design",
                "questions": ["choose"],
                "direction_summary": "Drafted a plan",
                "branch": "claim",
                "review_attention_counts": {"red": 1, "orange": 0, "yellow": 2},
                "resume": {"from": "design"},
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is not None


def test_feature_outcome_rejects_wrong_extra_field_type(tmp_path: Path) -> None:
    (tmp_path / "feature-outcome.json").write_text(
        json.dumps(
            {
                "contract": "factory-feature/1",
                "outcome": "needs-input",
                "stopped_step": 42,
            }
        )
    )
    assert read_interpreted_outcome(tmp_path, "factory-feature/1").outcome is None
