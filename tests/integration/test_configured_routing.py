from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agent_factory import github as github_module
from agent_factory import routing
from agent_factory.config import ConfigurationError, SharedConfig
from agent_factory.routing import ProjectItem, RouteEvent, Router, SourceItem


def _items() -> dict[str, ProjectItem]:
    return {}


def _permissions() -> dict[tuple[str, str], str | None]:
    return {}


def _comments() -> dict[str, list[str]]:
    return {}


def _issue_types() -> dict[str, str]:
    return {}


def config_text(*, harness_ref: str = "main", extra_eval: str = "") -> str:
    return f'''\
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
backlog = "backlog-option"
ready = "ready-option"
done = "done-option"

[fields.owner]
id = "owner-field"
[fields.owner.options]
factory = "factory-option"
human = "human-option"

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
bug_type = "Bug"
hold_label = "factory-hold"

[eval]
harness_ref = "{harness_ref}"
suite = "and-scene"
repetitions = 3
{extra_eval}
'''


@dataclass
class MemoryGitHub:
    items: dict[str, ProjectItem] = field(default_factory=_items)
    permissions: dict[tuple[str, str], str | None] = field(default_factory=_permissions)
    comments: dict[str, list[str]] = field(default_factory=_comments)
    issue_types: dict[str, str] = field(default_factory=_issue_types)
    added: int = 0

    def get_permission(self, repository: str, login: str) -> str | None:
        return self.permissions.get((repository, login))

    def set_issue_type(self, repository: str, number: int, issue_type: str) -> None:
        self.issue_types[f"{repository}#{number}"] = issue_type

    def find_project_item(self, project_id: str, content_id: str) -> ProjectItem | None:
        return self.items.get(content_id)

    def add_project_item(self, project_id: str, content_id: str) -> ProjectItem:
        self.added += 1
        return self.items.setdefault(content_id, ProjectItem(f"item-{content_id}", content_id, {}))

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        item = next(value for value in self.items.values() if value.id == item_id)
        item.fields[field_id] = option_id

    def list_comments(self, repository: str, number: int) -> list[str]:
        return self.comments.get(f"{repository}#{number}", [])

    def create_comment(self, repository: str, number: int, body: str) -> None:
        self.comments.setdefault(f"{repository}#{number}", []).append(body)


def item(
    *, labels: set[str] | None = None, state: str = "open", issue_type: str | None = "Eval"
) -> SourceItem:
    return SourceItem(
        id="ISSUE-1",
        repository="example/evals",
        number=42,
        author="writer",
        labels=frozenset(labels or {"run-eval"}),
        issue_type=issue_type,
        state=state,
    )


def bug_item(
    *,
    id: str = "ISSUE-BUG-1",
    repository: str = "example/work",
    number: int = 99,
    author: str = "writer",
    labels: set[str] | None = None,
    pull_request: bool = False,
) -> SourceItem:
    return SourceItem(
        id=id,
        repository=repository,
        number=number,
        author=author,
        labels=frozenset(labels or set()),
        issue_type="Bug",
        state="open",
        pull_request=pull_request,
    )


def test_authorized_eval_precedes_general_intake_and_initializes_owner_before_ready() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/evals", "writer"): "write"})

    result = Router(config, github).route(RouteEvent(item(issue_type=None)))

    project_item = github.items["ISSUE-1"]
    assert result.destination == "ready"
    assert project_item.fields == {"owner-field": "factory-option", "status-field": "ready-option"}
    assert github.issue_types == {"example/evals#42": "Eval"}
    assert github.added == 1


def test_repeated_delivery_preserves_later_human_field_edits() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/evals", "writer"): "admin"})
    router = Router(config, github)
    router.route(RouteEvent(item()))
    github.items["ISSUE-1"].fields["status-field"] = "human-selected-option"

    router.route(RouteEvent(item()))

    assert github.added == 1
    assert github.items["ISSUE-1"].fields["status-field"] == "human-selected-option"


def test_unknown_permission_routes_marked_eval_to_backlog_without_owner() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub()

    result = Router(config, github).route(RouteEvent(item()))

    assert result.destination == "backlog"
    assert github.items["ISSUE-1"].fields == {"status-field": "backlog-option"}
    assert github.issue_types == {}


def test_later_authorization_updates_factory_initialization_without_overwriting_human_edits() -> (
    None
):
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub()
    router = Router(config, github)
    router.route(RouteEvent(item()))
    github.permissions[("example/evals", "writer")] = "maintain"

    result = router.route(RouteEvent(item()))

    assert result.destination == "ready"
    assert github.items["ISSUE-1"].fields == {
        "owner-field": "factory-option",
        "status-field": "ready-option",
    }


def test_closure_moves_existing_project_card_to_done_without_reinitializing() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(
        items={"ISSUE-1": ProjectItem("item-ISSUE-1", "ISSUE-1", {"owner-field": "factory-option"})}
    )

    result = Router(config, github).route(RouteEvent(item(state="closed")))

    assert result.destination == "done"
    assert github.items["ISSUE-1"].fields == {
        "owner-field": "factory-option",
        "status-field": "done-option",
    }


def test_shared_config_rejects_a_harness_commit_sha() -> None:
    with pytest.raises(ConfigurationError, match="harness_ref"):
        SharedConfig.from_toml(config_text(harness_ref="a" * 40))


def test_shared_config_rejects_a_short_harness_commit_id() -> None:
    with pytest.raises(ConfigurationError, match="harness_ref"):
        SharedConfig.from_toml(config_text(harness_ref="deadbee"))


def test_shared_config_rejects_a_leftover_harness_sha_key() -> None:
    with pytest.raises(ConfigurationError, match="harness_ref"):
        SharedConfig.from_toml(config_text(extra_eval='harness_sha = "' + "a" * 40 + '"'))


def test_shared_config_defaults_harness_ref_to_main() -> None:
    text = config_text().replace('harness_ref = "main"\n', "")
    config = SharedConfig.from_toml(text)

    assert config.eval.harness_ref == "main"


def test_shared_config_exposes_configured_reporting_field_mappings() -> None:
    config = SharedConfig.from_toml(config_text())

    assert config.project.refs.id == "refs-field"
    assert config.project.verdict.option("infra-error") == "infra-option"


@pytest.mark.parametrize("event_type", ["issues", "pull_request"])
def test_actions_entry_point_routes_real_event_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event_type: str
) -> None:
    class EventGitHub(MemoryGitHub):
        def get_source_item(self, repository: str, number: int) -> SourceItem:
            assert repository == "example/evals"
            assert number == 42
            return item()

    client = EventGitHub(permissions={("example/evals", "writer"): "write"})

    def client_factory(runner: object, token: Callable[[], str]) -> EventGitHub:
        assert token() == "test-installation-token"
        return client

    payload: dict[str, object] = {
        "action": "opened",
        "repository": {"full_name": "example/evals"},
    }
    if event_type == "issues":
        payload["issue"] = {"number": 42}
    else:
        payload["number"] = 42
        payload["pull_request"] = {"number": 42}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload))
    config_path = tmp_path / "factory.toml"
    config_path.write_text(config_text())
    monkeypatch.setattr(
        sys, "argv", ["routing", "--config", str(config_path), "--event", str(event_path)]
    )
    monkeypatch.setenv("GH_TOKEN", "test-installation-token")
    monkeypatch.setattr(github_module, "GitHubClient", client_factory)

    routing.main()

    assert client.items["ISSUE-1"].fields == {
        "owner-field": "factory-option",
        "status-field": "ready-option",
    }
    assert client.added == 1


def test_eval_source_must_be_an_explicit_configured_source() -> None:
    text = config_text().replace(
        'general_sources = ["example/evals", "example/work"]',
        'general_sources = ["example/work"]',
    )
    with pytest.raises(ConfigurationError, match="eval_source.*general_sources"):
        SharedConfig.from_toml(text)


def test_writer_bug_routes_to_ready_with_factory_owner_and_writes_receipt() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})

    result = Router(config, github).route(RouteEvent(bug_item()))

    assert result.destination == "ready"
    assert github.items["ISSUE-BUG-1"].fields == {
        "owner-field": "factory-option",
        "status-field": "ready-option",
    }
    assert github.comments["example/work#99"]


def test_non_writer_bug_enters_backlog_without_ownership() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "outsider"): "read"})

    result = Router(config, github).route(RouteEvent(bug_item(author="outsider")))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {"status-field": "backlog-option"}


def test_failed_permission_lookup_routes_bug_to_backlog_without_ownership() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub()

    result = Router(config, github).route(RouteEvent(bug_item()))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {"status-field": "backlog-option"}


def test_hold_label_routes_bug_to_backlog_with_human_owner() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})

    result = Router(config, github).route(RouteEvent(bug_item(labels={"factory-hold"})))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {
        "owner-field": "human-option",
        "status-field": "backlog-option",
    }


def test_hold_bypass_is_sticky_after_the_label_is_removed() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})
    router = Router(config, github)
    router.route(RouteEvent(bug_item(labels={"factory-hold"})))

    result = router.route(RouteEvent(bug_item(labels=set())))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {
        "owner-field": "human-option",
        "status-field": "backlog-option",
    }


def test_hold_bypass_remains_sticky_across_a_second_post_hold_event() -> None:
    """The backlog-fallback receipt write after a hold must not drop the sticky flag."""
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})
    router = Router(config, github)
    router.route(RouteEvent(bug_item(labels={"factory-hold"})))
    router.route(RouteEvent(bug_item(labels=set())))

    result = router.route(RouteEvent(bug_item(labels=set())))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {
        "owner-field": "human-option",
        "status-field": "backlog-option",
    }


def test_hold_bypass_survives_a_receipt_write_that_actually_changes_fields() -> None:
    """A generic-fallback receipt write (forced by a stale receipt) must preserve the flag.

    Reproduces the reported gap directly: seed a hold_bypassed receipt whose recorded
    status does not match the generic fallback's desired backlog value, so `_initialize`
    cannot early-return and must actually call `_write_receipt`. Before the fix, that
    write dropped `hold_bypassed`, letting the very next event auto-admit the bug.
    """
    from agent_factory.routing import _RECEIPT_PREFIX  # pyright: ignore[reportPrivateUsage]

    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})
    stale_receipt = {
        "project": "PVT_example",
        "item": "item-ISSUE-BUG-1",
        "values": {"status-field": "ready-option"},
        "complete": True,
        "hold_bypassed": True,
    }
    github.items["ISSUE-BUG-1"] = ProjectItem(
        "item-ISSUE-BUG-1",
        "ISSUE-BUG-1",
        {"owner-field": "human-option", "status-field": "ready-option"},
    )
    github.comments["example/work#99"] = [f"{_RECEIPT_PREFIX}{json.dumps(stale_receipt)} -->"]
    router = Router(config, github)
    router.route(RouteEvent(bug_item(labels=set())))

    result = router.route(RouteEvent(bug_item(labels=set())))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields["owner-field"] == "human-option"


def test_pull_request_typed_bug_is_not_routed_as_a_bug() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})

    result = Router(config, github).route(RouteEvent(bug_item(pull_request=True)))

    assert result.destination == "backlog"
    assert github.items["ISSUE-BUG-1"].fields == {"status-field": "backlog-option"}
    assert github.issue_types == {}


def test_eval_labelled_bug_in_eval_source_applies_eval_rule_not_bug_rule() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/evals", "writer"): "write"})

    source = SourceItem(
        id="ISSUE-BOTH",
        repository="example/evals",
        number=7,
        author="writer",
        labels=frozenset({"run-eval"}),
        issue_type="Bug",
        state="open",
    )

    result = Router(config, github).route(RouteEvent(source))

    assert result.destination == "ready"
    assert github.items["ISSUE-BOTH"].fields == {
        "owner-field": "factory-option",
        "status-field": "ready-option",
    }
    assert github.issue_types == {"example/evals#7": "Eval"}


def test_human_hold_on_a_routed_bug_survives_re_delivery() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/work", "writer"): "write"})
    router = Router(config, github)
    router.route(RouteEvent(bug_item()))
    github.items["ISSUE-BUG-1"].fields["owner-field"] = "human-option"
    github.items["ISSUE-BUG-1"].fields["status-field"] = "backlog-option"

    router.route(RouteEvent(bug_item()))

    assert github.items["ISSUE-BUG-1"].fields == {
        "owner-field": "human-option",
        "status-field": "backlog-option",
    }
