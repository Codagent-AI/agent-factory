from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_factory.config import ConfigurationError, SharedConfig
from agent_factory.routing import ProjectItem, RouteEvent, Router, SourceItem


def _items() -> dict[str, ProjectItem]:
    return {}


def _permissions() -> dict[tuple[str, str], str | None]:
    return {}


def _comments() -> dict[str, list[str]]:
    return {}


def config_text(*, harness_sha: str = "a" * 40) -> str:
    return f'''\
[github]
organization = "Example Org"
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
harness_sha = "{harness_sha}"
suite = "and-scene"
repetitions = 3
'''


@dataclass
class MemoryGitHub:
    items: dict[str, ProjectItem] = field(default_factory=_items)
    permissions: dict[tuple[str, str], str | None] = field(default_factory=_permissions)
    comments: dict[str, list[str]] = field(default_factory=_comments)
    added: int = 0

    def get_permission(self, repository: str, login: str) -> str | None:
        return self.permissions.get((repository, login))

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


def item(*, labels: set[str] | None = None, state: str = "open") -> SourceItem:
    return SourceItem(
        id="ISSUE-1",
        repository="example/evals",
        number=42,
        author="writer",
        labels=frozenset(labels or {"run-eval"}),
        issue_type="Eval",
        state=state,
    )


def test_authorized_eval_precedes_general_intake_and_initializes_owner_before_ready() -> None:
    config = SharedConfig.from_toml(config_text())
    github = MemoryGitHub(permissions={("example/evals", "writer"): "write"})

    result = Router(config, github).route(RouteEvent(item()))

    project_item = github.items["ISSUE-1"]
    assert result.destination == "ready"
    assert project_item.fields == {"owner-field": "factory-option", "status-field": "ready-option"}
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


def test_shared_config_rejects_mutable_harness_revision() -> None:
    with pytest.raises(ConfigurationError, match="full 40-character commit SHA"):
        SharedConfig.from_toml(config_text(harness_sha="main"))


def test_shared_config_exposes_configured_reporting_field_mappings() -> None:
    config = SharedConfig.from_toml(config_text())

    assert config.project.refs.id == "refs-field"
    assert config.project.verdict.option("infra-error") == "infra-option"
