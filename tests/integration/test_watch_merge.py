"""Rated watch results pass through deterministic merge gates."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_factory.config import FixConfig, FixTarget, SharedConfig, WatchConfig
from agent_factory.github import (
    CommitChecks,
    GitHubMergeRejectedError,
    IssueComment,
    PullRequestState,
    ReviewActivity,
)
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.watch import deliver, merge, result
from agent_factory.watch import store as watch_store

HEAD = "a" * 40


class Client:
    def __init__(self) -> None:
        self.pull = PullRequestState("OPEN", None, False, True, "main", HEAD)
        self.checks = CommitChecks(True, (), (), ("CI",))
        self.activity = ReviewActivity((), (), ())
        self.merges: list[tuple[str, int, str]] = []
        self.comments: list[int] = []

    def get_pull_request(self, repository: str, number: int) -> PullRequestState:
        return self.pull

    def get_pull_request_details(self, repository: str, number: int) -> PullRequestState:
        return self.pull

    def merge_has_parent(self, repository: str, merge_sha: str, head_sha: str) -> bool:
        return head_sha == HEAD

    def commit_checks(self, repository: str, sha: str) -> CommitChecks:
        return self.checks

    def list_review_activity(self, repository: str, number: int) -> ReviewActivity:
        return self.activity

    def get_permission(self, repository: str, login: str) -> str:
        return "write"

    def merge_pull_request(self, repository: str, number: int, sha: str) -> str:
        self.merges.append((repository, number, sha))
        return "b" * 40

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return []

    def create_comment(self, repository: str, number: int, body: str) -> str:
        self.comments.append(number)
        return "1"


def setup(tmp_path: Path, level: str = "low") -> tuple[ClaimStore, dict[str, Any], SharedConfig]:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(ClaimDraft("o/r", 5, "I", "P", "fix", "fp", {}))
    now = datetime.now(UTC).isoformat()
    watch_store.insert(
        store,
        event_key="PR-READY:r",
        event_kind="PR-READY",
        claim_id=claim.id,
        run_id=None,
        repository="o/r",
        issue_number=5,
        pr_number=70,
        pr_url="https://github.com/o/r/pull/70",
        event_at=now,
        now=now,
    )
    row = watch_store.rows(store)[0]
    watch_store.update(store, row["id"], merge_json=json.dumps({"auto_merge": True}))
    deliver.end(
        store,
        row["id"],
        "completed",
        "",
        "config.toml",
        validated={
            "procedure": "pr-check",
            "summary": "checked",
            "issues_filed": [],
            "issues_updated": [],
            "risk": {"level": level, "head_sha": HEAD, "reasons": ["checked criteria"]},
        },
        result_json=json.dumps(
            {
                "procedure": "pr-check",
                "summary": "checked",
                "issues_filed": [],
                "issues_updated": [],
                "risk": {"level": level, "head_sha": HEAD, "reasons": ["checked criteria"]},
            }
        ),
    )
    shared = SharedConfig.from_file(Path("config/codagent.toml"))
    shared = replace(
        shared,
        watch=WatchConfig(
            True, "o/r", "claude:model:medium", auto_merge=True, expected_checks={"o/r": ("CI",)}
        ),
        fix=FixConfig(targets=(FixTarget("o/r", "main"),)),
    )
    return store, watch_store.get(store, row["id"]) or {}, shared


def test_low_risk_waits_then_merges_and_comments_on_pr(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = Client()
    try:
        client.checks = CommitChecks(True, ("CI",), ())
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert (
            watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")["state"]
            == "waiting"
        )
        assert not client.merges
        client.checks = CommitChecks(True, (), (), ("CI",))
        merge.step(store, client, shared)  # type: ignore[arg-type]
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert client.merges == [("o/r", 70, HEAD)]
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert client.comments == [70]
    finally:
        store.close()


def test_switch_off_ends_waiting_merge(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = Client()
    try:
        merge.step(store, client, replace(shared, watch=replace(shared.watch, auto_merge=False)))  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged" and saved["reason"] == "auto-merge off"
        assert not client.merges
    finally:
        store.close()


def test_head_move_and_no_checks_fail_closed(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = Client()
    try:
        client.pull = replace(client.pull, head_sha="c" * 40)
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["reason"] == "rated head moved"
        assert not client.merges
    finally:
        store.close()

    store, row, shared = setup(tmp_path / "other")
    client = Client()
    try:
        client.checks = CommitChecks(False, (), ())
        saved = watch_store.json_field(row, "merge_json")
        saved["checked_at"] = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
        watch_store.update(store, row["id"], merge_json=json.dumps(saved))
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged" and "no checks reported" in saved["reason"]
    finally:
        store.close()


def test_rating_required_only_when_launch_enabled() -> None:
    value: dict[str, Any] = {
        "procedure": "pr-check",
        "summary": "ok",
        "issues_filed": [],
        "issues_updated": [],
    }
    assert "risk" not in result.validate(value, "pr-check")
    with pytest.raises(ValueError, match="risk"):
        result.validate(value, "pr-check", auto_merge=True)
    value["risk"] = {"level": "low", "head_sha": HEAD, "reasons": ["checked"]}
    assert result.validate(value, "pr-check", auto_merge=True)["risk"]["head_sha"] == HEAD
    with pytest.raises(ValueError, match="absent"):
        result.validate(value, "pr-check")


def test_restart_after_merge_records_parent_without_second_request(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = Client()
    try:
        client.pull = replace(
            client.pull, state="MERGED", merged_at="2026-10-03T00:00:00Z", merge_commit_sha="b" * 40
        )
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "merged" and saved["merge_sha"] == "b" * 40
        assert not client.merges
    finally:
        store.close()


@pytest.mark.parametrize("gate", ["url", "base", "failed", "thread", "review"])
def test_failed_gate_never_sends_merge(tmp_path: Path, gate: str) -> None:
    from agent_factory.github import IssueComment, ReviewThread

    store, row, shared = setup(tmp_path)
    client = Client()
    try:
        if gate == "url":
            watch_store.update(store, row["id"], pr_url="https://github.com/other/r/pull/70")
        elif gate == "base":
            client.pull = replace(client.pull, base_ref="other")
        elif gate == "failed":
            client.checks = CommitChecks(True, (), ("CI",))
        elif gate == "thread":
            client.activity = ReviewActivity((), (ReviewThread("t", False, "file", 1, ()),), ())
        else:
            client.activity = ReviewActivity(
                (IssueComment("r", "", "writer", "2026-10-03T00:00:00Z", "CHANGES_REQUESTED"),),
                (),
                (),
            )
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged"
        assert not client.merges
    finally:
        store.close()


def test_medium_risk_never_enters_merge_queue(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path, level="medium")
    client = Client()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged" and saved["reason"] == "medium risk"
        assert not client.merges
        deliver.deliver(store, client, "factory[bot]")  # type: ignore[arg-type]
        assert client.comments == [70]
    finally:
        store.close()


class UncertainClient(Client):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0
        self.read_fails = False

    def get_pull_request_details(self, repository: str, number: int) -> PullRequestState:
        self.reads += 1
        if self.read_fails:
            raise RuntimeError("read unavailable")
        return self.pull

    def merge_pull_request(self, repository: str, number: int, sha: str) -> str:
        self.merges.append((repository, number, sha))
        self.read_fails = True
        raise RuntimeError("response lost")


def test_switch_off_finishes_without_github_read(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = UncertainClient()
    client.read_fails = True
    try:
        merge.step(store, client, replace(shared, watch=replace(shared.watch, auto_merge=False)))  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged" and saved["reason"] == "auto-merge off"
        assert client.reads == 0
    finally:
        store.close()


def test_lost_merge_response_is_reconciled_without_retry(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = UncertainClient()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "waiting" and saved["request_sent_at"]
        assert client.merges == [("o/r", 70, HEAD)]
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert client.merges == [("o/r", 70, HEAD)]
        client.read_fails = False
        client.pull = replace(
            client.pull, state="MERGED", merged_at="2026-10-03T00:00:00Z", merge_commit_sha="b" * 40
        )
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "merged"
        assert client.merges == [("o/r", 70, HEAD)]
    finally:
        store.close()


def test_turning_off_does_not_settle_a_request_with_uncertain_outcome(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    client = UncertainClient()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        client.read_fails = False
        off = replace(shared, watch=replace(shared.watch, auto_merge=False))
        merge.step(store, client, off)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "waiting"
        assert client.merges == [("o/r", 70, HEAD)]
        client.pull = replace(client.pull, state="MERGED", merged_at="2026-10-03T00:00:00Z")
        merge.step(store, client, off)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "waiting"
        client.pull = replace(
            client.pull,
            state="MERGED",
            merged_at="2026-10-03T00:00:00Z",
            merge_commit_sha="b" * 40,
        )
        merge.step(store, client, off)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "merged"
        assert client.merges == [("o/r", 70, HEAD)]
    finally:
        store.close()


def test_definitive_merge_rejection_ends_once(tmp_path: Path) -> None:
    class Rejected(Client):
        def merge_pull_request(self, repository: str, number: int, sha: str) -> str:
            self.merges.append((repository, number, sha))
            raise GitHubMergeRejectedError("approval required")

    store, row, shared = setup(tmp_path)
    client = Rejected()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "not-merged" and "approval required" in saved["reason"]
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert client.merges == [("o/r", 70, HEAD)]
    finally:
        store.close()


def test_expected_check_not_reported_waits_for_it(tmp_path: Path) -> None:
    store, row, shared = setup(tmp_path)
    shared = replace(
        shared, watch=replace(shared.watch, expected_checks={"o/r": ("CI", "security")})
    )
    client = Client()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        saved = watch_store.json_field(watch_store.get(store, row["id"]) or {}, "merge_json")
        assert saved["state"] == "waiting" and "security" in saved["reason"]
        assert not client.merges
        client.checks = CommitChecks(True, (), (), ("CI", "security"))
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert client.merges == [("o/r", 70, HEAD)]
    finally:
        store.close()


def test_expected_duplicate_check_requires_both_results(tmp_path: Path) -> None:
    store, _, shared = setup(tmp_path)
    shared = replace(shared, watch=replace(shared.watch, expected_checks={"o/r": ("CI", "CI")}))
    client = Client()
    try:
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert not client.merges
        client.checks = CommitChecks(True, (), (), ("CI", "CI"))
        merge.step(store, client, shared)  # type: ignore[arg-type]
        assert client.merges == [("o/r", 70, HEAD)]
    finally:
        store.close()
