"""Idempotent GitHub Project routing; it deliberately has no local queue dependency."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import SharedConfig


@dataclass(frozen=True)
class SourceItem:
    id: str
    repository: str
    number: int
    author: str
    labels: frozenset[str]
    issue_type: str | None
    state: str
    body: str = ""
    pull_request: bool = False
    title: str = ""
    created_at: str = ""


@dataclass
class ProjectItem:
    id: str
    content_id: str
    fields: dict[str, str]


@dataclass(frozen=True)
class RouteEvent:
    item: SourceItem


@dataclass(frozen=True)
class RouteResult:
    destination: str
    project_item_id: str


class GitHubRoutingClient(Protocol):
    def find_project_item(self, project_id: str, content_id: str) -> ProjectItem | None: ...

    def add_project_item(self, project_id: str, content_id: str) -> ProjectItem: ...

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None: ...

    def ensure_issue_select_default(self, issue_id: str, field_id: str, option_id: str) -> None: ...


class Router:
    def __init__(self, config: SharedConfig, github: GitHubRoutingClient) -> None:
        self._config = config
        self._github = github

    def route(self, event: RouteEvent) -> RouteResult:
        source = event.item
        if source.repository not in self._config.routing.general_sources:
            raise ValueError(f"source repository is not configured: {source.repository}")
        self._ensure_default_priority(source)
        project_item = self._github.find_project_item(self._config.project.id, source.id)
        if project_item is None:
            project_item = self._github.add_project_item(self._config.project.id, source.id)

        if source.state.lower() == "closed":
            self._set_status_if_changed(project_item, self._config.project.status.id, "done")
            return RouteResult("done", project_item.id)

        # Status stays unset: routing writes nothing that could overwrite a Ready that a
        # handoff set while this run was in flight.
        return RouteResult("unset", project_item.id)

    def _ensure_default_priority(self, source: SourceItem) -> None:
        if source.pull_request:
            return
        field_id = self._config.project.priority_issue_field_id
        option_id = self._config.project.priority_default_option_id
        if not field_id or not option_id:
            return
        self._github.ensure_issue_select_default(source.id, field_id, option_id)

    def _set_status_if_changed(self, item: ProjectItem, field_id: str, logical_option: str) -> None:
        option = self._config.project.status.option(logical_option)
        if item.fields.get(field_id) != option:
            self._github.set_single_select_field(self._config.project.id, item.id, field_id, option)
            item.fields[field_id] = option


def main() -> None:
    """Route the current default-branch Actions event using its App installation token."""
    parser = argparse.ArgumentParser(description="Route one GitHub issue or pull request")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--event", type=Path, required=True)
    arguments = parser.parse_args()
    payload = json.loads(arguments.event.read_text(encoding="utf-8"))
    repository = _event_repository(payload)
    number = payload.get("number")
    if number is None:
        issue = payload.get("issue")
        if isinstance(issue, dict):
            number = cast(dict[str, object], issue).get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("GitHub event does not identify an issue or pull request number")
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN must contain the short-lived GitHub App installation token")
    from agent_factory.github import GitHubClient, SubprocessGhRunner

    config = SharedConfig.from_file(arguments.config)
    github = GitHubClient(SubprocessGhRunner(), lambda: token)
    Router(config, github).route(RouteEvent(github.get_source_item(repository, number)))


def _event_repository(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("GitHub event must be an object")
    event = cast(dict[str, object], payload)
    repository = event.get("repository")
    if not isinstance(repository, dict):
        raise ValueError("GitHub event does not identify a repository")
    full_name = cast(dict[str, object], repository).get("full_name")
    if not isinstance(full_name, str) or not full_name:
        raise ValueError("GitHub event does not identify a repository")
    return full_name


if __name__ == "__main__":
    main()
