"""Sync claim orchestration: PR lookup, working-clone merge, closure, and reporting."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import cast

from agent_factory.config import LocalConfig, RepositoryConfig
from agent_factory.github import IssueComment
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.fix.sync import sync_claim

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


def _settled_claim_with_pr(store: ClaimStore, *, pr_number: int = 214) -> str:
    claim = store.create_claim(
        ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {})
    )
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
        ["git", "-C", str(seed), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "commit", "-m", "seed"],
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
