from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_factory.suites.and_scene import ReadinessError, SourceRepositories, inputs
from agent_factory.work_kinds.eval import EvalDefaults, parse_request


def test_registry_drives_keys_order_and_honored_names() -> None:
    assert [entry.name for entry in inputs.admission_order()] == [
        "runner",
        "skills",
        "validator",
        "fixture",
        "evals",
    ]
    assert inputs.worktree_names() == ("runner", "skills", "evals")
    assert inputs.honored_revisions() == ("runner", "skills", "evals", "validator", "fixture")
    assert inputs.by_name("fixture") is inputs.EVAL_INPUTS[-1]
    assert inputs.by_name("unknown") is None
    defaults = EvalDefaults("main", "main", {}, "agent-validator", 1)
    for key in ("agent_runner_ref", "agent_skills_ref", "fixture_ref"):
        assert key in parse_request(f'```eval\n{key} = "main"\n```', defaults).settings
    with pytest.raises(ValueError, match="unsupported eval setting: agent_validator_ref"):
        parse_request('```eval\nagent_validator_ref = "main"\n```', defaults)


def test_validation_workflow_uses_the_default_and_accepts_supported_selections() -> None:
    defaults = EvalDefaults("main", "main", {}, "none", 1)

    request = parse_request("```eval\nrepetitions = 1\n```", defaults)
    assert request.settings["validation_workflow"] == "none"
    for value in ("auto", "agent-validator", "none", "builtin:core/custom-v1.0.yaml"):
        request = parse_request(f'```eval\nvalidation_workflow = "{value}"\n```', defaults)
        assert request.settings["validation_workflow"] == value


@pytest.mark.parametrize(
    "value", ["builtin:/etc/x.yaml", "builtin:../x.yaml", "builtin:core/../../x.yaml"]
)
def test_validation_workflow_builtin_reference_stays_inside_runner(value: str) -> None:
    defaults = EvalDefaults("main", "main", {}, "none", 1)

    with pytest.raises(ValueError, match="validation_workflow"):
        parse_request(f'```eval\nvalidation_workflow = "{value}"\n```', defaults)


@pytest.mark.parametrize(
    ("toml", "skip", "selection"),
    [("true", True, "none"), ("false", False, "agent-validator")],
)
def test_legacy_skip_validator_maps_to_a_selection_and_keeps_its_fingerprint(
    toml: str, skip: bool, selection: str
) -> None:
    defaults = EvalDefaults("main", "main", {}, "auto", 1)

    request = parse_request(f"```eval\nskip_validator = {toml}\n```", defaults)

    assert request.settings["validation_workflow"] == selection
    assert "skip_validator" not in request.settings
    canonical = json.dumps({"skip_validator": skip}, sort_keys=True, separators=(",", ":"))
    assert request.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('validation_workflow = "None"', "validation_workflow must be"),
        ('validation_workflow = "/tmp/validate.yaml"', "validation_workflow must be"),
        ('validation_workflow = ""', "validation_workflow must be"),
        ("validation_workflow = true", "validation_workflow must be"),
        ('skip_validator = "true"', "skip_validator must be a boolean"),
        ('skip_validator = true\nvalidation_workflow = "none"', "not both"),
    ],
)
def test_invalid_validation_settings_are_rejected(body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_request(f"```eval\n{body}\n```", EvalDefaults("main", "main", {}, "auto", 1))


def test_freeze_validates_registry_inputs() -> None:
    request = parse_request(
        "```eval\nrepetitions = 1\n```", EvalDefaults("main", "main", {}, "agent-validator", 1)
    )
    base = {"runner": "a" * 40, "skills": "b" * 40, "evals": "c" * 40}
    with pytest.raises(ValueError, match="harness revision must be a full commit SHA"):
        request.freeze({**base, "evals": "bad"}, suite="and-scene")
    with pytest.raises(ValueError, match="validator source is required with its revision"):
        request.freeze({**base, "validator": "d" * 40}, suite="and-scene")
    with pytest.raises(ValueError, match="fixture revision requires fixture_ref"):
        request.freeze({**base, "fixture": "e" * 40}, suite="and-scene")
    with pytest.raises(ValueError, match="fixture revision must be a full commit SHA"):
        request.freeze({**base, "fixture": "bad"}, suite="and-scene")


def test_source_repository_lookup() -> None:
    sources = SourceRepositories(
        Path("runner"), Path("skills"), Path("evals"), extra={"sample": Path("sample")}
    )
    assert sources.checkout("runner") == Path("runner")
    assert sources.checkout("fixture") is None
    assert sources.checkout("sample") == Path("sample")
    assert sources.checkout("missing") is None


def test_registry_resolver_refuses_a_missing_checkout() -> None:
    runner = inputs.by_name("runner")
    assert runner is not None
    with pytest.raises(ReadinessError, match="source checkout is not configured"):
        runner.resolve(None, "main")


def test_revision_input_rejects_positional_fields() -> None:
    def resolve(_checkout: Path | None, ref: str) -> str:
        return ref

    with pytest.raises(TypeError, match="positional"):
        inputs.RevisionInput(
            "sample",  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
            noun="sample",
            required=False,
            setting=None,
            requestable=False,
            has_default=False,
            admission_rank=0,
            resolve=resolve,
        )
