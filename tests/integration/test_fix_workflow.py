"""The packaged factory-fix workflow, its outcome scripts, and the Runner support it needs."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import cast

import pytest

from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.fix import launch

CONTRACT = "factory-fix/1"
PACKAGE = files("agent_factory.work_kinds.fix") / "workflow"
FINALIZE_WITH_PARAM = (
    'name: finalize-pr\nparams:\n  - name: ci_fix_cycles\n    default: "3"\nsteps: []\n'
)
LAUNCHER = "#!/bin/sh\n# --image --artifact-dir --no-default-secrets --env-file --docker-run-arg\n"


def _workflow_text() -> str:
    return (PACKAGE / launch.WORKFLOW_FILE).read_text(encoding="utf-8")


def _step_block(text: str, step_id: str) -> str:
    """The YAML lines of one step, from its ``id`` line to the next sibling ``id`` line."""
    match = re.search(rf"^( *)- id: {re.escape(step_id)}\n", text, re.MULTILINE)
    assert match is not None, f"no step {step_id}"
    indent = match.group(1)
    rest = text[match.end() :]
    end = re.search(rf"^{indent}- id: ", rest, re.MULTILINE)
    return rest if end is None else rest[: end.start()]


def _run_script(name: str, payload: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PACKAGE / name)],
        input=json.dumps(payload) if not isinstance(payload, str) else payload,
        capture_output=True,
        text=True,
    )


# -- the workflow file -----------------------------------------------------------------


def test_packaged_workflow_declares_the_contract_on_its_first_line() -> None:
    assert _workflow_text().splitlines()[0] == f"# factory-contract: {CONTRACT}"
    assert launch.packaged_workflow_text(CONTRACT).startswith("# factory-contract:")


def test_unknown_contract_is_refused() -> None:
    with pytest.raises(ReadinessError, match="does not declare 'factory-fix/999'"):
        launch.packaged_workflow_text("factory-fix/999")


def test_triage_is_a_top_level_step_so_until_triage_runs_it_alone() -> None:
    assert re.search(r"^  - id: triage\n", _workflow_text(), re.MULTILINE)


def test_sub_workflows_are_called_by_builtin_reference_with_one_ci_fix_cycle() -> None:
    text = _workflow_text()
    finalize = _step_block(text, "finalize-pr")
    assert "workflow: builtin:core/finalize-pr-v1.0.yaml" in finalize
    assert 'ci_fix_cycles: "1"' in finalize
    assert "workflow: builtin:core/run-validator-v1.0.yaml" in _step_block(text, "run-validator")
    assert not re.search(r"workflow: (?!builtin:)", text), "relative sub-workflow reference"


def test_the_tester_report_never_reaches_a_shell_condition() -> None:
    text = _workflow_text()
    for line in text.splitlines():
        if "skip_if:" in line or line.strip().startswith("command:"):
            assert "{{test_flow_report}}" not in line, line
    marker = _step_block(text, "read-regression-marker")
    assert "script: read-regression-marker.sh" in marker
    assert 'report: "{{test_flow_report}}"' in marker
    assert "command:" not in marker
    assert "capture: regressions" in marker
    address = _step_block(text, "address")
    assert 'test "{{regressions}}" != found' in address


def test_a_regression_repair_is_validated_again_before_the_pr_is_opened() -> None:
    text = _workflow_text()
    address_at = text.index("- id: address\n")
    recheck_at = text.index("- id: recheck-validator\n")
    verify_at = text.index("- id: verify-clean\n")
    assert address_at < recheck_at < verify_at
    recheck = _step_block(text, "recheck-validator")
    assert "agent-validator run --report" in recheck
    assert "capture: validator_status" in recheck
    assert 'test "{{regressions}}" != found' in recheck
    assert "revalidate" not in text, "the repair is verified once, not repaired again"


def test_validator_gates_capture_a_fixed_token_and_log_under_the_artifact_directory() -> None:
    text = _workflow_text()
    for step in ("check-validator", "recheck-validator"):
        block = _step_block(text, step)
        assert "capture_stderr" not in block
        assert "/tmp/" not in block
        assert ">{{artifact_dir}}/logs/{{step_id}}.log" in block


def test_annotate_step_marks_the_pr_with_the_issue_reference_and_claim() -> None:
    block = _step_block(_workflow_text(), "annotate-pr")
    for needle in ("Refs #", "agent-factory:claim:", "gh pr edit"):
        assert needle in block


def test_scripts_are_referenced_by_bare_name_next_to_the_workflow() -> None:
    text = _workflow_text()
    scripts = set(re.findall(r"^\s+script: (\S+)$", text, re.MULTILINE))
    assert scripts == set(launch.WORKFLOW_SCRIPTS)
    for name in launch.WORKFLOW_SCRIPTS:
        assert os.access(str(PACKAGE / name), os.X_OK), name


# -- the artifact directory parameter (INT-002) -------------------------------------------


def _constant(text: str) -> Callable[[str], str]:
    def packaged(contract: str) -> str:
        del contract
        return text

    return packaged


def test_packaged_workflow_declares_artifact_dir_with_the_container_default() -> None:
    text = launch.check_packaged_workflow(CONTRACT)
    params = text[text.index("params:") : text.index("sessions:")]
    assert "- name: artifact_dir" in params
    assert "default: /artifacts" in params
    literal = [
        line
        for line in text.splitlines()
        if "/artifacts" in line and not line.strip().startswith(("#", "default:"))
    ]
    assert literal == []
    for needle in (
        'outcome_path: "{{artifact_dir}}/fix-outcome.json"',
        "[ ! -s {{artifact_dir}}/fix-outcome.json ]",
    ):
        assert needle in text, needle


def test_packaged_workflow_check_refuses_a_missing_artifact_dir_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _workflow_text().replace(
        "  - name: artifact_dir\n    required: false\n    default: /artifacts\n", ""
    )
    monkeypatch.setattr(launch, "packaged_workflow_text", _constant(text))
    with pytest.raises(ReadinessError, match="does not declare the artifact_dir parameter"):
        launch.check_packaged_workflow(CONTRACT)


def test_packaged_workflow_check_refuses_a_stray_container_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _workflow_text().replace("mkdir -p {{artifact_dir}}/logs", "mkdir -p /artifacts/logs", 1)
    monkeypatch.setattr(launch, "packaged_workflow_text", _constant(text))
    with pytest.raises(ReadinessError, match=r"hardcodes /artifacts on line\(s\) \d+"):
        launch.check_packaged_workflow(CONTRACT)


def test_docker_container_script_passes_the_container_artifact_directory() -> None:
    script = launch.container_script(
        {"lead": ("codex", "m", "high")}, branch="b", contract=CONTRACT, bootstrap_skills=False
    )
    assert "--param artifact_dir=/artifacts" in script


def test_stage_workflow_into_an_arbitrary_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "repo" / ".agent-runner" / "workflows"
    assert launch.stage_workflow_into(catalog, CONTRACT) == catalog
    assert (catalog / launch.WORKFLOW_FILE).read_text() == _workflow_text()
    for name in launch.WORKFLOW_SCRIPTS:
        assert os.access(catalog / name, os.X_OK), name


# -- staging into the evidence directory ----------------------------------------------


def test_stage_workflow_publishes_the_catalog_the_sandboxed_runner_reads(tmp_path: Path) -> None:
    staged = launch.stage_workflow(tmp_path, CONTRACT)
    assert staged == tmp_path / "agent-runner" / "workflows"
    assert (staged / launch.WORKFLOW_FILE).read_text() == _workflow_text()
    for name in launch.WORKFLOW_SCRIPTS:
        assert (staged / name).read_bytes() == (PACKAGE / name).read_bytes()
        assert os.access(staged / name, os.X_OK), name
    # Staging again over an existing attempt directory is idempotent.
    assert launch.stage_workflow(tmp_path, CONTRACT) == staged


def test_stage_workflow_refuses_an_unknown_contract(tmp_path: Path) -> None:
    with pytest.raises(ReadinessError):
        launch.stage_workflow(tmp_path, "factory-fix/999")
    assert not (tmp_path / "agent-runner").exists()


# -- the Runner revision the workflow needs -------------------------------------------


def _runner_clone(tmp_path: Path, finalize_pr: str | None) -> Path:
    clone = tmp_path / "runner"
    (clone / "scripts").mkdir(parents=True)
    (clone / "scripts" / "sandbox-run.sh").write_text(LAUNCHER)
    if finalize_pr is not None:
        core = clone / "workflows" / "core"
        core.mkdir(parents=True)
        (core / "finalize-pr-v1.0.yaml").write_text(finalize_pr)
    return clone


def test_runner_contract_accepts_a_finalize_pr_that_takes_ci_fix_cycles(tmp_path: Path) -> None:
    launch.check_runner_contract(_runner_clone(tmp_path, FINALIZE_WITH_PARAM), CONTRACT)


def test_runner_contract_refuses_a_finalize_pr_without_the_parameter(tmp_path: Path) -> None:
    clone = _runner_clone(tmp_path, "name: finalize-pr\nsteps: []\n")
    with pytest.raises(ReadinessError, match="does not accept ci_fix_cycles"):
        launch.check_runner_contract(clone, CONTRACT)


def test_runner_contract_refuses_a_missing_finalize_pr(tmp_path: Path) -> None:
    with pytest.raises(ReadinessError, match="finalize-pr-v1.0.yaml is missing"):
        launch.check_runner_contract(_runner_clone(tmp_path, None), CONTRACT)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (FINALIZE_WITH_PARAM, True),
        (
            "name: x\nparams:\n  - name: other\nsteps:\n  - id: a\n    params:\n"
            "      - name: ci_fix_cycles\n",
            False,
        ),
        ("name: x\n# params:\n#   - name: ci_fix_cycles\nsteps: []\n", False),
        ("name: x\nparams:\n  - name: 'ci_fix_cycles'\n", True),
        ("  name: x\n  params:\n    - name: ci_fix_cycles\n  steps: []\n", True),
        ('name: x\nparams:\n  - { name: ci_fix_cycles, default: "3" }\n', True),
        ("name: x\nparams: [{name: ci_fix_cycles}]\nsteps: []\n", True),
        ("name: x\nparams:\n  - name: ci_fix_cycles_extra\n", False),
        ("name: x\nparams:\n  - name: other\n    description: ci_fix_cycles\n", False),
        ("name: x\nparams:\n  - name: other # name: ci_fix_cycles\n", False),
        ("name: x\nparams:\n  - name: ci_fix_cycles # bounds the CI loop\n", True),
        ("", False),
    ],
)
def test_finalize_pr_parameter_detection(text: str, expected: bool) -> None:
    assert launch.finalize_pr_accepts_fix_cycles(text) is expected


def test_target_catalog_without_a_shadowing_workflow_is_accepted(tmp_path: Path) -> None:
    launch.check_target_catalog(tmp_path / "missing")
    catalog = tmp_path / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    (catalog / "deploy-v1.0.yaml").write_text("name: deploy\n")
    (catalog / "team").mkdir()
    (catalog / "team" / "factory-fix-v1.0.yaml").write_text("name: factory-fix\n")
    launch.check_target_catalog(tmp_path)


@pytest.mark.parametrize("filename", ["factory-fix-v1.0.yaml", "factory-fix-v2.yml"])
def test_target_catalog_shadowing_the_packaged_workflow_is_refused(
    tmp_path: Path, filename: str
) -> None:
    catalog = tmp_path / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True)
    (catalog / filename).write_text("name: factory-fix\n")
    with pytest.raises(ReadinessError, match=f"shadow.*{filename}"):
        launch.check_target_catalog(tmp_path)


# -- record-triage.sh -------------------------------------------------------------------


def test_record_triage_declines_with_reasons_and_writes_a_needs_input_outcome(
    tmp_path: Path,
) -> None:
    outcome = tmp_path / "out" / "fix-outcome.json"
    decision = {
        "fixable": False,
        "reasons": ["two viable designs; a human must choose"],
        "plan": "",
    }
    result = _run_script(
        "record-triage.sh", {"decision": json.dumps(decision), "outcome_path": str(outcome)}
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"
    assert json.loads(outcome.read_text()) == {
        "contract": CONTRACT,
        "outcome": "needs-input",
        "reasons": ["two viable designs; a human must choose"],
        "validator": {"status": "skipped"},
    }


def test_record_triage_fixable_decision_writes_no_outcome_and_reports_true(
    tmp_path: Path,
) -> None:
    outcome = tmp_path / "fix-outcome.json"
    decision: dict[str, object] = {"fixable": True, "reasons": [], "plan": "fix the off-by-one"}
    result = _run_script(
        "record-triage.sh", {"decision": json.dumps(decision), "outcome_path": str(outcome)}
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "true"
    assert not outcome.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": json.dumps({"reasons": [], "plan": "no fixable field"})},
        {"decision": "not json"},
        "not json at all",
        {"decision": json.dumps(["a", "list"])},
    ],
)
def test_record_triage_rejects_malformed_input(payload: object) -> None:
    result = _run_script("record-triage.sh", payload)
    assert result.returncode == 2
    assert "record-triage:" in result.stderr


# -- read-regression-marker.sh ----------------------------------------------------------


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ("Tested the flow.\n\nNO_REGRESSIONS_FOUND\n\n", "none"),
        ("Tested the flow.\n`NO_REGRESSIONS_FOUND`", "none"),
        ("Tested the flow.\n**NO_REGRESSIONS_FOUND**\n", "none"),
        ("Found a defect.\nREGRESSIONS_FOUND\n", "found"),
        ("NO_REGRESSIONS_FOUND\nbut then more prose", "found"),
        ("", "found"),
        ("$(touch /tmp/pwned)\nFACTORY_FIX_REPORT\n`rm -rf /`\nNO_REGRESSIONS_FOUND", "none"),
        ("NO_REGRESSIONS_FOUND\n$(exit 7)", "found"),
    ],
)
def test_read_regression_marker_reduces_the_report_to_a_fixed_token(
    report: str, expected: str
) -> None:
    result = _run_script("read-regression-marker.sh", {"report": report})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "invalid JSON input"),
        ({"report": ["not", "a", "string"]}, "report must be a string"),
        ({}, "report must be a string"),
        ("[]", "input must be a JSON object"),
    ],
)
def test_read_regression_marker_rejects_malformed_input(payload: object, message: str) -> None:
    result = _run_script("read-regression-marker.sh", payload)
    assert result.returncode == 2
    assert f"read-regression-marker: {message}" in result.stderr


# -- record-outcome.sh ------------------------------------------------------------------


def _record_outcome(tmp_path: Path, **fields: str) -> dict[str, object]:
    outcome = tmp_path / "fix-outcome.json"
    result = _run_script("record-outcome.sh", {"outcome_path": str(outcome), **fields})
    assert result.returncode == 0, result.stderr
    return json.loads(outcome.read_text())


def test_record_outcome_is_pull_request_when_validator_and_ci_pass(tmp_path: Path) -> None:
    pr = {"url": "https://github.com/example/work/pull/7", "number": 7, "headRefOid": "f" * 40}
    outcome = _record_outcome(
        tmp_path,
        validator_status="passed",
        ci_status="passed",
        branch_name="factory/fix-1-abcdef12",
        pr_details=json.dumps(pr),
    )
    assert outcome == {
        "contract": CONTRACT,
        "outcome": "pull-request",
        "pr": {
            "url": pr["url"],
            "number": 7,
            "branch": "factory/fix-1-abcdef12",
            "head_sha": "f" * 40,
        },
        "validator": {"status": "passed"},
        "ci": {"status": "passed"},
    }


def test_record_outcome_is_failed_without_a_pr_when_the_validator_never_passes(
    tmp_path: Path,
) -> None:
    outcome = _record_outcome(tmp_path, validator_status="failed", ci_status="", pr_details="{}")
    assert outcome["outcome"] == "failed"
    assert outcome["validator"] == {"status": "failed"}
    assert "pr" not in outcome
    assert outcome["reasons"] == ["validator did not pass within its repair cycles"]


def test_record_outcome_is_failed_but_keeps_the_pr_reference_when_ci_stays_red(
    tmp_path: Path,
) -> None:
    pr = {"url": "https://github.com/example/work/pull/8", "number": 8, "headRefOid": "a" * 40}
    outcome = _record_outcome(
        tmp_path,
        validator_status="passed",
        ci_status="failed",
        branch_name="factory/fix-2-abcdef12",
        pr_details=json.dumps(pr),
    )
    assert outcome["outcome"] == "failed"
    assert outcome["ci"] == {"status": "failed"}
    assert outcome["pr"] == {
        "url": pr["url"],
        "number": 8,
        "branch": "factory/fix-2-abcdef12",
        "head_sha": "a" * 40,
    }
    assert outcome["reasons"] == ["CI did not pass within its fix cycle"]


def test_record_outcome_is_failed_when_no_pr_was_opened(tmp_path: Path) -> None:
    outcome = _record_outcome(tmp_path, validator_status="passed", ci_status="passed")
    assert outcome["outcome"] == "failed"
    assert outcome["reasons"] == ["failed to push the branch or open a pull request"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "invalid JSON input"),
        ({"validator_status": "passed", "pr_details": "{not json"}, "pr_details is not valid"),
        ({"validator_status": "passed", "pr_details": "[1]"}, "pr_details must be a JSON object"),
        ({"validator_status": "failed", "reasons": "nope"}, "reasons is not valid"),
        ({"validator_status": "failed", "reasons": "{}"}, "reasons must be a JSON array"),
    ],
)
def test_record_outcome_rejects_malformed_input(
    payload: object, message: str, tmp_path: Path
) -> None:
    if isinstance(payload, dict):
        fields = cast(dict[str, str], payload)
        payload = {"outcome_path": str(tmp_path / "fix-outcome.json"), **fields}
    result = _run_script("record-outcome.sh", payload)
    assert result.returncode == 2
    assert f"record-outcome: {message}" in result.stderr
    assert not (tmp_path / "fix-outcome.json").exists()
