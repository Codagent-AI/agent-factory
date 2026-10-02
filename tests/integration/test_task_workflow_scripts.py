"""Task kind configuration, staged catalog, and deterministic boundaries."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

WORKFLOW = Path("src/agent_factory/work_kinds/pull_request/workflow")


def run(
    command: list[str], *, cwd: Path | None = None, data: object | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        input=json.dumps(data) if data is not None else None,
        check=False,
    )


def git(repo: Path, *args: str) -> str:
    result = run(["git", *args], cwd=repo)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Tester")
    git(repo, "config", "user.email", "tester@example.com")
    (repo / "README.md").write_text("initial\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    return repo, git(repo, "rev-parse", "HEAD")


def test_record_triage_task_decline_and_fix_default_are_distinct(tmp_path: Path) -> None:
    script = WORKFLOW / "record-triage.sh"
    decision = {
        "doable": False,
        "reasons": ["belongs in a Feature"],
        "plan": "",
        "choices": [],
        "gates": [],
        "user_visible": False,
    }
    path = tmp_path / "task-outcome.json"
    result = run(
        ["sh", str(script)],
        data={
            "decision": json.dumps(decision),
            "outcome_path": str(path),
            "contract": "factory-task/1",
            "accept_field": "doable",
            "decision_path": str(tmp_path / "task-triage.json"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "false"
    assert json.loads(path.read_text())["contract"] == "factory-task/1"
    assert json.loads((tmp_path / "task-triage.json").read_text()) == decision
    fix = tmp_path / "fix-outcome.json"
    result = run(
        ["sh", str(script)],
        data={
            "decision": json.dumps(
                {"fixable": False, "reasons": ["missing reproduction"], "plan": ""}
            ),
            "outcome_path": str(fix),
        },
    )
    assert result.returncode == 0
    assert json.loads(fix.read_text())["contract"] == "factory-fix/1"


def test_record_triage_requires_task_fields_under_a_configured_contract(tmp_path: Path) -> None:
    # The Task contract is configurable, so the Task schema must not depend on its name.
    script = WORKFLOW / "record-triage.sh"
    incomplete: dict[str, object] = {"doable": True, "reasons": [], "plan": "Tighten lint."}
    result = run(
        ["sh", str(script)],
        data={
            "decision": json.dumps(incomplete),
            "outcome_path": str(tmp_path / "task-outcome.json"),
            "contract": "factory-task/2",
            "accept_field": "doable",
        },
    )
    assert result.returncode != 0
    assert "choices, gates, and user_visible" in result.stderr


def test_task_scope_floor_and_commit_normalization(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    (repo / "README.md").write_text("changed\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "[implement-task] feat: update docs")
    before_tree = git(repo, "rev-parse", "HEAD^{tree}")
    result = run(
        ["python3", str(WORKFLOW.resolve() / "normalize-chore-commits.py"), base], cwd=repo
    )
    assert result.returncode == 0, result.stderr
    assert git(repo, "log", "-1", "--format=%s") == "[implement-task] chore: update docs"
    assert git(repo, "rev-parse", "HEAD^{tree}") == before_tree
    assert git(repo, "status", "--porcelain") == ""
    (repo / "CODEOWNERS").write_text("* @owner\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: owners")
    result = run(["python3", str(WORKFLOW.resolve() / "task-scope-floor.py"), base], cwd=repo)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["CODEOWNERS"]


def test_normalize_chore_commits_refuses_pushed_commits_and_merge(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    script = str(WORKFLOW.resolve() / "normalize-chore-commits.py")
    (repo / "README.md").write_text("changed\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: docs")
    bare = tmp_path / "origin.git"
    run(["git", "init", "--bare", "-q", str(bare)])
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "-u", "origin", "HEAD")
    head = git(repo, "rev-parse", "HEAD")
    refused = run(["python3", script, base], cwd=repo)
    assert refused.returncode != 0 and "already pushed" in refused.stderr
    assert git(repo, "rev-parse", "HEAD") == head
    # A review round: the branch has an upstream, and only the round's commits are rewritten.
    (repo / "round.txt").write_text("round\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "[implement-task-plan] fix: address review")
    round_tree = git(repo, "rev-parse", "HEAD^{tree}")
    normalized = run(["python3", script, head], cwd=repo)
    assert normalized.returncode == 0, normalized.stderr
    assert git(repo, "log", "-1", "--format=%s") == "[implement-task-plan] chore: address review"
    assert git(repo, "rev-parse", "HEAD^{tree}") == round_tree
    assert git(repo, "rev-parse", "HEAD~1") == head
    git(repo, "reset", "-q", "--hard", head)
    git(repo, "branch", "--unset-upstream")
    git(repo, "remote", "remove", "origin")
    main_branch = git(repo, "branch", "--show-current")
    git(repo, "checkout", "-q", "-b", "side", base)
    (repo / "side.txt").write_text("side\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feat: side")
    git(repo, "checkout", "-q", main_branch)
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
    head = git(repo, "rev-parse", "HEAD")
    refused = run(["python3", script, base], cwd=repo)
    assert refused.returncode != 0 and "merge commit" in refused.stderr
    assert git(repo, "rev-parse", "HEAD") == head


def test_gate_exercise_requires_matching_command_and_confirmed_diagnostic(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "task-triage.json").write_text(
        json.dumps({"gates": [{"name": "lint", "command": "make lint"}]})
    )
    (evidence / "good.log").write_text("clean\n")
    (evidence / "bad.log").write_text("lint: planted violation\n")
    (evidence / "plant.patch").write_text("diff --git a/x b/x\n")
    exercise = {
        "name": "lint",
        "command": "make lint",
        "positive": {"command": "make lint", "exit": 0, "log": "good.log"},
        "negative": {"command": "make lint", "exit": 1, "log": "bad.log", "patch": "plant.patch"},
    }
    (evidence / "gate-exercises.json").write_text(json.dumps([exercise]))
    (evidence / "gate-verdicts.json").write_text(
        json.dumps(
            [
                {
                    "name": "lint",
                    "confirmed": True,
                    "criterion_met": True,
                    "diagnostic": "lint: planted violation",
                    "reason": "",
                }
            ]
        )
    )
    checker = str(WORKFLOW.resolve() / "check-gate-exercises.py")
    assert run(["python3", checker, str(evidence)], cwd=repo).returncode == 0
    negative = exercise["negative"]
    assert isinstance(negative, dict)
    negative["command"] = "make test"
    (evidence / "gate-exercises.json").write_text(json.dumps([exercise]))
    assert run(["python3", checker, str(evidence)], cwd=repo).returncode != 0


def test_final_gate_inventory_merges_sources_instead_of_requiring_agreement(
    tmp_path: Path,
) -> None:
    """Acceptance F6: independent sessions naming one check differently add an exercise."""
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    checker = str(WORKFLOW.resolve() / "check-gate-inventory.py")
    triage = evidence / "task-triage.json"
    inventory = evidence / "final-gates-prepush.json"
    changes = evidence / "gate-changes.json"
    derived = evidence / "diff-gates-prepush.json"
    lint = {"name": "ruff-strict-lint", "command": "ruff check .", "violation": "B006 default"}
    dup = {"name": "jscpd", "command": "npx --yes jscpd src", "violation": "50+ token copy"}
    triage.write_text(json.dumps({"gates": [lint, dup]}))
    command = ["python3", checker, str(triage), str(inventory), str(changes), str(derived)]

    def final() -> list[dict[str, str]]:
        return json.loads(inventory.read_text())["gates"]

    # R1: the lead dropped jscpd and kept triage's lint command; the implementor pinned
    # jscpd; the tester derived what CI actually runs under the same name.
    inventory.write_text(json.dumps({"gates": [lint]}))
    changes.write_text(json.dumps({"gates": [{**dup, "command": "npx --yes jscpd@4.3.0 src"}]}))
    full_lint = "ruff check . && ruff format --check ."
    derived.write_text(
        json.dumps({"gates": [{**lint, "command": full_lint, "violation": "unformatted file"}]})
    )
    result = run(command)
    assert result.returncode == 0, result.stderr
    assert [(gate["name"], gate["command"]) for gate in final()] == [
        ("ruff-strict-lint", "ruff check ."),
        ("jscpd", "npx --yes jscpd@4.3.0 src"),
        ("ruff-strict-lint (diff)", full_lint),
    ]
    # R2: a review round whose two sessions named the same check differently.
    c4 = {
        "name": "ruff C4 (flake8-comprehensions) lint rule",
        "command": "ruff check --select C4 .",
    }
    inventory.write_text(json.dumps({"gates": [{**c4, "violation": "list(x for x in y)"}]}))
    review = evidence / "review-gates.json"
    review.write_text(json.dumps({"gates": [{**c4, "violation": "list(x for x in y)"}]}))
    derived.write_text(
        json.dumps(
            {"gates": [{"name": "ruff-lint-C4", "command": "ruff check .", "violation": "C400"}]}
        )
    )
    result = run(["python3", checker, str(review), str(inventory), "", str(derived)])
    assert result.returncode == 0, result.stderr
    assert {gate["name"] for gate in final()} == {c4["name"], "ruff-lint-C4"}
    # The same command under another name is already covered: no duplicate exercise.
    derived.write_text(
        json.dumps({"gates": [{"name": "c4", "command": c4["command"], "violation": "C400"}]})
    )
    inventory.write_text(json.dumps({"gates": [{**c4, "violation": "list(x for x in y)"}]}))
    assert run(["python3", checker, str(review), str(inventory), "", str(derived)]).returncode == 0
    assert len(final()) == 1
    # A gate that cannot be exercised still stops the guard.
    derived.write_text(json.dumps({"gates": [{"name": "new", "command": "make new"}]}))
    failed = run(["python3", checker, str(review), str(inventory), "", str(derived)])
    assert failed.returncode != 0
    assert "needs a name, command and violation" in failed.stderr
    (evidence / "gate-exercises.json").write_text("[]")
    (evidence / "gate-verdicts.json").write_text("[]")
    inventory.write_text(json.dumps({"gates": [lint]}))
    exercise_checker = str(WORKFLOW.resolve() / "check-gate-exercises.py")
    assert run(["python3", exercise_checker, str(evidence), str(inventory)]).returncode != 0


def test_scope_outcomes_stop_before_push_and_keep_postfinalize_pr(tmp_path: Path) -> None:
    script = (WORKFLOW / "record-outcome.sh").resolve()
    pre = tmp_path / "scope-prepush.json"
    post = tmp_path / "scope-postfinalize.json"
    output = tmp_path / "task-outcome.json"
    pre.write_text(
        json.dumps({"crossed": True, "reasons": ["belongs in a Feature"], "complete": True})
    )
    payload = {
        "contract": "factory-task/1",
        "outcome_path": str(output),
        "scope_path": str(pre),
        "post_scope_path": str(post),
        "validator_status": "passed",
        "ci_status": "passed",
        "branch_name": "factory/task-1-abc",
        "pr_details": json.dumps({"url": "https://github.com/example/repo/pull/3", "number": 3}),
    }
    result = run(["sh", str(script)], data=payload)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(output.read_text())
    assert outcome["outcome"] == "needs-input"
    assert "pr" not in outcome
    pre.write_text(json.dumps({"crossed": False, "reasons": [], "complete": True}))
    post.write_text(
        json.dumps({"crossed": True, "reasons": ["gate exercise failed"], "complete": True})
    )
    # CI repair moved HEAD past the recorded pre-finalize head, so the post guard applies.
    (tmp_path / "clone").mkdir()
    repo, prefinalize = repository(tmp_path / "clone")
    (repo / "ci.txt").write_text("repair\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "[fix-pr] ci repair")
    payload["prefinalize_head"] = prefinalize
    result = run(["sh", str(script)], cwd=repo, data=payload)
    assert result.returncode == 0, result.stderr
    outcome = json.loads(output.read_text())
    assert outcome["outcome"] == "failed"
    assert outcome["pr"]["url"] == "https://github.com/example/repo/pull/3"
    post.unlink()
    assert run(["sh", str(script)], cwd=repo, data=payload).returncode == 0
    assert (
        "post-finalize Task scope guard did not complete"
        in json.loads(output.read_text())["reasons"]
    )
    payload["prefinalize_head"] = git(repo, "rev-parse", "HEAD")
    assert run(["sh", str(script)], cwd=repo, data=payload).returncode == 0
    assert json.loads(output.read_text())["outcome"] == "pull-request"
    post.write_text("{")
    assert run(["sh", str(script)], cwd=repo, data=payload).returncode == 0
    malformed = json.loads(output.read_text())
    assert malformed["outcome"] == "failed"
    assert malformed["pr"]["url"] == "https://github.com/example/repo/pull/3"
    assert "unreadable" in malformed["reasons"][0]


def test_scope_floor_distinguishes_check_only_and_release_workflows(tmp_path: Path) -> None:
    repo, base = repository(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "name: lint\non: push\njobs:\n  check:\n    runs-on: ubuntu-latest\n"
    )
    (repo / "ruff.toml").write_text("line-length = 100\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: checks")
    script = str(WORKFLOW.resolve() / "task-scope-floor.py")
    result = run(["python3", script, base], cwd=repo)
    assert json.loads(result.stdout) == []
    (workflows / "tag.yml").write_text("name: CI\non:\n  push:\n    tags:\n      - v*\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "chore: tags")
    result = run(["python3", script, base], cwd=repo)
    assert json.loads(result.stdout) == [".github/workflows/tag.yml"]


def test_record_scope_floor_overrides_a_clean_lead_verdict(tmp_path: Path) -> None:
    scope = tmp_path / "scope-prepush.json"
    script = WORKFLOW / "record-scope.sh"
    result = run(
        ["sh", str(script)],
        data={
            "floor": json.dumps(["CODEOWNERS"]),
            "decision": json.dumps({"crossed": False, "reasons": []}),
            "scope_path": str(scope),
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "crossed"
    recorded = json.loads(scope.read_text())
    assert recorded["crossed"] is True
    assert "CODEOWNERS" in recorded["reasons"][0]


def test_annotate_chore_pr_is_idempotent_and_records_title_failure(tmp_path: Path) -> None:
    repo, _ = repository(tmp_path)
    issue = tmp_path / "issue.json"
    issue.write_text(json.dumps({"number": 7, "claim_id": "claim-7"}))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "task-choices.json").write_text('[{"choice":"baseline"}]')
    (evidence / "gate-exercises.json").write_text("[]")
    state = tmp_path / "pr.json"
    state.write_text(json.dumps({"body": "Original", "title": "fix: lint", "patches": []}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['PR_STATE'])
state = json.loads(p.read_text())
args = sys.argv[1:]
if args[:2] == ['pr', 'list']:
    print(json.dumps([{'number': 3}]))
elif args[:1] == ['api'] and '-X' not in args:
    print(json.dumps(state))
elif args[:1] == ['api'] and '-X' in args:
    fields = dict(arg.split('=', 1) for i, arg in enumerate(args) if i and args[i-1] == '-f')
    state['patches'].append(fields)
    if os.environ.get('FAIL_TITLE') and 'title' in fields:
        p.write_text(json.dumps(state)); print('title rejected', file=sys.stderr); sys.exit(1)
    state.update(fields)
    p.write_text(json.dumps(state))
    print(json.dumps(state))
else:
    sys.exit(2)
""")
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "PR_STATE": str(state),
    }
    script = WORKFLOW.resolve() / "annotate-chore-pr.sh"
    payload = {"artifact_dir": str(evidence), "issue_file": str(issue)}

    def annotate(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(script)],
            cwd=repo,
            env=environment,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
        )

    first = annotate(env)
    assert first.returncode == 0 and first.stdout == "passed", first.stderr
    saved = json.loads(state.read_text())
    assert len(saved["patches"]) == 1
    assert saved["title"] == "chore: lint"
    assert "Refs #7" in saved["body"]
    assert "agent-factory:claim:claim-7" in saved["body"]
    assert "agent-factory:task-evidence" in saved["body"]
    assert annotate(env).returncode == 0
    assert len(json.loads(state.read_text())["patches"]) == 1
    (evidence / "gate-exercises.json").write_text('[{"name":"new-gate"}]')
    assert annotate(env).returncode == 0
    saved = json.loads(state.read_text())
    assert len(saved["patches"]) == 2
    assert "new-gate" in saved["body"]
    assert saved["body"].count("agent-factory:task-evidence -->") == 1
    saved["body"] = saved["body"].replace(
        "<!-- agent-factory:task-evidence-end -->", "\n\nHuman note"
    )
    state.write_text(json.dumps(saved))
    assert annotate(env).returncode == 0
    saved = json.loads(state.read_text())
    assert "Human note" in saved["body"]
    assert "agent-factory:task-evidence-end" in saved["body"]
    saved["title"] = "fix: lint"
    state.write_text(json.dumps(saved))
    failure = annotate({**env, "FAIL_TITLE": "1"})
    assert failure.returncode == 0 and failure.stdout == "passed"
    assert "title rejected" in (evidence / "retitle-failed").read_text()


def test_review_scope_crossings_map_to_needs_input_and_failed(tmp_path: Path) -> None:
    result_path = tmp_path / "implement-result.json"
    outcome_path = tmp_path / "review-outcome.json"
    payload = {
        "decision": json.dumps(
            {"needs_input": [], "items": [{"id": "comment-1", "decision": "change"}]}
        ),
        "changes_needed": "true",
        "merge_status": "none",
        "result_path": str(result_path),
        "outcome_path": str(outcome_path),
    }
    script = WORKFLOW / "record-review-outcome.sh"
    for mode, expected in (("prepush", "needs-input"), ("postfinalize", "failed")):
        result_path.write_text(
            json.dumps(
                {
                    "validator": {"status": "passed"},
                    "ci": {"status": "passed"},
                    "scope": {mode: {"crossed": True, "reasons": ["belongs in a Feature"]}},
                }
            )
        )
        result = run(["sh", str(script)], data=payload)
        assert result.returncode == 0, result.stderr
        outcome = json.loads(outcome_path.read_text())
        assert outcome["outcome"] == expected
        assert outcome["reasons"] == ["belongs in a Feature"]


def test_scope_state_chore_check_and_json_flag_helpers(tmp_path: Path) -> None:
    scope_state = str(WORKFLOW.resolve() / "scope-state.py")
    scope = tmp_path / "scope-prepush.json"
    status = run(["python3", scope_state, "status", str(scope)])
    assert status.stdout == "crossed"  # a missing guard result never permits a push
    scope.write_text(json.dumps({"crossed": False, "reasons": []}) + "\n")
    assert run(["python3", scope_state, "complete", str(scope)]).returncode == 0
    assert run(["python3", scope_state, "status", str(scope)]).stdout == "clean"
    marked = run(["python3", scope_state, "cross", str(scope), "gate exercise failed"])
    assert marked.returncode == 0, marked.stderr
    assert json.loads(scope.read_text()) == {
        "crossed": True,
        "reasons": ["gate exercise failed"],
        "complete": True,
    }
    assert run(["python3", scope_state, "status", str(scope)]).stdout == "crossed"

    flag = str(WORKFLOW.resolve() / "json-flag.py")
    triage = tmp_path / "task-triage.json"
    assert run(["python3", flag, str(triage), "gates"]).stdout == "false"
    triage.write_text(json.dumps({"gates": [{"name": "dup"}], "user_visible": False}))
    assert run(["python3", flag, str(triage), "gates"]).stdout == "true"
    assert run(["python3", flag, str(triage), "user_visible"]).stdout == "false"

    (tmp_path / "git").mkdir()
    repo, base = repository(tmp_path / "git")
    checker = str(WORKFLOW.resolve() / "check-chore-subjects.py")
    listing = tmp_path / "subjects.json"
    git(repo, "commit", "-q", "--allow-empty", "-m", "[implement-task] chore: tidy")
    assert run(["python3", checker, base, str(listing)], cwd=repo).returncode == 0
    assert json.loads(listing.read_text()) == []
    git(repo, "commit", "-q", "--allow-empty", "-m", "[fix-pr] fix: ci")
    assert run(["python3", checker, base, str(listing)], cwd=repo).returncode == 3
    assert json.loads(listing.read_text()) == ["[fix-pr] fix: ci"]
    # An operational failure (here an unknown base) is not "non-chore subjects found".
    broken = run(["python3", checker, "no-such-revision", str(listing)], cwd=repo)
    assert broken.returncode not in (0, 3)


def test_nonchore_ci_repair_commit_is_recorded_without_changing_the_outcome(
    tmp_path: Path,
) -> None:
    """Acceptance F3: a `fix:` CI-repair commit after finalize keeps a good PR good."""
    (tmp_path / "clone").mkdir()
    repo, base = repository(tmp_path / "clone")
    git(repo, "commit", "-q", "--allow-empty", "-m", "[implement-task] chore: tidy")
    prefinalize = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "-q", "--allow-empty", "-m", "[fix-pr] fix: repair CI lint")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "scope-prepush.json").write_text(
        json.dumps({"crossed": False, "reasons": [], "complete": True})
    )
    post = evidence / "scope-postfinalize.json"
    # The post-finalize guard's script steps, in workflow order.
    recorded = run(
        ["sh", str((WORKFLOW / "record-scope.sh").resolve())],
        cwd=repo,
        data={
            "floor": "[]",
            "decision": json.dumps({"crossed": False, "reasons": []}),
            "scope_path": str(post),
        },
    )
    assert recorded.stdout == "clean", recorded.stderr
    checker = str((WORKFLOW / "check-chore-subjects.py").resolve())
    run(["python3", checker, base, str(evidence / "nonchore-commits.json")], cwd=repo)
    scope_state = str((WORKFLOW / "scope-state.py").resolve())
    run(["python3", scope_state, "complete", str(post)])
    assert json.loads((evidence / "nonchore-commits.json").read_text()) == [
        "[fix-pr] fix: repair CI lint"
    ]
    output = evidence / "task-outcome.json"
    result = run(
        ["sh", str((WORKFLOW / "record-outcome.sh").resolve())],
        cwd=repo,
        data={
            "contract": "factory-task/1",
            "outcome_path": str(output),
            "validator_status": "passed",
            "ci_status": "passed",
            "branch_name": "factory/task-1-x",
            "pr_details": json.dumps({"url": "https://github.com/example/tidy/pull/7"}),
            "scope_path": str(evidence / "scope-prepush.json"),
            "post_scope_path": str(post),
            "prefinalize_head": prefinalize,
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["outcome"] == "pull-request"


def test_task_decline_reasons_carry_the_route_from_the_plan(tmp_path: Path) -> None:
    """Acceptance F4: the route and decision reach the outcome reasons the comment shows."""
    script = WORKFLOW / "record-triage.sh"
    decision = {
        "doable": False,
        "reasons": ["changes normalize_whitespace's return values"],
        "plan": "This belongs in a Feature, not a Task: decide the new casing rule.",
        "choices": [],
        "gates": [],
        "user_visible": False,
    }
    output = tmp_path / "task-outcome.json"
    result = run(
        ["sh", str(script)],
        data={
            "decision": json.dumps(decision),
            "outcome_path": str(output),
            "contract": "factory-task/1",
            "accept_field": "doable",
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())["reasons"] == [
        "changes normalize_whitespace's return values",
        "This belongs in a Feature, not a Task: decide the new casing rule.",
    ]
    fix = tmp_path / "fix-outcome.json"
    run(
        ["sh", str(script)],
        data={
            "decision": json.dumps({"fixable": False, "reasons": ["r"], "plan": "p"}),
            "outcome_path": str(fix),
        },
    )
    assert json.loads(fix.read_text())["reasons"] == ["r"]


def test_review_field_reads_review_json_strictly(tmp_path: Path) -> None:
    script = str((WORKFLOW / "review-field.sh").resolve())
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"kind": "task", "head_sha": "abc123"}))
    is_task = run(
        ["sh", script], data={"review_file": str(review), "field": "kind", "equals": "task"}
    )
    assert is_task.stdout == "true", is_task.stderr
    head = run(["sh", script], data={"review_file": str(review), "field": "head_sha"})
    assert head.stdout == "abc123"
    review.write_text(json.dumps({"kind": "fix", "head_sha": "abc123"}))
    assert (
        run(
            ["sh", script], data={"review_file": str(review), "field": "kind", "equals": "task"}
        ).stdout
        == "false"
    )
    # A record without its kind stops the round instead of reading as a non-task round.
    review.write_text(json.dumps({"head_sha": "abc123"}))
    missing = run(
        ["sh", script], data={"review_file": str(review), "field": "kind", "equals": "task"}
    )
    assert missing.returncode != 0
    assert "no kind" in missing.stderr
