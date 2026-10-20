# pyright: reportPrivateUsage=false
"""Factory attempt cap persistence and notice boundaries."""

from __future__ import annotations

import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_feature_gestures as gestures

from agent_factory.config import ConfigurationError, JobCapConfig, SharedConfig
from agent_factory.controller import AttemptResult, Controller, RequestSnapshot
from agent_factory.github import GitHubApiError, IssueComment
from agent_factory.job_cap import hold_claim, notice_body, notify_card, observe, status_lines
from agent_factory.store import ClaimDraft, ClaimStore, JobCapReached
from agent_factory.suites.and_scene import ReadinessError
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.eval import EvalDefaults, EvalHandler
from agent_factory.work_kinds.eval.handler import Resolution
from agent_factory.work_kinds.pull_request.blocked import process_blocked_claim
from agent_factory.work_kinds.pull_request.review import process_review_claim


class Comments:
    def __init__(self) -> None:
        self.records: list[IssueComment] = []

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return list(self.records)

    def create_comment(self, repository: str, number: int, body: str) -> str:
        identifier = str(len(self.records) + 1)
        self.records.append(IssueComment(identifier, body, "codagent-factory[bot]"))
        return identifier


def test_config_defaults_validation_and_leftover_watch_key() -> None:
    text = Path("config/codagent.toml").read_text()
    assert (
        SharedConfig.from_toml(
            text.replace("[job_cap]\nattempts = 100\nwindow_hours = 24", "")
        ).job_cap
        == JobCapConfig()
    )
    assert SharedConfig.from_toml(
        text.replace("[watch]", "[watch]\ndaily_sessions = 0")
    ).watch.enabled
    assert (
        SharedConfig.from_toml(text.replace("attempts = 100", "attempts = 3")).job_cap.attempts == 3
    )
    for key, bad in (("attempts", "0"), ("window_hours", "true")):
        with pytest.raises(ConfigurationError, match=f"job_cap.{key}"):
            SharedConfig.from_toml(
                text.replace(f"{key} = {100 if key == 'attempts' else 24}", f"{key} = {bad}")
            )


def test_count_reset_window_and_lowered_cap(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    with closing(ClaimStore(tmp_path / "state.sqlite3", job_cap=JobCapConfig(10, 24))) as store:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        for age in (25, 3, 2, 2, 1, 0):
            run = store.reserve_run(
                claim.id,
                str(age) + str(store._connection.execute("SELECT count(*) FROM run").fetchone()[0]),
                lane="low",
                reason="initial",
                evidence_path="/tmp/e",
            )
            store.finish_run(run.id, execution_status="failed", result={})
            store._connection.execute(
                "UPDATE run SET created_at=? WHERE id=?",
                ((now - timedelta(hours=age)).isoformat(), run.id),
            )
        state = store.job_cap_state(now, JobCapConfig(3, 24))
        assert state.count == 5
        assert state.clears_at == now + timedelta(hours=22)
        lowered = store.job_cap_state(now, JobCapConfig(2, 24))
        assert lowered.clears_at == now + timedelta(hours=23)
        store.set_setting("job-cap", "reset", {"at": now.isoformat()})
        assert store.job_cap_state(now, JobCapConfig(3, 24)).count == 1
        assert not store.job_cap_state(now, JobCapConfig(3, 24)).reached


def test_future_dated_attempts_still_count(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    with closing(ClaimStore(tmp_path / "state.sqlite3", job_cap=JobCapConfig(1, 24))) as store:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/e"
        )
        store.finish_run(run.id, execution_status="failed", result={})
        # The host clock moved back an hour after this attempt was stamped.
        store._connection.execute(
            "UPDATE run SET created_at=? WHERE id=?",
            ((now + timedelta(hours=1)).isoformat(), run.id),
        )
        state = store.job_cap_state(now)
        assert state.count == 1
        assert state.reached
        assert state.clears_at == now + timedelta(hours=25)


def test_status_survives_an_unreadable_reset_time(tmp_path: Path) -> None:
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store:
        store.set_setting("job-cap", "reset", {"at": "not a time"})
        lines = status_lines(store, JobCapConfig(), datetime.now(UTC))
        assert len(lines) == 1
        assert lines[0].startswith("job cap: saved state unreadable")


def test_last_slot_is_atomic_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    cap = JobCapConfig(1, 24)
    with closing(ClaimStore(path, job_cap=cap)) as store:
        claims = [
            store.create_claim(ClaimDraft("o/r", i, f"I{i}", f"P{i}", kind, "fp", {}))
            for i, kind in ((1, "fix"), (2, "eval"))
        ]
    gate = tmp_path / "go"
    script = """
import pathlib, sys, time
from agent_factory.config import JobCapConfig
from agent_factory.store import ClaimStore, JobCapReached
path, claim_id, gate = sys.argv[1:]
store = ClaimStore(pathlib.Path(path), job_cap=JobCapConfig(1, 24))
while not pathlib.Path(gate).exists():
    time.sleep(.001)
try:
    store.reserve_run(claim_id, 'one', lane='low', reason='initial', evidence_path='/tmp/e')
except JobCapReached:
    print('held')
else:
    print('reserved')
finally:
    store.close()
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path), claim.id, str(gate)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for claim in claims
    ]
    gate.touch()
    results: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        results.append(stdout.strip())
    assert sorted(results) == ["held", "reserved"]
    with closing(ClaimStore(path)) as store:
        assert store.job_cap_state(datetime.now(UTC)).count == 1


def test_default_store_enforces_one_hundred_attempts(tmp_path: Path) -> None:
    with closing(ClaimStore(tmp_path / "state.sqlite3")) as store:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        for number in range(100):
            run = store.reserve_run(
                claim.id, f"unit-{number}", lane="low", reason="initial", evidence_path="/tmp/e"
            )
            store.finish_run(run.id, execution_status="failed", result={})
        with pytest.raises(JobCapReached):
            store.reserve_run(
                claim.id, "unit-100", lane="low", reason="initial", evidence_path="/tmp/e"
            )


def test_episode_claim_and_card_notices_are_once_per_episode(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    with closing(ClaimStore(tmp_path / "state.sqlite3", job_cap=JobCapConfig(1, 24))) as store:
        claim = store.create_claim(ClaimDraft("o/r", 1, "I", "P", "fix", "fp", {}))
        run = store.reserve_run(
            claim.id, "one", lane="low", reason="initial", evidence_path="/tmp/e"
        )
        store._connection.execute(
            "UPDATE run SET created_at=? WHERE id=?",
            ((now - timedelta(seconds=1)).isoformat(), run.id),
        )
        state = store.job_cap_state(now)
        assert state.reached
        episode = observe(store, state, now)
        assert isinstance(episode, str)
        hold_claim(store, claim.id, state, now)
        hold_claim(store, claim.id, state, now)
        assert store.get_hold(claim.id, "job-cap") == {"episode": episode}
        assert "1 of 1" in notice_body(state)

        class FailingOnce(Comments):
            failed = False

            def create_comment(self, repository: str, number: int, body: str) -> str:
                if not self.failed:
                    self.failed = True
                    raise GitHubApiError("temporary failure")
                return super().create_comment(repository, number, body)

        comments = FailingOnce()
        notify_card(store, comments, "codagent-factory[bot]", "o/r", 2, episode, state)
        assert "temporary failure" in str(store.get_setting("job-cap-card", "o/r:2"))
        notify_card(store, comments, "codagent-factory[bot]", "o/r", 2, episode, state)
        assert len(comments.records) == 1
        store.set_setting(
            "job-cap-card", "o/r:2", {"episode": episode, "repository": "o/r", "issue": 2}
        )
        notify_card(store, comments, "codagent-factory[bot]", "o/r", 2, episode, state)
        assert len(comments.records) == 1
        assert "job cap waiting: o/r#2" in status_lines(store, JobCapConfig(1, 24), now)
        store.set_setting("job-cap", "reset", {"at": (now + timedelta(seconds=1)).isoformat()})
        observe(store, store.job_cap_state(now + timedelta(seconds=1)), now + timedelta(seconds=1))
        assert store.get_settings_by_prefix("job-cap-card", "") == {}


def test_preflight_reuses_claim_and_cap_holds_next_eval_repetition(tmp_path: Path) -> None:
    defaults = EvalDefaults(
        agent_runner_ref="main",
        agent_skills_ref="main",
        roles={"lead": "codex:m:high", "implementor": "codex:m:high", "tester": "codex:m:high"},
        skip_validator=False,
        repetitions=2,
    )
    snapshot = RequestSnapshot(
        "example/evals",
        1,
        "I",
        "P",
        "writer",
        "write",
        "Eval",
        frozenset({"run-eval"}),
        "Ready",
        "factory",
        None,
        "```eval\nrepetitions = 2\n```",
        False,
    )
    with closing(ClaimStore(tmp_path / "state.sqlite3", job_cap=JobCapConfig(1, 24))) as store:
        comments = Comments()
        controller = Controller(
            store, comments, {"eval": EvalHandler(defaults, harness_ref="c" * 40)}
        )

        def resolve(_: object) -> Resolution:
            return Resolution({"runner": "a" * 40, "skills": "b" * 40, "evals": "c" * 40}, {})

        draft = controller.preflight(snapshot, resolve=resolve)
        assert draft is not None
        assert draft.frozen_spec["revisions"] == resolve(None).revisions
        assert store.claims_for_item("P") == []
        invalid = replace(snapshot, body="```eval\nrepetitions = 0\n```")
        assert controller.preflight(invalid, resolve=resolve) is None
        assert store.claims_for_item("P") == []

        def unresolvable(_: object) -> Resolution:
            raise ReadinessError("revision unavailable")

        with pytest.raises(ReadinessError, match="revision unavailable"):
            controller.preflight(snapshot, resolve=unresolvable)
        assert store.claims_for_item("P") == []
        handler = controller.handler("eval")
        assert handler is not None
        assert handler.providers_for_spec(draft.frozen_spec) == {"codex"}
        claim = controller.accept(snapshot, resolve=resolve)
        assert claim is not None
        assert controller.select_existing(snapshot) == claim
        assert controller.accept(snapshot, resolve=resolve) == claim
        run = controller.reserve_next(claim.id, lane="low", readiness=lambda: None)
        assert run is not None
        controller.record_result(
            run.id, AttemptResult("completed", "ready-for-human-review", {"score": 60})
        )
        assert controller.reserve_next(claim.id, lane="low", readiness=lambda: None) is None
        assert len(store.runs_for_claim(claim.id)) == 1
        assert store.get_hold(claim.id, "job-cap") is not None
        store.set_setting("job-cap", "reset", {"at": datetime.now(UTC).isoformat()})
        assert controller.reserve_next(claim.id, lane="low", readiness=lambda: None) is not None


def test_blocked_and_review_paths_hold_before_preparing(tmp_path: Path) -> None:
    from agent_factory.github import IssueComment, ReviewActivity
    from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
    from agent_factory.work_kinds.pull_request.kinds import FEATURE

    with closing(ClaimStore(tmp_path / "state.sqlite3", job_cap=JobCapConfig(1, 24))) as store:
        seed = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "eval", "fp", {}))
        run = store.reserve_run(
            seed.id, "one", lane="low", reason="initial", evidence_path="/tmp/e"
        )
        store.finish_run(run.id, execution_status="failed", result={})
        now = datetime.now(UTC)
        blocked = store.create_claim(
            ClaimDraft("example/work", 12, "I12", "P12", "feature", "fp", {})
        )
        store.set_claim_lifecycle(blocked.id, "blocked", {"declined_at": "2026-01-01T00:00:00Z"})

        class NoPreparation(PullRequestHandler):
            def prepare(self, claim: object) -> Preparation:
                pytest.fail("capped unblock prepared clones")

            def prepare_review(self, claim: object, review: object) -> Preparation:
                pytest.fail("capped review prepared clones")

        shared = SharedConfig.from_toml(gestures._SHARED_BASE + "\n[feature]\n")  # pyright: ignore[reportPrivateUsage]
        local = gestures.LocalConfig.from_toml(gestures._LOCAL_BASE)  # pyright: ignore[reportPrivateUsage]
        handler = NoPreparation(FEATURE, shared, local)
        handler.attach_store(store)
        current = store.get_claim(blocked.id)
        assert current is not None
        assert (
            process_blocked_claim(
                store,
                gestures.FakeGitHub([], {}),  # pyright: ignore[reportArgumentType]
                handler,
                shared,
                local,
                gestures._card("Ready"),
                current,  # pyright: ignore[reportPrivateUsage]
                lane="low",
                bot_login="example-factory[bot]",
                artifact_root=tmp_path,
                now=now,
            )
            is None
        )  # pyright: ignore[reportArgumentType]
        assert store.get_hold(blocked.id, "job-cap") is not None
        assert store.get_claim(blocked.id).lifecycle == "blocked"  # type: ignore[union-attr]

        review = store.create_claim(
            ClaimDraft("example/work", 64, "I64", "P64", "feature", "fp", {})
        )
        store.set_claim_lifecycle(
            review.id,
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
        activity = ReviewActivity(
            reviews=(),
            threads=(),
            comments=(
                IssueComment("comment-9", "please update", "writer", "2099-01-01T00:00:00Z"),
            ),
        )
        client = gestures.FakeReviewGitHub(activity, {"writer": "write"})
        review_claim = store.get_claim(review.id)
        assert review_claim is not None
        before = review_claim.outcome
        assert (
            process_review_claim(
                store,
                client,  # pyright: ignore[reportArgumentType]
                handler,
                review_claim,
                lane="low",
                bot_login="example-factory[bot]",
                artifact_root=tmp_path,
                now=now,
                local=local,
                readiness=lambda: True,
            )
            is None
        )  # pyright: ignore[reportArgumentType]
        assert store.get_hold(review.id, "job-cap") is not None
        assert store.get_claim(review.id).outcome == before  # type: ignore[union-attr]
