"""INT-006: pushed feature checkpoints determine the next attempt."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from agent_factory.config import LocalConfig, SharedConfig
from agent_factory.controller import ExecutionPlan
from agent_factory.github import BranchInfo, IssueComment, PullRequestInfo, ReviewActivity
from agent_factory.operations import status
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.pull_request import handler, launch
from agent_factory.work_kinds.pull_request.blocked import process_blocked_claim
from agent_factory.work_kinds.pull_request.kinds import FEATURE
from agent_factory.work_kinds.pull_request.review import process_review_claim
from agent_factory.work_kinds.pull_request.workspace import PullRequestWorkspace
from tests.integration.test_fix_gestures import (
    _LOCAL_BASE,
    _SHARED_BASE,
    FakeGitHub,
    FakeReviewGitHub,
    _card,
    _ReviewPreparedHandler,
)


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def test_int006_newest_pushed_checkpoint_controls_recovery(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    work = tmp_path / "work"
    _git("clone", str(remote), str(work))
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.com", cwd=work)
    _git("checkout", "-b", "factory/feature-12-abcd1234", cwd=work)
    (work / "plan").write_text("planned")
    _git("add", "plan", cwd=work)
    _git("commit", "-m", "plan", "-m", "Factory-Checkpoint: planned", cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    mirror = tmp_path / "storage" / "mirrors" / "example__work.git"
    mirror.parent.mkdir(parents=True)
    _git("clone", "--mirror", str(remote), str(mirror))
    workspace = PullRequestWorkspace(tmp_path / "storage", work, work)
    assert (
        workspace.feature_checkpoint("example/work", "factory/feature-12-abcd1234", "read-token")
        == "planned"
    )
    (work / "plan").write_text("archived")
    _git("add", "plan", cwd=work)
    _git("commit", "-m", "archive", "-m", "Factory-Checkpoint: archived", cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    assert workspace.feature_checkpoint("example/work", "factory/feature-12-abcd1234") == "archived"


def test_int006_checkpoint_does_not_inherit_merged_target_marker(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    work = tmp_path / "work"
    _git("clone", str(remote), str(work))
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.com", cwd=work)
    (work / "old-feature").write_text("already merged")
    _git("add", "old-feature", cwd=work)
    _git("commit", "-m", "prior feature", "-m", "Factory-Checkpoint: archived", cwd=work)
    base_sha = _git("rev-parse", "HEAD", cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    _git("checkout", "-b", "factory/feature-12-new", cwd=work)
    (work / "current-feature").write_text("no checkpoint yet")
    _git("add", "current-feature", cwd=work)
    _git("commit", "-m", "draft", cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    mirror = tmp_path / "storage" / "mirrors" / "example__work.git"
    mirror.parent.mkdir(parents=True)
    _git("clone", "--mirror", str(remote), str(mirror))
    workspace = PullRequestWorkspace(tmp_path / "storage", work, work)
    assert (
        workspace.feature_checkpoint("example/work", "factory/feature-12-new", base_sha=base_sha)
        is None
    )
    (work / "plan").write_text("current plan")
    _git("add", "plan", cwd=work)
    _git("commit", "-m", "current plan", "-m", "Factory-Checkpoint: planned", cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    assert (
        workspace.feature_checkpoint("example/work", "factory/feature-12-new", base_sha=base_sha)
        == "planned"
    )


def test_int006_continued_claim_does_not_inherit_prior_claim_marker(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    work = tmp_path / "work"
    _git("clone", str(remote), str(work))
    _git("config", "user.name", "Test", cwd=work)
    _git("config", "user.email", "test@example.com", cwd=work)
    (work / "base").write_text("base")
    _git("add", "base", cwd=work)
    _git("commit", "-m", "base", cwd=work)
    target_branch = _git("branch", "--show-current", cwd=work)
    _git("checkout", "-b", "factory/feature-12-prior", cwd=work)
    (work / "prior").write_text("prior")
    _git("add", "prior", cwd=work)
    _git("commit", "-m", "prior", "-m", "Factory-Checkpoint: archived", cwd=work)
    prior_head = _git("rev-parse", "HEAD", cwd=work)
    _git("checkout", target_branch, cwd=work)
    (work / "target").write_text("target")
    _git("add", "target", cwd=work)
    _git("commit", "-m", "target", cwd=work)
    target_head = _git("rev-parse", "HEAD", cwd=work)
    _git("checkout", "-b", "factory/feature-12-current", prior_head, cwd=work)
    _git("merge", "--no-edit", target_head, cwd=work)
    _git("push", "origin", "HEAD", cwd=work)
    mirror = tmp_path / "storage" / "mirrors" / "example__work.git"
    mirror.parent.mkdir(parents=True)
    _git("clone", "--mirror", str(remote), str(mirror))
    workspace = PullRequestWorkspace(tmp_path / "storage", work, work)
    assert (
        workspace.feature_checkpoint(
            "example/work",
            "factory/feature-12-current",
            base_sha=target_head,
            exclude_sha=prior_head,
        )
        is None
    )


def test_int006_host_attempt_passes_resume_and_prior_branch() -> None:
    command = launch.host_script(
        runner="/bin/agent-runner",
        repo_clone=Path("/repo"),
        evidence=Path("/evidence"),
        credential_copy=Path("/private/feature.env"),
        gitconfig=Path("/private/gitconfig"),
        askpass=Path("/private/askpass.sh"),
        branch="factory/feature-12-new",
        contract="factory-feature/1",
        definition=FEATURE,
        resume_from="implement",
        prior_branch="factory/feature-12-old",
    )
    assert "--param resume_from=implement" in command
    assert "--param prior_branch=factory/feature-12-old" in command


def test_int006_resume_point_follows_stop_checkpoint_or_continuation() -> None:
    feature_resume_point = handler.feature_resume_point
    assert feature_resume_point("needs-input", "design", None, False, False) == "design"
    assert feature_resume_point("needs-input", "preflight", None, False, False) == ""
    assert feature_resume_point(None, None, "planned", False, False) == "implement"
    assert feature_resume_point(None, None, "implemented", False, False) == "archive"
    assert feature_resume_point(None, None, "archived", False, False) == "verify"
    assert feature_resume_point(None, None, None, True, False) == "verify"
    assert feature_resume_point(None, None, "archived", False, True) == "implement"
    assert feature_resume_point(None, None, None, False, False) == ""


def test_int006_definition_stop_reports_questions_direction_and_branch(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft("example/work", 12, "I12", "P12", "feature", "feature:example/work#12", {})
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(claim.id, "feature", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "needs-input",
            "stopped_step": "design",
            "questions": ["Which API?"],
            "direction_summary": "Use the existing client",
            "branch": "factory/feature-12-abcd",
            "sandbox": "host",
        },
    )
    feature.settle(claim, store.runs_for_claim(claim.id))
    events = store.pending_events(claim.id)
    assert any(
        "Which API?" in event.body
        and "Use the existing client" in event.body
        and "https://github.com/example/work/tree/factory/feature-12-abcd" in event.body
        for event in events
    )
    observed = status(store)
    assert "feature slot: free" in observed
    assert "blocked: example/work#12" in observed
    assert "factory/feature-12-abcd" in observed


def test_int006_preflight_stop_reports_fresh_next_attempt(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(claim.id, "feature", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "needs-input",
            "stopped_step": "preflight",
            "questions": ["Initialize OpenSpec"],
            "direction_summary": "Definition has not started",
        },
    )
    feature.settle(claim, store.runs_for_claim(claim.id))
    bodies = [event.body for event in store.pending_events(claim.id)]
    assert any(
        "No branch was created" in body and "next attempt starts fresh" in body for body in bodies
    )


def test_int006_open_pr_refuses_fresh_claim_and_explains_once(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(
        claim.id,
        "settled",
        {
            "verdict": "pending-human-review",
            "pr": {"number": 7, "url": "https://github.com/example/work/pull/7"},
        },
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)

    class GitHub:
        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return [PullRequestInfo("https://github.com/example/work/pull/7", 7, "a" * 40)]

    feature.attach_github(GitHub())  # type: ignore[arg-type]
    current = store.get_claim(claim.id)
    assert current is not None
    assert feature.gesture(current, _card("Ready"), []) is None
    assert feature.gesture(current, _card("Ready"), []) is None
    assert (
        len([event for event in store.pending_events(claim.id) if event.key == "open-pr-retry"])
        == 1
    )


def test_int006_closed_handed_off_feature_can_start_another_claim(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )

    class GitHub:
        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    feature.attach_github(GitHub())  # type: ignore[arg-type]
    current = store.get_claim(claim.id)
    assert current is not None
    assert feature.gesture(current, _card("Ready"), []) == "fresh"


def test_int006_feature_review_completion_preserves_acceptance_context(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(
        claim.id, "active", {"pr": {"number": 7, "url": "https://github.com/example/work/pull/7"}}
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(claim.id, "feature", reason="review", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "answered": ["thread-1"],
            "changed": ["review-2"],
            "sandbox": "host",
        },
    )
    current = store.get_claim(claim.id)
    assert current is not None
    outcome = feature.settle(current, store.runs_for_claim(claim.id))
    assert outcome is not None
    assert "https://github.com/example/work/pull/7" in outcome.event_body
    assert "thread-1" in outcome.event_body
    assert "review-2" in outcome.event_body
    assert "acceptance was not re-run" in outcome.event_body


def test_int006_failed_feature_review_links_the_open_pr(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(
        claim.id,
        "active",
        {
            "pr": {
                "number": 7,
                "url": "https://github.com/example/work/pull/7",
            }
        },
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(claim.id, "feature", reason="review", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "failed",
            "reasons": ["CI still red"],
            "branch": "factory/feature-12-abcd",
        },
    )
    current = store.get_claim(claim.id)
    assert current is not None
    outcome = feature.settle(current, store.runs_for_claim(claim.id))
    assert outcome is not None
    assert "https://github.com/example/work/pull/7" in outcome.event_body


def test_int006_archive_recovery_copies_report_and_announces_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            12,
            "I12",
            "P12",
            "feature",
            "fp",
            {"revisions": {"target": "a" * 40, "runner": "b" * 40, "skills": "c" * 40}},
        )
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(
        claim.id, "feature", reason="recovery", evidence_path=str(tmp_path / "evidence")
    )
    prior = tmp_path / "prior" / "agent-runner-session" / "output"
    prior.mkdir(parents=True)
    (prior / "task-session-report.out").write_text("prior findings")

    def credential(*_args: object) -> Path:
        return tmp_path / "token"

    def host_plan(**_kwargs: object) -> ExecutionPlan:
        return ExecutionPlan(("/bin/true",), str(tmp_path), {}, (), (), {}, False)

    monkeypatch.setattr(launch, "validated_credential_copy", credential)
    monkeypatch.setattr(launch, "build_host_plan", host_plan)
    feature.plan(
        claim,
        run,
        Preparation(
            payload={
                "clones": {"repo": str(tmp_path), "runner": str(tmp_path), "skills": str(tmp_path)},
                "issue": {},
                "resume_from": "verify",
                "prior_branch": "",
                "prior_report": str(tmp_path / "prior"),
            }
        ),
    )
    copied = (
        tmp_path
        / "evidence"
        / "attempt-1"
        / "agent-runner-session"
        / "output"
        / "task-session-report.out"
    )
    assert copied.read_text() == "prior findings"
    assert any("resumes at verify" in event.body for event in store.pending_events(claim.id))


def test_int006_missing_prior_branch_reports_fresh_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            12,
            "I12",
            "P12",
            "feature",
            "fp",
            {
                "revisions": {"target": "a" * 40, "runner": "b" * 40, "skills": "c" * 40},
            },
        )
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    run = store.reserve_run(
        claim.id, "feature", reason="initial", evidence_path=str(tmp_path / "evidence")
    )

    def credential(*_args: object) -> Path:
        return tmp_path / "token"

    def host_plan(**_kwargs: object) -> ExecutionPlan:
        return ExecutionPlan(("/bin/true",), str(tmp_path), {}, (), (), {}, False)

    monkeypatch.setattr(launch, "validated_credential_copy", credential)
    monkeypatch.setattr(launch, "build_host_plan", host_plan)
    feature.plan(
        claim,
        run,
        Preparation(
            payload={
                "clones": {"repo": str(tmp_path), "runner": str(tmp_path), "skills": str(tmp_path)},
                "issue": {},
                "resume_from": "",
                "prior_branch": "",
                "resume_fallback": "prior branch unavailable",
            }
        ),
    )
    evidence = tmp_path / "evidence" / "attempt-1"
    assert (
        json.loads((evidence / "resume.json").read_text())["fallback"] == "prior branch unavailable"
    )
    assert any(
        "prior branch unavailable" in event.body and "starts fresh" in event.body
        for event in store.pending_events(claim.id)
    )
    assert "prior branch unavailable" in feature.attempt_message(
        run,
        {"outcome": "failed", "resume": {"fallback": "prior branch unavailable"}},
        stage="complete",
    )


def test_int006_review_needs_input_waits_for_pr_feedback(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(claim.id, "blocked", {"blocked_by": "review"})
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    current = store.get_claim(claim.id)
    assert current is not None
    comment = IssueComment("1", "answer", "writer", "2099-01-01T00:00:00Z")
    assert feature.gesture(current, _card("Ready"), [comment]) is None


def test_int006_blocked_reconciliation_failure_is_visible_and_retried(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {}))
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01T00:00:00Z"})

    class Handler(handler.PullRequestHandler):
        broken = True

        def prepare(self, claim: object) -> Preparation:
            if self.broken:
                raise ReadinessError("ambiguous feature pull requests")
            return Preparation()

    feature = Handler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    client = FakeGitHub([], {})
    current = store.get_claim(claim.id)
    assert current is not None
    arguments = (
        store,
        client,
        feature,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        LocalConfig.from_toml(_LOCAL_BASE),
        _card("Ready"),
        current,
    )
    options = {
        "bot_login": "example-factory[bot]",
        "artifact_root": tmp_path,
        "now": datetime(2026, 1, 2, tzinfo=UTC),
    }
    assert process_blocked_claim(*arguments, **options) is None  # type: ignore[arg-type]
    assert "readiness: ambiguous feature pull requests" in status(store)
    feature.broken = False
    assert process_blocked_claim(*arguments, **options) is not None  # type: ignore[arg-type]
    assert "readiness: ambiguous feature pull requests" not in status(store)


@pytest.mark.parametrize(
    ("branch_exists", "result", "expected"),
    [
        (True, {"reason": "runner exited"}, "verify"),
        (False, {"outcome": "needs-input", "stopped_step": "design"}, "design"),
    ],
)
def test_int006_prepare_uses_own_branch_or_lets_workflow_record_missing_branch(
    tmp_path: Path, branch_exists: bool, result: dict[str, object], expected: str
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            12,
            "I12",
            "P12",
            "feature",
            "fp",
            {
                "target": {"repository": "example/work"},
                "revisions": {"target": "a" * 40, "runner": "b" * 40, "skills": "c" * 40},
                "roles": {},
            },
        )
    )
    run = store.reserve_run(claim.id, "feature", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(run.id, execution_status="failed", result=result)

    class Workspace(PullRequestWorkspace):
        def feature_checkpoint(
            self,
            repository: str,
            branch: str,
            token: str | None = None,
            *,
            base_sha: str | None = None,
            exclude_sha: str | None = None,
        ) -> str | None:
            return "archived"

        def prepare_clones(
            self,
            claim_id: str,
            attempt: int,
            repository: str,
            revisions: Mapping[str, object],
        ) -> dict[str, str]:
            return {name: str(tmp_path) for name in ("repo", "runner", "skills")}

    class GitHub:
        def get_branch(self, repository: str, branch: str) -> BranchInfo | None:
            return BranchInfo(branch, "d" * 40) if branch_exists else None

        def list_open_pull_requests_for_head(
            self, repository: str, branch: str
        ) -> list[PullRequestInfo]:
            return []

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    local = LocalConfig.from_toml(_LOCAL_BASE)
    local = replace(
        local, credentials=replace(local.credentials, fix_environment=tmp_path / "credential.env")
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        local,
        workspace=Workspace(tmp_path, tmp_path, tmp_path),
    )
    feature.attach_store(store)
    feature.attach_github(GitHub())  # type: ignore[arg-type]
    feature._issue_input = lambda _claim: {}  # type: ignore[method-assign]
    prepared = feature.prepare(claim)
    assert prepared.payload["resume_from"] == expected
    assert prepared.payload["prior_branch"] == ""
    assert prepared.payload["prior_report"] == (
        str(tmp_path / "attempt-1") if branch_exists else ""
    )


@pytest.mark.parametrize("prior_available", [True, False])
def test_int006_failed_claim_continues_prior_planned_branch(
    tmp_path: Path, prior_available: bool
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    draft = ClaimDraft(
        "example/work",
        12,
        "I12",
        "P12",
        "feature",
        "fp",
        {
            "target": {"repository": "example/work"},
            "revisions": {"target": "a" * 40, "runner": "b" * 40, "skills": "c" * 40},
            "roles": {},
        },
    )
    previous = store.create_claim(draft)
    run = store.reserve_run(previous.id, "feature", reason="initial", evidence_path=str(tmp_path))
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "failed",
            "reasons": ["CI stayed red"],
            "branch": "factory/feature-12-old",
        },
    )
    store.set_claim_lifecycle(previous.id, "settled", {"verdict": "failed"})
    current = store.supersede_and_create(previous.id, draft)

    class Workspace(PullRequestWorkspace):
        def feature_checkpoint(
            self,
            repository: str,
            branch: str,
            token: str | None = None,
            *,
            base_sha: str | None = None,
            exclude_sha: str | None = None,
        ) -> str | None:
            return "planned"

        def prepare_clones(
            self,
            claim_id: str,
            attempt: int,
            repository: str,
            revisions: Mapping[str, object],
        ) -> dict[str, str]:
            return {name: str(tmp_path) for name in ("repo", "runner", "skills")}

    prior_branch = f"factory/feature-12-{previous.id[:8]}"

    class GitHub:
        def get_branch(self, repository: str, branch: str) -> BranchInfo | None:
            return (
                BranchInfo(branch, "d" * 40) if branch == prior_branch and prior_available else None
            )

        def list_open_pull_requests_for_head(
            self, repository: str, branch: str
        ) -> list[PullRequestInfo]:
            return []

        def list_open_factory_pull_requests_for_issue(
            self, repository: str, number: int
        ) -> list[PullRequestInfo]:
            return []

    local = LocalConfig.from_toml(_LOCAL_BASE)
    local = replace(
        local, credentials=replace(local.credentials, fix_environment=tmp_path / "credential.env")
    )
    feature = handler.PullRequestHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + "\n[feature]\n"),
        local,
        workspace=Workspace(tmp_path, tmp_path, tmp_path),
    )
    feature.attach_store(store)
    feature.attach_github(GitHub())  # type: ignore[arg-type]
    feature._issue_input = lambda _claim: {}  # type: ignore[method-assign]
    prepared = feature.prepare(current)
    assert prepared.payload["resume_from"] == ("implement" if prior_available else "")
    assert prepared.payload["prior_branch"] == (prior_branch if prior_available else "")
    if not prior_available:
        assert "prior branch unavailable" in str(prepared.payload["resume_fallback"])


@pytest.mark.parametrize("enabled", [True, False])
def test_int006_feature_review_admission_names_pr_and_feedback(
    tmp_path: Path, enabled: bool
) -> None:
    from datetime import UTC, datetime

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 64, "I64", "P64", "feature", "fp", {}))
    store.set_claim_lifecycle(
        claim.id,
        "settled",
        {
            "verdict": "pending-human-review",
            "pr": {
                "number": 7,
                "url": "https://github.com/example/work/pull/7",
                "branch": "factory/feature-64-abcd",
                "head_sha": "a" * 40,
            },
            "review_checkpoint": "2026-01-01T00:00:00Z",
        },
    )
    store.set_preparation(claim.id, {"issue": {"title": "Feature", "body": "Feature body"}})
    feature = _ReviewPreparedHandler(
        FEATURE,
        SharedConfig.from_toml(_SHARED_BASE + ("\n[feature]\n" if enabled else "")),
        LocalConfig.from_toml(_LOCAL_BASE),
    )
    feature.attach_store(store)
    activity = ReviewActivity(
        reviews=(),
        threads=(),
        comments=(IssueComment("comment-9", "please update", "writer", "2099-01-01T00:00:00Z"),),
    )
    client = FakeReviewGitHub(activity, {"writer": "write"})
    current = store.get_claim(claim.id)
    assert current is not None
    admitted = process_review_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        feature,
        current,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=datetime(2099, 1, 2, tzinfo=UTC),
        local=LocalConfig.from_toml(_LOCAL_BASE),
        readiness=lambda: True,
    )
    assert admitted is not None
    assert admitted[0].kind == "feature"
    assert admitted[1].payload["review"]["kind"] == "feature"  # type: ignore[index]
    assert any(
        "https://github.com/example/work/pull/7" in event.body and "comment-9" in event.body
        for event in store.pending_events(claim.id)
    )
