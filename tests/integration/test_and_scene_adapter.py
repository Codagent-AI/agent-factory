from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from agent_factory.controller import AttemptResult, Controller, RequestSnapshot
from agent_factory.github import IssueComment
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import SourceRepositories
from agent_factory.work_kinds.eval import EvalDefaults


def _git(path: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *arguments], text=True).strip()


def _repository(tmp_path: Path, name: str, files: dict[str, str]) -> tuple[Path, str]:
    source = tmp_path / name
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "tests@example.invalid")
    _git(source, "config", "user.name", "Tests")
    for relative, contents in files.items():
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def _sources(tmp_path: Path) -> tuple[SourceRepositories, dict[str, str]]:
    runner, runner_sha = _repository(
        tmp_path,
        "runner",
        {
            "scripts/sandbox-run.sh": "#!/bin/sh\n# --docker-run-arg\n",
            "workflows/core/implement-change-v1.0.yaml": "ok\n",
        },
    )
    skills, skills_sha = _repository(tmp_path, "skills", {"SKILL.md": "ok\n"})
    evals, evals_sha = _repository(
        tmp_path,
        "evals",
        {
            "evals/agent-runner/and-scene/run.sh": "#!/bin/sh\n",
            "evals/agent-runner/and-scene/human-review.sh": "#!/bin/sh\n",
            "evals/agent-runner/and-scene/lib/phases.mjs": "export {}\n",
            "evals/agent-runner/and-scene/lib/outcomes.mjs": "export {}\n",
            "evals/agent-runner/and-scene/lib/result.mjs": "export {}\n",
        },
    )
    for script in (
        runner / "scripts/sandbox-run.sh",
        evals / "evals/agent-runner/and-scene/run.sh",
        evals / "evals/agent-runner/and-scene/human-review.sh",
    ):
        script.chmod(script.stat().st_mode | 0o111)
    return SourceRepositories(runner, skills, evals), {
        "runner": runner_sha,
        "skills": skills_sha,
        "evals": evals_sha,
    }


def test_prepare_claim_keeps_distinct_detached_pins_and_removes_only_recorded_worktrees(
    tmp_path: Path,
) -> None:
    from agent_factory.suites.and_scene import GitWorktreeManager

    sources, revisions = _sources(tmp_path)
    manager = GitWorktreeManager(tmp_path / "factory", sources)

    first = manager.prepare("claim/a", revisions)
    second = manager.prepare("claim-b", revisions)

    assert first.runner != second.runner
    assert all(path.is_dir() for path in first.paths())
    assert _git(first.runner, "rev-parse", "HEAD") == revisions["runner"]
    assert _git(first.runner, "status", "--porcelain") == ""
    assert _git(sources.runner, "rev-parse", "HEAD") == revisions["runner"]

    manager.remove(first)

    assert not any(path.exists() for path in first.paths())
    assert all(path.exists() for path in second.paths())
    assert all(
        str(path) not in _git(source, "worktree", "list", "--porcelain")
        for source, path in (
            (sources.runner, first.runner),
            (sources.skills, first.skills),
            (sources.evals, first.evals),
        )
    )


def test_adapter_builds_safe_accepted_argv_and_only_resumes_valid_checkpoints(
    tmp_path: Path,
) -> None:
    from agent_factory.suites.and_scene import (
        AndSceneAdapter,
        GitWorktreeManager,
        RecoveryStateError,
    )

    sources, revisions = _sources(tmp_path)
    worktrees = GitWorktreeManager(tmp_path / "factory", sources).prepare("claim-1", revisions)
    environment = tmp_path / "candidate credentials.env"
    environment.write_text("CANDIDATE_TOKEN=token\n", encoding="utf-8")
    artifact = tmp_path / "artifact ; not shell"
    frozen = {
        "suite": "and-scene",
        "settings": {
            "roles": {
                "lead": "codex:lead-model:high",
                "implementor": "claude:implementation-model:medium",
                "tester": "codex:tester-model:low",
            },
            "skip_validator": True,
            "repetitions": 2,
        },
        "revisions": revisions,
    }
    adapter = AndSceneAdapter(environment_file=environment)

    plan = adapter.plan(frozen, worktrees, artifact, recovery=False)

    assert plan.resume is False
    assert plan.argv[0] == str(worktrees.evals / "evals/agent-runner/and-scene/run.sh")
    assert "--calibration-record" not in plan.argv
    assert "--skip-validator" in plan.argv
    assert "--tester-cli" in plan.argv
    assert "--reviewer-cli" not in plan.argv
    assert str(artifact) in plan.argv
    # The sandbox reads --env-file at launch; credential values must never enter
    # the serialized execution plan or supervisor environment.
    assert plan.allowed_environment == {}
    assert plan.credential_files == (str(environment),)
    assert plan.argv[plan.argv.index("--env-file") + 1] == str(environment)
    assert all(";" not in value or value == str(artifact) for value in plan.argv)
    assert (
        f"glob:{artifact}/.runtime/agent-session-state/cursor/chats/*/*/store.db*"
        in plan.progress_sources
    )

    artifact.mkdir()
    (artifact / "run-state.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    assert adapter.plan(frozen, worktrees, artifact, recovery=True).resume is True

    (artifact / "run-state.json").write_text("not json", encoding="utf-8")
    with pytest.raises(RecoveryStateError, match="corrupt"):
        adapter.plan(frozen, worktrees, artifact, recovery=True)

    (artifact / "run-state.json").unlink()
    (artifact / "candidate.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RecoveryStateError, match="missing"):
        adapter.plan(frozen, worktrees, artifact, recovery=True)


def test_adapter_reads_suite_outcomes_and_renders_only_reviewable_handoffs(tmp_path: Path) -> None:
    from agent_factory.suites.and_scene import AndSceneAdapter

    artifact = tmp_path / "run with spaces"
    artifact.mkdir()
    review_script = tmp_path / "human review.sh"
    review_script.write_text("#!/bin/sh\n", encoding="utf-8")
    review_script.chmod(0o755)
    adapter = AndSceneAdapter(environment_file=tmp_path / "env", mac_name="Factory Mac")

    (artifact / "result.json").write_text(
        json.dumps(
            {
                "evaluation_status": "pending-human-review",
                "product_verdict": "unavailable",
                "automated_subtotal": 40,
                "delivery": {
                    "candidate_branch": "eval/one",
                    "pull_request": "https://example/pr/1",
                },
            }
        ),
        encoding="utf-8",
    )
    outcome = adapter.read_result(artifact)
    assert outcome.execution_status == "completed"
    assert outcome.product_verdict == "unavailable"
    assert outcome.result["score"] == 40
    handoff = adapter.review_handoff(outcome.result, review_script, artifact)
    assert handoff is not None
    assert str(review_script.resolve()) in handoff
    assert "'" in handoff
    assert "Factory Mac" in handoff

    (artifact / "result.json").write_text(
        json.dumps(
            {
                "evaluation_status": "complete",
                "product_verdict": "fail",
                "automated_subtotal": 39,
                "product_failure": {"reason": "suite supplied failure"},
            }
        ),
        encoding="utf-8",
    )
    failed = adapter.read_result(artifact)
    assert failed.product_verdict == "failed"
    assert adapter.review_handoff(failed.result, review_script, artifact) is None


def test_candidate_environment_preserves_literal_runner_values(
    tmp_path: Path,
) -> None:
    from agent_factory.suites.and_scene import candidate_environment

    environment = tmp_path / "candidate.env"
    environment.write_text(
        "CANDIDATE_TOKEN=\"abc 123\"\nSECOND='value with spaces'\n",
        encoding="utf-8",
    )

    assert candidate_environment(environment) == {
        "CANDIDATE_TOKEN": '"abc 123"',
        "SECOND": "'value with spaces'",
    }

    environment.write_text('CANDIDATE_TOKEN="unterminated\n', encoding="utf-8")
    assert candidate_environment(environment) == {"CANDIDATE_TOKEN": '"unterminated'}


def test_controller_understands_real_nonresumable_workflow_owner(tmp_path: Path) -> None:
    class Comments:
        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return []

        def create_comment(self, repository: str, number: int, body: str) -> str:
            return "1"

    defaults = EvalDefaults(
        "main", "main", {"lead": "a:b:c", "implementor": "a:b:c", "tester": "a:b:c"}, False, 1
    )
    controller = Controller(
        ClaimStore(tmp_path / "state.sqlite3"), Comments(), defaults, harness_sha="e" * 40
    )
    snapshot = RequestSnapshot(
        "example/evals",
        1,
        "I",
        "P",
        "writer",
        "write",
        "Eval",
        frozenset(),
        "Ready",
        "factory",
        None,
        "```eval\nrepetitions = 1\n```",
        False,
    )
    claim = controller.accept(snapshot, resolve=lambda _: ("a" * 40, "b" * 40))
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    controller.record_result(
        run.id,
        AttemptResult(
            "failed",
            "unavailable",
            {
                "failure": {"owner": "implementation-workflow", "code": "side-effect"},
                "resumable": False,
            },
            resumable=False,
        ),
    )
    assert controller.presentation(claim.id).verdict == "failed"


def test_reviewed_done_cleanup_is_durable_and_does_not_touch_active_worktrees(
    tmp_path: Path,
) -> None:
    from agent_factory.suites.and_scene import GitWorktreeManager, WorktreeCleanup

    sources, revisions = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    reviewed = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "one", {}))
    active = store.create_claim(ClaimDraft("example/evals", 2, "I2", "P2", "eval", "two", {}))
    manager = GitWorktreeManager(tmp_path / "factory", sources)
    reviewed_trees = manager.prepare(reviewed.id, revisions)
    active_trees = manager.prepare(active.id, revisions)
    cleanup = WorktreeCleanup(store, manager)
    cleanup.record(reviewed.id, reviewed_trees)
    cleanup.record(active.id, active_trees)

    store.set_claim_lifecycle(reviewed.id, "settled", {"verdict": "pending-human-review"})
    assert cleanup.reconcile(reviewed.id, board_status="Review") is False
    assert all(path.exists() for path in reviewed_trees.paths())
    assert cleanup.reconcile(active.id, board_status="Done") is False
    assert all(path.exists() for path in active_trees.paths())
    assert cleanup.reconcile(reviewed.id, board_status="Done") is True
    assert not any(path.exists() for path in reviewed_trees.paths())
    saved = store.get_claim(reviewed.id)
    assert saved is not None and saved.cleanup["complete"] is True
    assert all(path.exists() for path in active_trees.paths())


def test_cleanup_records_malformed_persisted_worktree_state_for_retry(tmp_path: Path) -> None:
    from agent_factory.suites.and_scene import GitWorktreeManager, WorktreeCleanup

    sources, _ = _sources(tmp_path)
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/evals", 1, "I1", "P1", "eval", "one", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    store.set_cleanup(claim.id, {"review_observed": True, "complete": False})
    cleanup = WorktreeCleanup(store, GitWorktreeManager(tmp_path / "factory", sources))

    assert cleanup.reconcile(claim.id, board_status="Done") is False
    saved = store.get_claim(claim.id)
    assert saved is not None
    assert saved.cleanup["complete"] is False
    last_error = cast(dict[str, object], saved.cleanup["last_error"])
    cleanup_error = last_error["cleanup"]
    assert isinstance(cleanup_error, str)
    assert "recorded worktrees" in cleanup_error


def test_store_migrates_existing_claim_rows_without_preparation_columns(tmp_path: Path) -> None:
    state = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(state)
    connection.executescript(
        """
        CREATE TABLE claim (
            id TEXT PRIMARY KEY, repository TEXT NOT NULL, issue_number INTEGER NOT NULL,
            issue_id TEXT NOT NULL, project_item_id TEXT NOT NULL, kind TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL, frozen_spec_json TEXT NOT NULL,
            lifecycle TEXT NOT NULL, outcome_json TEXT NOT NULL, reporting_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO claim VALUES (
            'claim', 'example/evals', 1, 'I1', 'P1', 'eval', 'fingerprint', '{}',
            'active', '{}', '{}', 'now', 'now'
        );
        PRAGMA user_version = 2;
        """
    )
    connection.close()

    store = ClaimStore(state)
    migrated = store.get_claim("claim")

    assert migrated is not None
    assert migrated.preparation == {}
    assert migrated.cleanup == {}


def test_controller_reserves_an_absolute_stable_artifact_path(tmp_path: Path) -> None:
    class Comments:
        def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
            return []

        def create_comment(self, repository: str, number: int, body: str) -> str:
            return "1"

    defaults = EvalDefaults(
        "main", "main", {"lead": "a:b:c", "implementor": "a:b:c", "tester": "a:b:c"}, False, 1
    )
    controller = Controller(
        ClaimStore(tmp_path / "state.sqlite3"),
        Comments(),
        defaults,
        harness_sha="e" * 40,
        artifact_root=tmp_path / "artifacts",
    )
    claim = controller.accept(
        RequestSnapshot(
            "example/evals",
            1,
            "I",
            "P",
            "writer",
            "write",
            "Eval",
            frozenset(),
            "Ready",
            "factory",
            None,
            "```eval\nrepetitions = 1\n```",
            False,
        ),
        resolve=lambda _: ("a" * 40, "b" * 40),
    )
    assert claim is not None
    run = controller.reserve_next(claim.id, readiness=lambda: None)
    assert run is not None
    assert Path(run.evidence_path) == (tmp_path / "artifacts" / f"{claim.id}-rep-1").resolve()


@pytest.mark.parametrize(
    "reset", ["nonsense", "2026-09-10T12:00:00", "2026-09-10T11:58:00Z", "2026-09-10T19:00:00Z"]
)
def test_unusable_claude_wait_does_not_suspend_timers(tmp_path: Path, reset: str) -> None:
    from agent_factory.suites.and_scene import bounded_quota_deadline

    (tmp_path / "factory-suite.log").write_text(
        f"Claude lead quota reached; waiting until {reset} before resuming Agent Runner\n"
    )
    assert (
        bounded_quota_deadline(tmp_path, now=datetime(2026, 9, 10, 12, tzinfo=UTC).timestamp())
        is None
    )


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_quota_log_must_be_a_readable_regular_file(tmp_path: Path, kind: str) -> None:
    from agent_factory.suites.and_scene import ReadinessError, bounded_quota_deadline

    log = tmp_path / "factory-suite.log"
    if kind == "directory":
        log.mkdir()
    else:
        target = tmp_path / "other"
        target.write_text("not suite output")
        log.symlink_to(target)
    with pytest.raises(ReadinessError, match="quota log"):
        bounded_quota_deadline(tmp_path, now=0)
