"""Sync claim orchestration: PR lookup, working-clone merge, closure, and reporting."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import cast

import pytest

from agent_factory.config import LocalConfig, RepositoryConfig, SharedConfig
from agent_factory.github import IssueComment
from agent_factory.store import ClaimDraft, ClaimStore, Run
from agent_factory.work_kinds.pull_request import launch, sync
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from agent_factory.work_kinds.pull_request.kinds import FIX
from agent_factory.work_kinds.pull_request.sync import sync_claim

_LOCAL_BASE = """\
shared_config = "/opt/agent-factory/config/codagent.toml"
storage_root = "~/.agent-factory"

[repositories]
agent_evals = "/srv/src/agent-evals"
agent_runner = "/srv/src/agent-runner"
agent_skills = "/srv/src/agent-skills"

[schedule]
timezone = "America/New_York"
poll_seconds = 60
start_hour = 0
stop_hour = 15

[limits]
minimum_free_gib = 0
inactivity_seconds = 1800
execution_seconds = 21600
total_seconds = 43200
codex_reset_fallback_seconds = 18000

[credentials]
github_app_key = "/etc/agent-factory/github-app.pem"
suite_environment = "/etc/agent-factory/suite.env"
"""


@dataclasses.dataclass
class PullRequestState:
    state: str
    merged_at: str | None


class FakeClient:
    def __init__(self, pr_state: PullRequestState) -> None:
        self._pr_state = pr_state
        self.closed: list[tuple[str, int]] = []
        self.labels: list[bool] = []
        self.comments: list[IssueComment] = []

    def get_pull_request(self, repository: str, number: int) -> PullRequestState:
        return self._pr_state

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return self.comments

    def create_comment(self, repository: str, number: int, body: str) -> str | None:
        comment_id = str(len(self.comments) + 1)
        self.comments.append(IssueComment(comment_id, body, "bot"))
        return comment_id

    def set_attention_label(self, repository: str, number: int, needed: bool) -> None:
        self.labels.append(needed)

    def close_issue(self, repository: str, number: int) -> None:
        self.closed.append((repository, number))


def _local(working_clones: dict[str, Path] | None = None) -> LocalConfig:
    base = LocalConfig.from_toml(_LOCAL_BASE)
    return dataclasses.replace(
        base,
        repositories=RepositoryConfig(
            agent_evals=base.repositories.agent_evals,
            agent_runner=base.repositories.agent_runner,
            agent_skills=base.repositories.agent_skills,
            working_clones=working_clones or {},
        ),
    )


def _settled_claim_with_pr(
    store: ClaimStore, *, pr_number: int = 214, repository: str = "example/work"
) -> str:
    claim = store.create_claim(ClaimDraft(repository, 212, "I212", "P212", "fix", "fp", {}))
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/ev")
    url = f"https://github.com/example/work/pull/{pr_number}"
    store.finish_run(
        run.id,
        execution_status="completed",
        result={"pr": {"url": url, "number": pr_number}},
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    return claim.id


def test_no_pr_recorded_is_a_no_op(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {}))
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "failed"})
    client = FakeClient(PullRequestState("OPEN", None))

    sync_claim(store, client, _local(), store.get_claim(claim.id), bot_login="bot", card_done=False)  # type: ignore[arg-type]

    assert client.closed == []
    assert client.labels == []


def test_agent_runner_sync_requests_a_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    store = ClaimStore(tmp_path / "state.sqlite3")
    repository = "codagent-ai/agent-runner"
    claim_id = _settled_claim_with_pr(store, repository=repository)
    client = FakeClient(PullRequestState("MERGED", "2026-01-01T00:00:00Z"))
    rebuild_values: list[bool] = []

    def record_merge(_clone: Path, *, rebuild: bool = False) -> None:
        rebuild_values.append(rebuild)
        return None

    monkeypatch.setattr(sync, "_merge_working_clone", record_merge)

    sync_claim(
        store,
        client,
        _local({repository: clone}),
        store.get_claim(claim_id),  # type: ignore[arg-type]
        bot_login="bot",
        card_done=False,
    )

    assert rebuild_values == [True]


def test_unmerged_pr_is_a_no_op(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    client = FakeClient(PullRequestState("OPEN", None))

    sync_claim(store, client, _local(), store.get_claim(claim_id), bot_login="bot", card_done=False)  # type: ignore[arg-type]

    assert client.closed == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert "sync" not in reloaded.reporting


def test_missing_working_clone_blocks_and_labels(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    client = FakeClient(PullRequestState("MERGED", "2026-01-01T00:00:00Z"))

    sync_claim(store, client, _local(), store.get_claim(claim_id), bot_login="bot", card_done=False)  # type: ignore[arg-type]

    assert client.closed == []
    assert client.labels == [True]
    assert len(client.comments) == 1
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    sync = cast(dict[str, object], reloaded.reporting["sync"])
    assert sync["blocked_reason"]
    assert not sync.get("completed")


def test_missing_working_clone_does_not_label_a_done_card(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    client = FakeClient(PullRequestState("MERGED", "2026-01-01T00:00:00Z"))

    sync_claim(store, client, _local(), store.get_claim(claim_id), bot_login="bot", card_done=True)  # type: ignore[arg-type]

    assert client.labels == []


def test_repeated_block_does_not_duplicate_the_comment(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    client = FakeClient(PullRequestState("MERGED", "2026-01-01T00:00:00Z"))

    sync_claim(store, client, _local(), store.get_claim(claim_id), bot_login="bot", card_done=False)  # type: ignore[arg-type]
    sync_claim(store, client, _local(), store.get_claim(claim_id), bot_login="bot", card_done=False)  # type: ignore[arg-type]

    assert len(client.comments) == 1


def test_successful_merge_closes_issue_and_records_completion(tmp_path: Path) -> None:
    import subprocess

    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "--quiet", "--bare", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(seed)], check=True)
    subprocess.run(["git", "-C", str(seed), "checkout", "-b", "main"], check=True)
    (seed / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(seed),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-m",
            "seed",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(seed), "push", "-u", "origin", "main"], check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(origin), str(clone)], check=True)

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _settled_claim_with_pr(store)
    client = FakeClient(PullRequestState("MERGED", "2026-01-01T00:00:00Z"))

    sync_claim(
        store,
        client,
        _local({"example/work": clone}),
        store.get_claim(claim_id),  # type: ignore[arg-type]
        bot_login="bot",
        card_done=False,
    )

    assert client.closed == [("example/work", 212)]
    assert client.labels == [False]
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert cast(dict[str, object], reloaded.reporting["sync"])["completed"] is True

    client.closed.clear()
    sync_claim(
        store,
        client,
        _local({"example/work": clone}),
        store.get_claim(claim_id),  # type: ignore[arg-type]
        bot_login="bot",
        card_done=False,
    )
    assert client.closed == []


# -- the host note on outcome comments (INT-008) ---------------------------------------------


def _handler(tmp_path: Path) -> tuple[PullRequestHandler, ClaimStore]:
    shared = SharedConfig.from_toml(Path("config/codagent.toml").read_text())
    handler = PullRequestHandler(FIX, shared, _local())
    store = ClaimStore(tmp_path / "state.sqlite3")
    handler.attach_store(store)
    return handler, store


def _finished_run(store: ClaimStore, claim_id: str, result: dict[str, object]) -> Run:
    run = store.reserve_run(claim_id, "fix", reason="initial", evidence_path="/tmp/ev")
    store.finish_run(run.id, execution_status="completed", result=result)
    finished = store.get_run(run.id)
    assert finished is not None
    return finished


@pytest.mark.parametrize("sandbox", ["host", "docker"])
def test_pull_request_and_failed_outcomes_carry_the_host_note_only_for_host_runs(
    tmp_path: Path, sandbox: str
) -> None:
    handler, store = _handler(tmp_path)
    url = "https://github.com/example/work/pull/9"
    pr: dict[str, object] = {"url": url, "number": 9}
    for outcome, verdict in (("pull-request", "pending-human-review"), ("failed", "failed")):
        claim = store.create_claim(
            ClaimDraft("example/work", 1, "I1", "P1", "fix", f"fp-{outcome}-{sandbox}", {})
        )
        run = _finished_run(
            store,
            claim.id,
            {"outcome": outcome, "reasons": ["r"], "pr": pr, "sandbox": sandbox},
        )
        settled = handler.settle(store.get_claim(claim.id), [run])  # type: ignore[arg-type]
        assert settled is not None and settled.verdict == verdict
        body = settled.event_body or ""
        assert url in body
        assert (launch.HOST_NOTE in body) is (sandbox == "host"), body


@pytest.mark.parametrize("sandbox", ["host", "docker"])
def test_needs_input_event_and_exhausted_message_carry_the_host_note_only_for_host_runs(
    tmp_path: Path, sandbox: str
) -> None:
    handler, store = _handler(tmp_path)
    claim = store.create_claim(
        ClaimDraft("example/work", 1, "I1", "P1", "fix", f"fp-{sandbox}", {})
    )
    run = _finished_run(
        store, claim.id, {"outcome": "needs-input", "reasons": ["which API?"], "sandbox": sandbox}
    )
    assert handler.settle(store.get_claim(claim.id), [run]) is None  # type: ignore[arg-type]
    events = store.pending_events(claim.id)
    assert len(events) == 1 and "which API?" in events[0].body
    assert (launch.HOST_NOTE in events[0].body) is (sandbox == "host")
    exhausted = handler.attempt_message(
        run, {"reason": "boom", "sandbox": sandbox}, stage="exhausted"
    )
    assert "infra-error" in exhausted
    assert (launch.HOST_NOTE in exhausted) is (sandbox == "host")


def test_admission_comment_is_identical_across_execution_modes(tmp_path: Path) -> None:
    handler, store = _handler(tmp_path)
    frozen = {
        "target": {"repository": "example/work", "branch": "main"},
        "branches": {"runner": "main", "skills": "main"},
        "revisions": {"target": "a" * 40, "runner": "b" * 40, "skills": "c" * 40},
        "roles": {"lead": "cursor:m:high"},
    }
    first = store.create_claim(ClaimDraft("example/work", 1, "I1", "P1", "fix", "fp1", frozen))
    second = store.create_claim(ClaimDraft("example/work", 2, "I2", "P2", "fix", "fp2", frozen))
    texts = [handler.frozen_inputs_event(c) or "" for c in (first, second)]
    # Only the per-claim fix branch line differs; nothing about the execution mode appears.
    stripped = {"\n".join(x for x in t.splitlines() if "fix branch" not in x) for t in texts}
    assert len(stripped) == 1
    assert all(launch.HOST_NOTE not in t and "host" not in t for t in texts)
