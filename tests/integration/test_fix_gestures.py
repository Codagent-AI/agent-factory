"""INT-006: blocked-claim re-admission, comment eligibility, and fix gestures."""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast
from unittest import mock

import pytest

from agent_factory.config import FixBranches, FixConfig, FixTarget, LocalConfig, SharedConfig
from agent_factory.github import (
    BranchInfo,
    GitHubApiError,
    IssueComment,
    ProjectQueueItem,
    PullRequestState,
    ReviewActivity,
    ReviewThread,
)
from agent_factory.routing import SourceItem
from agent_factory.store import Claim, ClaimDraft, ClaimStore
from agent_factory.suites.and_scene import WorktreeError
from agent_factory.work_kinds.base import Preparation
from agent_factory.work_kinds.pull_request.blocked import eligible_comments, process_blocked_claim
from agent_factory.work_kinds.pull_request.handler import PullRequestHandler
from agent_factory.work_kinds.pull_request.kinds import FIX
from agent_factory.work_kinds.pull_request.review import process_review_claim

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


class _PreparedHandler(PullRequestHandler):
    """Reconciliation and clones are covered elsewhere; here preparation is a no-op."""

    def prepare(self, claim: Claim) -> Preparation:
        return Preparation()


def _handler(store: ClaimStore, *, with_targets: bool = False) -> PullRequestHandler:
    handler = _PreparedHandler(FIX, _shared(with_targets=with_targets), _local())
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

    assert admitted is not None
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

    assert admitted is None
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

    assert admitted is None
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

    assert admitted is None


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

    assert admitted is not None
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

    assert admitted is None
    assert client.labels == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    assert reloaded.lifecycle == "blocked"


def test_gesture_returns_fresh_for_settled_card_in_ready() -> None:
    handler = PullRequestHandler(FIX, _shared(), _local())
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
    settled = Claim(lifecycle="settled", **claim_kwargs)  # type: ignore[arg-type]
    assert handler.gesture(settled, _card("Ready"), []) == "fresh"
    assert handler.gesture(settled, _card("Running"), []) is None


def test_gesture_returns_unblock_for_blocked_card_in_ready() -> None:
    handler = PullRequestHandler(FIX, _shared(), _local())
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

    assert admitted is not None


def test_unparsable_decline_timestamp_fails_closed_for_all_comments() -> None:
    comments = [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")]

    result = eligible_comments(
        comments, since="not-a-timestamp", bot_login="bot", permission=lambda _: "write"
    )

    assert result == []


def test_blocked_claim_with_unparsable_declined_at_is_never_unblocked_by_comment(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft("example/work", 212, "I212", "P212", "fix", "fp", {"contract": "factory-fix/1"})
    )
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "not-a-timestamp"})
    comments = [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")]
    client = FakeGitHub(comments, {"writer": "write"})
    handler = _handler(store)
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    import datetime as dt

    admitted = process_blocked_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        _shared(),
        _local(),
        _card("Running"),
        reloaded,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=dt.datetime(2026, 1, 3, tzinfo=dt.UTC),
    )

    assert admitted is None


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

    assert admitted is None


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

    assert admitted is not None
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None
    issue = cast(dict[str, object], reloaded.preparation["issue"])
    assert issue["comments"] == [{"author": "writer", "body": "please retry"}]


def _blocked_claim_with_roles(store: ClaimStore, roles: dict[str, str]) -> str:
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            213,
            "I213",
            "P213",
            "fix",
            "fp",
            {"contract": "factory-fix/1", "roles": roles},
        )
    )
    store.set_claim_lifecycle(claim.id, "blocked", {"declined_at": "2026-01-01T00:00:00+00:00"})
    return claim.id


def _process(store: ClaimStore, client: FakeGitHub, claim_id: str, tmp_path: Path) -> object:
    import datetime as dt

    handler = _handler(store)
    claim = store.get_claim(claim_id)
    assert claim is not None
    return process_blocked_claim(
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


def test_provider_quota_hold_blocks_unblock_for_that_provider(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim_with_roles(store, {"lead": "codex:gpt:high"})
    store.set_setting("admission", "quota:codex", {"until": "2026-01-04T00:00:00+00:00"})
    comments = [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")]
    client = FakeGitHub(comments, {"writer": "write"})

    assert _process(store, client, claim_id, tmp_path) is None
    assert store.nonterminal_runs(kind="fix") == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None and reloaded.lifecycle == "blocked"


def test_provider_quota_hold_for_another_provider_does_not_block_unblock(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim_with_roles(store, {"lead": "cursor:model:high"})
    store.set_setting("admission", "quota:codex", {"until": "2026-01-04T00:00:00+00:00"})
    comments = [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")]
    client = FakeGitHub(comments, {"writer": "write"})

    assert _process(store, client, claim_id, tmp_path) is not None
    assert len(store.nonterminal_runs(kind="fix")) == 1


class _ReconcilingHandler(_PreparedHandler):
    """Counts reconciliations so the memory gate's split can be observed."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        self.reconciled = 0

    def reconcile(self, claim: Claim) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.reconciled += 1


def test_without_memory_headroom_an_eligible_claim_is_reconciled_but_not_relaunched(
    tmp_path: Path,
) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    client = FakeGitHub(
        [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")],
        {"writer": "write"},
    )
    handler = _ReconcilingHandler(FIX, _shared(), _local())
    handler.attach_store(store)
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
        memory_available=False,
    )

    assert admitted is None
    assert handler.reconciled == 1
    assert store.runs_for_claim(claim_id) == []
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None and reloaded.lifecycle == "blocked"
    assert client.labels == [], "the needs-input label stays until an attempt starts"


def test_losing_the_slot_race_after_preparing_discards_the_fresh_clones(tmp_path: Path) -> None:
    from agent_factory.store import NonterminalRunError

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    clone = tmp_path / "clones" / "attempt" / "repo"
    clone.mkdir(parents=True)

    class CloningHandler(_PreparedHandler):
        def prepare(self, claim: Claim) -> Preparation:
            return Preparation(payload={"clones": {"repo": str(clone)}})

    handler = CloningHandler(FIX, _shared(), _local())
    handler.attach_store(store)
    client = FakeGitHub(
        [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")],
        {"writer": "write"},
    )
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    def lose_race(*args: object, **kwargs: object) -> object:
        raise NonterminalRunError("another fix attempt was reserved first")

    with mock.patch.object(store, "reserve_run", side_effect=lose_race):
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

    assert admitted is None
    assert not clone.exists()
    reloaded = store.get_claim(claim_id)
    assert reloaded is not None and reloaded.lifecycle == "blocked"


def test_launch_input_fails_closed_when_a_commenter_permission_cannot_be_verified(
    tmp_path: Path,
) -> None:
    from agent_factory.suites.and_scene import ReadinessError

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    comments = [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")]

    class IssueGitHub(FakeGitHub):
        def get_source_item(self, repository: str, number: int) -> SourceItem:
            return _card().source

        def get_permission(self, repository: str, login: str) -> str | None:
            raise GitHubApiError("collaborator lookup failed")

    handler = PullRequestHandler(FIX, _shared(), _local())
    handler.attach_store(store)
    handler.attach_github(IssueGitHub(comments, {}))  # pyright: ignore[reportArgumentType]
    claim = store.get_claim(claim_id)
    assert claim is not None

    with pytest.raises(ReadinessError, match="cannot verify commenter permission for writer"):
        handler._issue_input(claim)  # pyright: ignore[reportPrivateUsage]


def test_clone_removal_failure_after_a_lost_slot_race_is_reported(tmp_path: Path) -> None:
    from agent_factory.store import NonterminalRunError

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim_id = _blocked_claim(store)
    clone = tmp_path / "clones" / "attempt" / "repo"
    clone.mkdir(parents=True)

    class CloningHandler(_PreparedHandler):
        def prepare(self, claim: Claim) -> Preparation:
            return Preparation(payload={"clones": {"repo": str(clone)}})

    handler = CloningHandler(FIX, _shared(), _local())
    handler.attach_store(store)
    client = FakeGitHub(
        [IssueComment("1", "please retry", "writer", "2026-01-02T00:00:00+00:00")],
        {"writer": "write"},
    )
    claim = store.get_claim(claim_id)
    assert claim is not None
    import datetime as dt

    def lose_race(*args: object, **kwargs: object) -> object:
        raise NonterminalRunError("another fix attempt was reserved first")

    with (
        mock.patch.object(store, "reserve_run", side_effect=lose_race),
        mock.patch(
            "agent_factory.work_kinds.pull_request.blocked.shutil.rmtree",
            side_effect=PermissionError("busy"),
        ),
    ):
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

    assert admitted is None
    events = {event.key: event.body for event in store.pending_events(claim_id)}
    assert str(clone) in events["unblock-clone-cleanup"]
    assert "busy" in events["unblock-clone-cleanup"]


# --- review intake: PR record lives on the run result, not the settled outcome ---


class FakeReviewGitHub:
    def __init__(self, activity: ReviewActivity, permissions: dict[str, str | None]) -> None:
        self._activity = activity
        self._permissions = permissions
        self.labels: list[bool] = []

    def get_permission(self, repository: str, login: str) -> str | None:
        return self._permissions.get(login)

    def get_pull_request(self, repository: str, number: int) -> PullRequestState:
        return PullRequestState("OPEN", None)

    def list_review_activity(self, repository: str, number: int) -> ReviewActivity:
        return self._activity

    def get_branch(self, repository: str, branch: str) -> BranchInfo | None:
        return BranchInfo(branch, "live-head")

    def get_source_item(self, repository: str, number: int) -> SourceItem:
        return SourceItem(
            "I64",
            repository,
            number,
            "writer",
            frozenset(),
            "Bug",
            "OPEN",
            body="Original bug body",
            title="Original bug title",
        )

    def set_attention_label(self, repository: str, number: int, needed: bool) -> None:
        self.labels.append(needed)


class _ReviewPreparedHandler(PullRequestHandler):
    def prepare_review(self, claim: Claim, review: Mapping[str, object]) -> Preparation:
        return Preparation(payload={"review": dict(review)})


def test_review_intake_reads_the_pr_from_the_latest_run_when_the_outcome_lacks_it(
    tmp_path: Path,
) -> None:
    """A claim settled by the controller carries only a verdict; the PR is on the run."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = store.create_claim(
        ClaimDraft("example/work", 64, "I64", "P64", "fix", "fp", {"contract": "factory-fix/1"})
    )
    older = store.reserve_run(claim.id, "fix", reason="initial", evidence_path=str(tmp_path))
    store.mark_running(older.id, {"pid": 1})
    store.finish_run(
        older.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "pr": {
                "url": "https://example/pr/101",
                "number": 101,
                "branch": "factory/fix-64-old",
                "head_sha": "old",
            },
        },
    )
    run = store.reserve_run(claim.id, "fix", reason="unblock", evidence_path=str(tmp_path))
    store.mark_running(run.id, {"pid": 1})
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "pr": {
                "url": "https://example/pr/113",
                "number": 113,
                "branch": "factory/fix-64",
                "head_sha": "abc",
            },
        },
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    activity = ReviewActivity(
        reviews=(),
        threads=(
            ReviewThread(
                "T1",
                False,
                "a.go",
                3,
                (IssueComment("c1", "remove test changes", "writer", "2099-01-01T00:00:00+00:00"),),
            ),
        ),
        comments=(),
    )
    client = FakeReviewGitHub(activity, {"writer": "write"})
    handler = _ReviewPreparedHandler(FIX, _shared(), _local())
    handler.attach_store(store)
    store.set_preparation(
        claim.id,
        {"issue": {"title": "Frozen original title", "body": "Frozen original body"}},
    )
    settled = store.get_claim(claim.id)
    assert settled is not None

    admitted = process_review_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        settled,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC),
        local=_local(),
        readiness=lambda: True,
    )

    assert admitted is not None
    review_run, preparation = admitted
    assert review_run.reason == "review"
    review = cast(dict[str, object], preparation.payload["review"])
    assert review["kind"] == "fix"
    assert review["branch"] == "factory/fix-64"
    # The round starts from the head observed on this poll, not the one saved at settle time.
    assert review["head_sha"] == "live-head"
    assert cast(dict[str, object], review["pull_request"])["number"] == 113
    assert cast(dict[str, object], review["pull_request"])["head_sha"] == "live-head"
    assert review["title"] == "Frozen original title"
    assert review["body"] == "Frozen original body"
    assert client.labels == [False]
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert reloaded.lifecycle == "active"
    assert reloaded.outcome["pre_review_verdict"] == "pending-human-review"
    assert "waiting_review" not in reloaded.outcome


def _settled_claim_with_pr(store: ClaimStore, roles: Mapping[str, str] | None = None) -> Claim:
    claim = store.create_claim(
        ClaimDraft(
            "example/work",
            64,
            "I64",
            "P64",
            "fix",
            "fp",
            {
                "contract": "factory-fix/1",
                "target": {"repository": "example/work"},
                "revisions": {"target": "t", "runner": "r", "skills": "s"},
                "roles": dict(roles or {}),
            },
        )
    )
    run = store.reserve_run(claim.id, "fix", reason="initial", evidence_path="/tmp/e")
    store.mark_running(run.id, {"pid": 1})
    store.finish_run(
        run.id,
        execution_status="completed",
        result={
            "outcome": "pull-request",
            "pr": {
                "url": "https://x/113",
                "number": 113,
                "branch": "factory/fix-64",
                "head_sha": "abc",
            },
        },
    )
    store.set_claim_lifecycle(claim.id, "settled", {"verdict": "pending-human-review"})
    settled = store.get_claim(claim.id)
    assert settled is not None
    return settled


_ACTIVITY = ReviewActivity(
    reviews=(),
    threads=(
        ReviewThread(
            "T1",
            False,
            "a.go",
            3,
            (IssueComment("c1", "remove test changes", "writer", "2099-01-01T00:00:00+00:00"),),
        ),
    ),
    comments=(),
)


class _UnreadyReviewHandler(PullRequestHandler):
    def prepare_review(self, claim: Claim, review: Mapping[str, object]) -> Preparation:
        raise WorktreeError("recorded commit abc is unavailable in the mirror")


def test_review_round_readiness_failure_holds_the_claim_in_review(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)
    client = FakeReviewGitHub(_ACTIVITY, {"writer": "write"})
    handler = _UnreadyReviewHandler(FIX, _shared(), _local())
    handler.attach_store(store)

    admitted = process_review_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC),
        local=_local(),
        readiness=lambda: True,
    )

    assert admitted is None
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert reloaded.lifecycle == "settled"
    assert cast(dict[str, object], reloaded.outcome["pr"])["number"] == 113
    assert reloaded.outcome["waiting_review"]
    assert [run.reason for run in store.runs_for_claim(claim.id)] == ["initial"]
    assert any(
        "Cannot start the review round yet" in event.body
        for event in store.pending_events(claim.id)
    )
    assert client.labels == []


class _RecordingWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[str] = []

    def fetch_mirror(self, repository: str, token: str | None) -> None:
        self.calls.append(f"fetch:{repository}:{token}")

    def prepare_review_clones(
        self,
        claim_id: str,
        attempt: int,
        repository: str,
        revisions: Mapping[str, object],
        *,
        branch: str,
        head_sha: str,
    ) -> dict[str, str]:
        self.calls.append(f"clone:{branch}@{head_sha}")
        return {"repo": str(self.root), "runner": str(self.root), "skills": str(self.root)}


def test_prepare_review_fetches_the_mirror_before_cutting_clones(tmp_path: Path) -> None:
    """The PR head was pushed after the mirror was last fetched for the claim."""
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)
    workspace = _RecordingWorkspace(tmp_path)
    handler = PullRequestHandler(
        FIX,
        _shared(),
        _local(),
        workspace=workspace,  # pyright: ignore[reportArgumentType]
    )
    handler.attach_store(store)
    handler.attach_installation_token(lambda: "tok")
    review = {"branch": "factory/fix-64", "head_sha": "abc"}

    with (
        mock.patch("agent_factory.work_kinds.pull_request.launch.check_runner_contract"),
        mock.patch("agent_factory.work_kinds.pull_request.launch.check_packaged_workflow"),
        mock.patch("agent_factory.work_kinds.pull_request.launch.check_target_catalog"),
    ):
        preparation = handler.prepare_review(claim, review)

    assert workspace.calls == ["fetch:example/work:tok", "clone:factory/fix-64@abc"]
    assert preparation.payload["attempt"] == 1


def _admit_review(
    store: ClaimStore,
    client: FakeReviewGitHub,
    claim: Claim,
    tmp_path: Path,
    *,
    readiness: Callable[[], bool] = lambda: True,
) -> tuple[object, Preparation] | None:
    handler = _ReviewPreparedHandler(FIX, _shared(), _local())
    handler.attach_store(store)
    return process_review_claim(
        store,
        client,  # pyright: ignore[reportArgumentType]
        handler,
        claim,
        bot_login="example-factory[bot]",
        artifact_root=tmp_path / "artifacts",
        now=datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC),
        local=_local(),
        readiness=readiness,
    )


def test_review_round_waits_when_the_pr_branch_head_cannot_be_read(tmp_path: Path) -> None:
    class Headless(FakeReviewGitHub):
        def get_branch(self, repository: str, branch: str) -> BranchInfo | None:
            raise GitHubApiError("branch lookup failed")

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)

    assert _admit_review(store, Headless(_ACTIVITY, {"writer": "write"}), claim, tmp_path) is None
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None and reloaded.lifecycle == "settled"
    assert "review_checkpoint" not in reloaded.outcome
    assert store.nonterminal_runs(kind="fix") == []


def test_review_round_waits_when_fix_kind_readiness_fails(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)
    client = FakeReviewGitHub(_ACTIVITY, {"writer": "write"})

    admitted = _admit_review(store, client, claim, tmp_path, readiness=lambda: False)

    assert admitted is None
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None and reloaded.lifecycle == "settled"
    assert reloaded.outcome["waiting_review"]
    assert [run.reason for run in store.runs_for_claim(claim.id)] == ["initial"]
    assert client.labels == []


def test_claim_quota_hold_blocks_a_review_round(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)
    store.set_hold(claim.id, "quota", {"until": "2099-01-03T00:00:00+00:00"})

    client = FakeReviewGitHub(_ACTIVITY, {"writer": "write"})
    assert _admit_review(store, client, claim, tmp_path) is None
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None and reloaded.lifecycle == "settled"
    assert reloaded.outcome["waiting_review"]
    assert store.nonterminal_runs(kind="fix") == []


def test_provider_quota_hold_blocks_a_review_round_for_that_provider(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store, roles={"lead": "codex:gpt:high"})
    store.set_setting("admission", "quota:codex", {"until": "2099-01-03T00:00:00+00:00"})

    client = FakeReviewGitHub(_ACTIVITY, {"writer": "write"})
    assert _admit_review(store, client, claim, tmp_path) is None
    assert store.nonterminal_runs(kind="fix") == []

    store.set_setting("admission", "quota:codex", {"until": "2099-01-01T00:00:00+00:00"})
    store.set_setting("admission", "quota:cursor", {"until": "2099-01-03T00:00:00+00:00"})
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None
    assert _admit_review(store, client, reloaded, tmp_path) is not None


def test_review_round_still_launches_when_label_removal_fails(tmp_path: Path) -> None:
    class Unlabelable(FakeReviewGitHub):
        def set_attention_label(self, repository: str, number: int, needed: bool) -> None:
            raise GitHubApiError("label update failed")

    store = ClaimStore(tmp_path / "state.sqlite3")
    claim = _settled_claim_with_pr(store)

    admitted = _admit_review(store, Unlabelable(_ACTIVITY, {"writer": "write"}), claim, tmp_path)

    assert admitted is not None
    reloaded = store.get_claim(claim.id)
    assert reloaded is not None and reloaded.lifecycle == "active"
