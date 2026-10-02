"""Captured outputs from the pre-registry eval implementation.

These fixtures are intentionally immutable after the first commit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler, parse_request

GOLDENS = Path(__file__).parents[1] / "fixtures" / "eval_inputs_golden"
SHA = "a" * 40
VALIDATOR = "b" * 40
FIXTURE = "c" * 40
SOURCE = "https://github.com/Codagent-AI/agent-validator.git"


@pytest.mark.parametrize(
    ("shape", "body", "execution", "validator", "fixture"),
    [
        ("default_docker", "repetitions = 1", "docker", False, False),
        ("validator_fly", "repetitions = 1", "fly", True, False),
        ("fixture_docker", 'fixture_ref = "main"', "docker", False, True),
        ("combined_fly", 'fixture_ref = "main"', "fly", True, True),
        (
            "overrides",
            'fixture_ref = "main"\nrepetitions = 2\nskip_validator = true\nlead = "codex:default:medium"',
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
        "main", "main", {"lead": "claude:default:medium", "implementor": "codex:default:medium", "tester": "codex:default:medium"}, False, 1, execution=execution
    )
    request = parse_request(f"```eval\n{body}\n```", defaults)
    frozen = request.freeze(
        runner_sha=SHA,
        skills_sha=SHA,
        harness_sha=SHA,
        suite="and-scene",
        validator_sha=VALIDATOR if validator else None,
        validator_source=SOURCE if validator else None,
        fixture_sha=FIXTURE if fixture else None,
    )
    store = ClaimStore(tmp_path / "claims.sqlite3")
    claim = store.create_claim(ClaimDraft("repo", 1, "issue", "item", "eval", request.fingerprint, frozen.payload))
    handler = EvalHandler(defaults)
    handler.attach_store(store)
    actual = {
        "fingerprint": request.fingerprint,
        "frozen_spec": json.dumps(claim.frozen_spec),
        "refs": handler.refs_text(claim),
        "frozen_inputs": handler.frozen_inputs_event(claim),
    }
    path = GOLDENS / f"{shape}.json"
    if os.environ.get("CAPTURE_EVAL_GOLDENS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    assert actual == json.loads(path.read_text(encoding="utf-8"))
    store.close()
