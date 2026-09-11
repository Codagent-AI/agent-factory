"""GitHub App authentication and the narrow API surface used by routing."""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast

from agent_factory.config import ProjectConfig
from agent_factory.routing import ProjectItem, SourceItem


class GitHubApiError(RuntimeError):
    """A GitHub response did not have the expected contract."""


@dataclass(frozen=True)
class ProjectQueueItem:
    """One Project item in GitHub's delivered manual POSITION order."""

    id: str
    content_id: str
    fields: dict[str, str]
    source: SourceItem


@dataclass(frozen=True)
class IssueComment:
    id: str
    body: str
    author: str


class GhRunner(Protocol):
    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str: ...


class SubprocessGhRunner:
    """Runs gh without ever placing an authentication value in argv or request JSON."""

    def __init__(self, executable: str = "gh") -> None:
        self._executable = executable

    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str:
        child_environment = os.environ | environment
        completed = subprocess.run(
            [self._executable, *arguments],
            input=None if body is None else json.dumps(body),
            text=True,
            capture_output=True,
            check=False,
            env=child_environment,
        )
        if completed.returncode != 0:
            raise GitHubApiError("gh api request failed")
        return completed.stdout


@dataclass(frozen=True)
class AppCredentials:
    app_id: str
    installation_id: str
    private_key_path: Path


class InstallationTokenProvider:
    """Mints and caches a short-lived installation token entirely in process memory."""

    def __init__(self, credentials: AppCredentials, openssl: str = "openssl") -> None:
        self._credentials = credentials
        self._openssl = openssl
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def __call__(self) -> str:
        now = datetime.now(UTC)
        if (
            self._token is not None
            and self._expires_at is not None
            and now + timedelta(minutes=5) < self._expires_at
        ):
            return self._token
        jwt = self._signed_jwt(now)
        host = os.environ.get("GH_HOST") or "github.com"
        api_base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
        # gh's GH_TOKEN path sends `Authorization: token`, which GitHub rejects
        # for App JWTs. Keep the Bearer credential in memory, not process argv.
        request = urllib.request.Request(
            f"{api_base}/app/installations/{self._credentials.installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "agent-factory",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as result:
                response = result.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise GitHubApiError(f"installation token exchange failed: HTTP {error.code}") from None
        except (OSError, urllib.error.URLError) as error:
            raise GitHubApiError(
                f"installation token exchange failed: {_network_error_detail(error)}"
            ) from None
        payload = _json_object(response)
        token = _required_string(payload, "token")
        expires_at = _required_string(payload, "expires_at")
        try:
            parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise GitHubApiError("installation token expiry is invalid") from error
        self._token = token
        self._expires_at = parsed_expiry
        return token

    def _signed_jwt(self, now: datetime) -> str:
        header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _base64url(
            json.dumps(
                {
                    "iat": int(now.timestamp()) - 60,
                    "exp": int((now + timedelta(minutes=9)).timestamp()),
                    "iss": self._credentials.app_id,
                },
                separators=(",", ":"),
            ).encode()
        )
        signing_input = f"{header}.{payload}".encode()
        completed = subprocess.run(
            [self._openssl, "dgst", "-sha256", "-sign", str(self._credentials.private_key_path)],
            input=signing_input,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise GitHubApiError("unable to sign GitHub App JWT")
        return f"{header}.{payload}.{_base64url(completed.stdout)}"


def _network_error_detail(error: OSError | urllib.error.URLError) -> str:
    """Describe the underlying failure without reflecting remote text or credentials."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(cause, socket.gaierror):
        return "DNS resolution failed"
    if isinstance(cause, TimeoutError):
        return "connection timed out"
    if isinstance(cause, OSError) and cause.errno is not None:
        return f"{type(cause).__name__}: {os.strerror(cause.errno)}"
    return type(cause).__name__


class GitHubClient:
    """Concrete routing client using documented REST and ProjectV2 GraphQL shapes."""

    def __init__(self, runner: GhRunner, token: Callable[[], str]) -> None:
        self._runner = runner
        self._token = token

    def validate_project(self, project: ProjectConfig) -> None:
        """Validate configured field types and option membership without writing."""
        fields: dict[str, Mapping[str, object]] = {}
        cursor: str | None = None
        while True:
            data = self._graphql(
                "query Fields($project: ID!, $cursor: String) { node(id: $project) { "
                "... on ProjectV2 { fields(first: 100, after: $cursor) { nodes { "
                "... on ProjectV2Field { id dataType } "
                "... on ProjectV2SingleSelectField { id dataType options { id name } } "
                "} pageInfo { hasNextPage endCursor } } } } }",
                {"project": project.id, "cursor": cursor},
            )
            connection = _object(_object(data.get("node")).get("fields"))
            for raw in _list(connection.get("nodes")):
                field = _object(raw)
                fields[_required_string(field, "id")] = field
            page = _object(connection.get("pageInfo"))
            if page.get("hasNextPage") is not True:
                break
            cursor = _required_string(page, "endCursor")
        for configured in (project.status, project.owner, project.verdict):
            observed = fields.get(configured.id)
            if observed is None or observed.get("dataType") != "SINGLE_SELECT":
                raise GitHubApiError(
                    f"configured select field is missing or changed: {configured.id}"
                )
            options = {_required_string(_object(v), "id") for v in _list(observed.get("options"))}
            if not set(configured.options.values()) <= options:
                raise GitHubApiError(f"configured field has missing options: {configured.id}")
        refs = fields.get(project.refs.id)
        if refs is None or refs.get("dataType") != "TEXT":
            raise GitHubApiError(f"configured text field is missing or changed: {project.refs.id}")

    def get_permission(self, repository: str, login: str) -> str | None:
        try:
            response = self._request(
                ["api", f"repos/{repository}/collaborators/{login}/permission", "--method", "GET"],
                None,
            )
        except GitHubApiError:
            return None
        payload = _json_object(response)
        permission = payload.get("permission")
        return permission if isinstance(permission, str) else None

    def set_issue_type(self, repository: str, number: int, issue_type: str) -> None:
        self._request(
            ["api", f"repos/{repository}/issues/{number}", "--method", "PATCH", "--input", "-"],
            {"type": issue_type},
        )

    def get_source_item(self, repository: str, number: int) -> SourceItem:
        payload = _json_object(
            self._request(["api", f"repos/{repository}/issues/{number}", "--method", "GET"], None)
        )
        user = _object(payload.get("user"))
        labels = frozenset(
            name
            for value in _list(payload.get("labels"))
            if isinstance((name := _object(value).get("name")), str)
        )
        issue_type = payload.get("type", payload.get("issue_type"))
        type_name = (
            _object(cast(Mapping[str, object], issue_type)).get("name")
            if isinstance(issue_type, Mapping)
            else None
        )
        return SourceItem(
            id=_required_string(payload, "node_id"),
            repository=repository,
            number=number,
            author=_required_string(user, "login"),
            labels=labels,
            issue_type=type_name if isinstance(type_name, str) else None,
            state=_required_string(payload, "state"),
            body=_optional_string(payload, "body"),
        )

    def list_project_items(self, project_id: str) -> list[ProjectQueueItem]:
        """Read all Project cards in API order; callers apply eligibility afterwards."""
        cursor: str | None = None
        result: list[ProjectQueueItem] = []
        while True:
            payload = self._graphql(
                "".join(
                    (
                        "query Items($project: ID!, $cursor: String) { node(id: $project) { ",
                        "... on ProjectV2 { items(first: 100, after: $cursor, ",
                        "orderBy: {field: POSITION, direction: ASC}) { nodes { id ",
                        "content { __typename ... on Issue { id number body state ",
                        "author { login } ",
                        "repository { nameWithOwner } labels(first: 100) { nodes { name } } ",
                        "issueType { name } } } fieldValues(first: 50) { nodes { ... on ",
                        "ProjectV2ItemFieldSingleSelectValue ",
                        "{ field { ... on ProjectV2SingleSelectField { id } } optionId } } } } ",
                        "pageInfo { ",
                        "hasNextPage endCursor } } } } }",
                    )
                ),
                {"project": project_id, "cursor": cursor},
            )
            node = _object(payload.get("node"))
            items = _object(node.get("items"))
            for value in _list(items.get("nodes")):
                project_item = _object(value)
                raw_content = project_item.get("content")
                if not isinstance(raw_content, Mapping):
                    continue
                content = cast(Mapping[str, object], raw_content)
                if content.get("__typename") != "Issue":
                    continue
                try:
                    result.append(_queue_item(project_item, content))
                except GitHubApiError:
                    # A draft, pull request, or malformed card is not eligible work.
                    continue
            page_info = _object(items.get("pageInfo"))
            if page_info.get("hasNextPage") is not True:
                return result
            cursor = _required_string(page_info, "endCursor")

    def find_project_item(self, project_id: str, content_id: str) -> ProjectItem | None:
        cursor: str | None = None
        while True:
            payload = self._graphql(
                "".join(
                    (
                        "query Items($project: ID!, $cursor: String) { node(id: $project) { ",
                        "... on ProjectV2 { ",
                        "items(first: 100, after: $cursor, ",
                        "orderBy: {field: POSITION, direction: ASC}) { nodes { id content { ",
                        "... on Issue { id } ... on PullRequest { id } } ",
                        "fieldValues(first: 50) { nodes { ... on ",
                        "ProjectV2ItemFieldSingleSelectValue { field { ... on ",
                        "ProjectV2SingleSelectField { id } } optionId } } } } ",
                        "pageInfo { hasNextPage endCursor } } } } }",
                    )
                ),
                {"project": project_id, "cursor": cursor},
            )
            node = _object(payload.get("node"))
            items = _object(node.get("items"))
            nodes = _list(items.get("nodes"))
            for value in nodes:
                project_item = _object(value)
                content = _object(project_item.get("content"))
                if content.get("id") == content_id:
                    return ProjectItem(
                        _required_string(project_item, "id"),
                        content_id,
                        _single_select_fields(project_item),
                    )
            page_info = _object(items.get("pageInfo"))
            if page_info.get("hasNextPage") is not True:
                return None
            cursor = _required_string(page_info, "endCursor")

    def add_project_item(self, project_id: str, content_id: str) -> ProjectItem:
        payload = self._graphql(
            "".join(
                (
                    "mutation Add($project: ID!, $content: ID!) { addProjectV2ItemById(input: ",
                    "{projectId: $project, contentId: $content}) { item { id } } }",
                )
            ),
            {"project": project_id, "content": content_id},
        )
        added = _object(payload.get("addProjectV2ItemById"))
        item = _object(added.get("item"))
        return ProjectItem(_required_string(item, "id"), content_id, {})

    def set_single_select_field(
        self, project_id: str, item_id: str, field_id: str, option_id: str
    ) -> None:
        self._graphql(
            "".join(
                (
                    "mutation Set($project: ID!, $item: ID!, $field: ID!, $option: String!) { ",
                    "updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, ",
                    "fieldId: $field, value: {singleSelectOptionId: $option}}) { projectV2Item { ",
                    "id } } }",
                )
            ),
            {"project": project_id, "item": item_id, "field": field_id, "option": option_id},
        )

    def clear_field(self, project_id: str, item_id: str, field_id: str) -> None:
        """Remove a Project field value when it no longer describes the active claim."""
        self._graphql(
            "mutation Clear($project: ID!, $item: ID!, $field: ID!) { "
            "clearProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, "
            "fieldId: $field}) { projectV2Item { id } } }",
            {"project": project_id, "item": item_id, "field": field_id},
        )

    def set_text_field(self, project: str, item: str, field: str, value: str) -> None:
        self._graphql(
            "mutation Text($project: ID!, $item: ID!, $field: ID!, $text: String!) { "
            "updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, "
            "fieldId: $field, value: {text: $text}}) { projectV2Item { id } } }",
            {"project": project, "item": item, "field": field, "text": value},
        )

    def set_attention_label(self, repository: str, number: int, needed: bool) -> None:
        endpoint = f"repos/{repository}/issues/{number}/labels"
        if needed:
            self._request(
                ["api", endpoint, "--method", "POST", "--input", "-"], {"labels": ["needs-input"]}
            )
        else:
            # Read before deleting so an absent label is not a failed API request.
            labels = _json_list(self._request(["api", endpoint, "--method", "GET"], None))
            if any(_object(label).get("name") == "needs-input" for label in labels):
                self._request(["api", endpoint + "/needs-input", "--method", "DELETE"], None)

    def list_comments(self, repository: str, number: int) -> list[str]:
        return [comment.body for comment in self.list_comment_records(repository, number)]

    def list_comment_records(self, repository: str, number: int) -> list[IssueComment]:
        comments: list[IssueComment] = []
        page = 1
        while True:
            response = self._request(
                [
                    "api",
                    f"repos/{repository}/issues/{number}/comments?per_page=100&page={page}",
                    "--method",
                    "GET",
                ],
                None,
            )
            values = _json_list(response)
            for value in values:
                comment = _object(value)
                body = comment.get("body")
                user = _object(comment.get("user"))
                identifier = comment.get("id")
                login = user.get("login")
                if (
                    isinstance(body, str)
                    and isinstance(identifier, (int, str))
                    and isinstance(login, str)
                ):
                    comments.append(IssueComment(str(identifier), body, login))
            if len(values) < 100:
                return comments
            page += 1

    def create_comment(self, repository: str, number: int, body: str) -> str | None:
        response = self._request(
            [
                "api",
                f"repos/{repository}/issues/{number}/comments",
                "--method",
                "POST",
                "--input",
                "-",
            ],
            {"body": body},
        )
        payload = _json_object(response)
        comment_id = payload.get("id")
        return str(comment_id) if isinstance(comment_id, int | str) else None

    def _graphql(self, query: str, variables: dict[str, object]) -> Mapping[str, object]:
        response = self._request(
            ["api", "graphql", "--input", "-"], {"query": query, "variables": variables}
        )
        envelope = _json_object(response)
        if "errors" in envelope:
            raise GitHubApiError("GitHub GraphQL request failed")
        return _object(envelope.get("data"))

    def _request(self, arguments: list[str], body: dict[str, object] | None) -> str:
        return self._runner.run(arguments, body, {"GH_TOKEN": self._token()})


def _single_select_fields(item: Mapping[str, object]) -> dict[str, str]:
    field_values = _object(item.get("fieldValues"))
    parsed: dict[str, str] = {}
    for value in _list(field_values.get("nodes")):
        selection = _object(value)
        # Other union members (for example title/text fields) have no selected fields.
        if not selection:
            continue
        option_id = selection.get("optionId")
        field = _object(selection.get("field"))
        field_id = field.get("id")
        if isinstance(field_id, str) and isinstance(option_id, str):
            parsed[field_id] = option_id
    return parsed


def _queue_item(
    project_item: Mapping[str, object], content: Mapping[str, object]
) -> ProjectQueueItem:
    """Build an eligible Issue-shaped card; callers skip malformed cards."""
    # The Project connection may omit native Type. It belongs to issue data.
    issue_type = _object(content.get("issueType")).get("name")
    labels = frozenset(
        name
        for label in _list(_object(content.get("labels")).get("nodes"))
        if isinstance((name := _object(label).get("name")), str)
    )
    author = _object(content.get("author"))
    repository = _object(content.get("repository"))
    return ProjectQueueItem(
        id=_required_string(project_item, "id"),
        content_id=_required_string(content, "id"),
        fields=_single_select_fields(project_item),
        source=SourceItem(
            id=_required_string(content, "id"),
            repository=_required_string(repository, "nameWithOwner"),
            number=_required_int(content, "number"),
            author=_required_string(author, "login"),
            labels=labels,
            issue_type=issue_type if isinstance(issue_type, str) else None,
            state=_required_string(content, "state"),
            body=_optional_string(content, "body"),
        ),
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _object(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise GitHubApiError("GitHub response has an unexpected shape")
    return cast(Mapping[str, object], raw)


def _json_object(response: str) -> Mapping[str, object]:
    try:
        return _object(json.loads(response))
    except json.JSONDecodeError as error:
        raise GitHubApiError("GitHub response is not JSON") from error


def _json_list(response: str) -> list[object]:
    try:
        return _list(json.loads(response))
    except json.JSONDecodeError as error:
        raise GitHubApiError("GitHub response is not JSON") from error


def _list(raw: object) -> list[object]:
    if not isinstance(raw, list):
        raise GitHubApiError("GitHub response has an unexpected shape")
    return cast(list[object], raw)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubApiError(f"GitHub response does not include {key}")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GitHubApiError(f"GitHub response does not include {key}")
    return value


def _optional_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""
