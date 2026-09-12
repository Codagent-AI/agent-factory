"""Idempotent GitHub Project routing; it deliberately has no local queue dependency."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import SharedConfig

_RECEIPT_PREFIX = "<!-- agent-factory-routing:v1 "


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
    def get_permission(self, repository: str, login: str) -> str | None: ...

    def set_issue_type(self, repository: str, number: int, issue_type: str) -> None: ...

    def find_project_item(self, project_id: str, content_id: str) -> ProjectItem | None: ...

    def add_project_item(self, project_id: str, content_id: str) -> ProjectItem: ...

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None: ...

    def list_comments(self, repository: str, number: int) -> list[str]: ...

    def create_comment(self, repository: str, number: int, body: str) -> str | None: ...


class Router:
    def __init__(self, config: SharedConfig, github: GitHubRoutingClient) -> None:
        self._config = config
        self._github = github

    def route(self, event: RouteEvent) -> RouteResult:
        source = event.item
        if source.repository not in self._config.routing.general_sources:
            raise ValueError(f"source repository is not configured: {source.repository}")
        project_item = self._github.find_project_item(self._config.project.id, source.id)
        if project_item is None:
            project_item = self._github.add_project_item(self._config.project.id, source.id)

        if source.state.lower() == "closed":
            self._set_status_if_changed(project_item, self._config.project.status.id, "done")
            return RouteResult("done", project_item.id)

        if self._is_eval(source):
            permission = self._github.get_permission(source.repository, source.author)
            if permission in {"write", "maintain", "admin"}:
                self._set_eval_type_if_needed(source)
                self._initialize(project_item, source, (("owner", "factory"), ("status", "ready")))
                return RouteResult("ready", project_item.id)
        elif self._is_bug(source):
            if self._config.routing.hold_label in source.labels:
                self._initialize(project_item, source, (("owner", "human"), ("status", "backlog")))
                return RouteResult("backlog", project_item.id)
            permission = self._github.get_permission(source.repository, source.author)
            if permission in {"write", "maintain", "admin"}:
                self._initialize(project_item, source, (("owner", "factory"), ("status", "ready")))
                return RouteResult("ready", project_item.id)

        self._initialize(project_item, source, (("status", "backlog"),))
        return RouteResult("backlog", project_item.id)

    def _is_eval(self, source: SourceItem) -> bool:
        return (
            source.repository == self._config.routing.eval_source
            and self._config.routing.eval_label in source.labels
        )

    def _is_bug(self, source: SourceItem) -> bool:
        return (
            not source.pull_request
            and source.repository in self._config.routing.general_sources
            and source.issue_type == self._config.routing.bug_type
        )

    def _set_eval_type_if_needed(self, source: SourceItem) -> None:
        if source.issue_type != self._config.routing.eval_type:
            self._github.set_issue_type(
                source.repository, source.number, self._config.routing.eval_type
            )

    def _initialize(
        self, item: ProjectItem, source: SourceItem, values: tuple[tuple[str, str], ...]
    ) -> None:
        receipt = self._receipt(source)
        desired: dict[str, str] = {}
        for field_name, logical_option in values:
            field, option = self._field_and_option(field_name, logical_option)
            desired[field.id] = option
        receipt_values_raw = receipt.get("values") if receipt is not None else None
        receipt_values = (
            cast(dict[str, object], receipt_values_raw)
            if isinstance(receipt_values_raw, dict)
            else None
        )
        completed = bool(receipt and receipt.get("complete"))
        if (
            completed
            and receipt_values is not None
            and all(receipt_values.get(field_id) == option for field_id, option in desired.items())
        ):
            return
        for field_id, option in desired.items():
            current = item.fields.get(field_id)
            prior = receipt_values.get(field_id) if receipt_values is not None else None
            # Only the value previously recorded by factory is safe to replace on recovery.
            if current is None or current == prior:
                self._github.set_single_select_field(
                    self._config.project.id, item.id, field_id, option
                )
                item.fields[field_id] = option
        self._write_receipt(source, item, desired)

    def _set_status_if_changed(self, item: ProjectItem, field_id: str, logical_option: str) -> None:
        option = self._config.project.status.option(logical_option)
        if item.fields.get(field_id) != option:
            self._github.set_single_select_field(self._config.project.id, item.id, field_id, option)
            item.fields[field_id] = option

    def _field_and_option(self, field_name: str, option_name: str):
        if field_name == "status":
            return self._config.project.status, self._config.project.status.option(option_name)
        if field_name == "owner":
            return self._config.project.owner, self._config.project.owner.option(option_name)
        raise ValueError(f"unsupported routing field: {field_name}")

    def _receipt(self, source: SourceItem) -> dict[str, object] | None:
        for body in reversed(self._github.list_comments(source.repository, source.number)):
            if body.startswith(_RECEIPT_PREFIX) and body.endswith(" -->"):
                try:
                    parsed = json.loads(body[len(_RECEIPT_PREFIX) : -4])
                    if isinstance(parsed, dict):
                        return cast(dict[str, object], parsed)
                except json.JSONDecodeError:
                    continue
        return None

    def _write_receipt(
        self, source: SourceItem, item: ProjectItem, initialized: dict[str, str]
    ) -> None:
        payload = {
            "project": self._config.project.id,
            "item": item.id,
            "values": initialized,
            "complete": True,
        }
        self._github.create_comment(
            source.repository,
            source.number,
            f"{_RECEIPT_PREFIX}{json.dumps(payload, sort_keys=True, separators=(',', ':'))} -->",
        )


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
