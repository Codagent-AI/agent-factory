"""INT-006: blocked-claim re-admission, comment eligibility, and fix gestures."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import cast

from agent_factory.config import FixBranches, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.github import GitHubApiError, IssueComment, ProjectQueueItem
from agent_factory.routing import SourceItem
from agent_factory.store import ClaimDraft, ClaimStore
from agent_factory.work_kinds.fix.blocked import process_blocked_claim
from agent_factory.work_kinds.fix.handler import FixHandler

_SHARED_BASE = """\
[github]
organization = "Example Org"
bot_login = "example-factory[bot]"
app_id = "123"
installation_id = "456"

[project]
id = "PVT_example"
number = 7

[fields.status]
id = "status-field"
[fields.status.options]
ready = "ready-option"
running = "running-option"
review = "review-option"
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"

[fields.refs]
id = "refs-field"

[fields.verdict]
id = "verdict-field"
[fields.verdict.options]
pending-human-review = "pending-option"
failed = "failed-option"
quota-deferred = "quota-option"
infra-error = "infra-option"

[routing]
eval_source = "example/evals"
general_sources = ["example/evals", "example/work"]
eval_label = "run-eval"
eval_type = "Eval"

[eval]
harness_ref = "main"
suite = "and-scene"
repetitions = 3
"""

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


def _shared(*, with_targets: bool = False) -> SharedConfig:
    base = SharedConfig.from_toml(_SHARED_BASE)
    targets = (FixTarget("example/work"),) if with_targets else ()
    return dataclasses.replace(base, fix=FixConfig(targets=targets, branches=FixBranches()))


def _local() -> LocalConfig:
    return LocalConfig.from_toml(_LOCAL_BASE)


def _card(status: str = "Running") -> ProjectQueueItem:
    shared = _shared()
    return ProjectQueueItem(
        id="P212",
        content_id="I212",
        fields={shared.project.status.id: shared.project.status.option(status.lower())},
        source=SourceItem(
            id="I212",
            repository="example/work",
            number=212,
            author="writer",
            labels=frozenset({"needs-input"}),
            issue_type="Bug",
            state="OPEN",
        ),
    )


class FakeGitHub:
    def __init__(self, comments: list[IssueComment], permissions: dict[str, str | None]) -> None:
        self._comments = comments
        self._permissions = permissions
        self.permission_calls: list[str] = []
        self.labels: list[bool] = []

    def get_permission(self, repository: str, login: str) -> str | None:
        self.permission_calls.append(login)
        return self._permissions.get(login)

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        return self._comments

    def set_attention_label(self, repository: str, number: int, needed: bool) -> None:
        self.labels.append(needed)


def _blocked_claim(store: ClaimStore) -> str:
    claim = store.create_claim(
        ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {"contract": "factory-fix/1"})
    )
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01T00:00:00+00:00"})
    return claim.id


def _handler(store: ClaimStore, *, with_targets: bool = False) -> FixHandler:
    handler = FixHandler(_shared(with_targets=with_targets), _local())
    handler.attach_store(store)
    return handler


def test_writer_comment_after_decline_removes_label_and_reserves_unblock(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [
        IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00"),
    ]
    client = FakeGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=__import__("datetime").datetime(2026, 1, 3, tzinfo=__import__("datetime").UTC),
    )

    assert admitted is True
    assert client.labels == [False]
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "active"
    issue = cast(dict[str, object], reloaded.preparation["issue"])
    assert issue["comments"] == [{"author": "writer", "body": "please retry"}]
    runs = store.runs_for_claim(claim_id)
    assert len(runs) == 1
    assert runs[0].reason == "unblock"
    assert runs[0].unit_key == "fix"


def test_bot_comment_alone_changes_nothing(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [
        IssueComment("1", "automated note", "example-factory[bot]", "2026-01-02T00:00:00+00:00"),
    ]
    client = FakeGitHub(comments, {})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is False
    assert client.labels == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "blocked"
    assert store.runs_for_claim(claim_id) == []


def test_non_writer_comment_changes_nothing(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [
        IssueComment("1", "me too!", "rando", "2026-01-02T00:00:00+00:00"),
    ]
    client = FakeGitHub(comments, {"rando": "read"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is False
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "blocked"


def test_comment_before_decline_is_not_eligible(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [
        IssueComment("1", "old comment", "writer", "2025-12-31T00:00:00+00:00"),
    ]
    client = FakeGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is False


def test_drag_to_ready_without_comment_unblocks(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    client = FakeGitHub([], {})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Ready"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is True
    assert client.labels == [False]
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "active"


def test_slot_busy_leaves_claim_blocked(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    other = store.create_claim(ClaimDraft("example/work", 999, "I999", "P999", "fix", "fp2", {}))
    store.reserve_run(other.id, "fix", reason="initial", evidence_path="/tmp/other")
    client = FakeGitHub([], {})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Ready"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is False
    assert client.labels == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "blocked"


def test_gesture_returns_fresh_for_settled_card_in_ready() -> None:
    handler = FixHandler(_shared(), _local())
    claim_kwargs = dict(
        id="c1",
        repository="example/work",
        issue_number=212,
        issue_id="I212",
        project_item_id="P212",
        kind="fix",
        request_fingerprint="fp",
        frozen_spec={},
        outcome={},
        preparation={},
        reporting={},
        cleanup={},
    )
    from agent_factory.store import Claim

    settled = Claim(lifecycle="settled", **claim_kwargs)  # type: ignore[arg-type]
    assert handler.gesture(settled, _card("Ready"), []) == "fresh"
    assert handler.gesture(settled, _card("Running"), []) is None


def test_gesture_returns_unblock_for_blocked_card_in_ready() -> None:
    handler = FixHandler(_shared(), _local())
    from agent_factory.store import Claim

    claim_kwargs = dict(
        id="c1",
        repository="example/work",
        issue_number=212,
        issue_id="I212",
        project_item_id="P212",
        kind="fix",
        request_fingerprint="fp",
        frozen_spec={},
        outcome={},
        preparation={},
        reporting={},
        cleanup={},
    )
    blocked = Claim(lifecycle="blocked", **claim_kwargs)  # type: ignore[arg-type]
    assert handler.gesture(blocked, _card("Ready"), []) == "unblock"
    assert handler.gesture(blocked, _card("Running"), []) is None
    comments = [IssueComment("1", "b", "writer")]
    assert handler.gesture(blocked, _card("Running"), comments) == "unblock"


def test_comment_after_decline_is_eligible_across_iso8601_offset_notations(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    # The decline was recorded with a "+00:00" offset; the comment uses "Z" for the same
    # instant one second later. Raw string comparison would misorder these.
    comments = [IssueComment("1", "please retry", "writer", "2026-01-01T00:00:01Z")]
    client = FakeGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is True


def test_comment_with_missing_timestamp_is_not_eligible(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [IssueComment("1", "please retry", "writer", "")]
    client = FakeGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is False


def test_permission_lookup_failure_for_one_author_does_not_abort_the_scan(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [
        IssueComment("1", "from a broken lookup", "flaky", "2026-01-02T00:00:00+00:00"),
        IssueComment("2", "please retry", "writer", "2026-01-02T00:00:01+00:00"),
    ]

    class RaisingGitHub(FakeGitHub):
        def get_permission(self, repository: str, login: str) -> str | None:
            if login == "flaky":
                raise GitHubApiError("collaborator lookup failed")
            return super().get_permission(repository, login)

    client = RaisingGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is True
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    issue = cast(dict[str, object], reloaded.preparation["issue"])
    assert issue["comments"] == [{"author": "writer", "body": "please retry"}]
