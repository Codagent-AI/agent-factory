from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from graphql import parse

from agent_factory.github import (
    GitHubApiError,
    GitHubClient,
    GitHubNotFoundError,
    _single_select_fields,  # pyright: ignore[reportPrivateUsage]
)


@dataclass
class Call:
    arguments: list[str]
    body: dict[str, object] | None
    environment: dict[str, str]


def _calls() -> list[Call]:
    return []


@dataclass
class RecordingGh:
    responses: list[str]
    calls: list[Call] = field(default_factory=_calls)

    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str:
        self.calls.append(Call(arguments, body, environment))
        return self.responses.pop(0)


def test_client_uses_app_token_only_in_child_environment_and_structured_graphql() -> None:
    gh = RecordingGh([json.dumps({"data": {"addProjectV2ItemById": {"item": {"id": "ITEM"}}}})])
    client = GitHubClient(gh, lambda: "installation-token")

    project_item = client.add_project_item("PROJECT", "ISSUE")

    call = gh.calls[0]
    assert project_item.id == "ITEM"
    assert call.arguments == ["api", "graphql", "--input", "-"]
    assert call.environment == {"GH_TOKEN": "installation-token"}
    assert call.body == {
        "query": (
            "mutation Add($project: ID!, $content: ID!) { addProjectV2ItemById(input: "
            "{projectId: $project, contentId: $content}) { item { id } } }"
        ),
        "variables": {"project": "PROJECT", "content": "ISSUE"},
    }
    assert "installation-token" not in json.dumps(call.body)


def test_client_interprets_missing_collaborator_permission_as_untrusted() -> None:
    gh = RecordingGh([json.dumps({"message": "Not Found"})])
    client = GitHubClient(gh, lambda: "installation-token")

    permission = client.get_permission("example/repository", "outside-contributor")

    assert permission is None
    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/collaborators/outside-contributor/permission",
        "--method",
        "GET",
    ]


def test_client_assigns_native_issue_type_with_the_repository_api() -> None:
    gh = RecordingGh([json.dumps({"type": {"name": "Eval"}})])
    client = GitHubClient(gh, lambda: "installation-token")

    client.set_issue_type("example/evals", 42, "Eval")

    assert gh.calls[0].arguments == [
        "api",
        "repos/example/evals/issues/42",
        "--method",
        "PATCH",
        "--input",
        "-",
    ]
    assert gh.calls[0].body == {"type": "Eval"}
    assert gh.calls[0].environment == {"GH_TOKEN": "installation-token"}


def test_client_reads_manual_project_order_and_native_issue_type_from_issue_data() -> None:
    response: dict[str, object] = {
        "data": {
            "node": {
                "items": {
                    "nodes": [
                        {
                            "id": "PR",
                            "content": {"__typename": "PullRequest"},
                            "fieldValues": {"nodes": []},
                        },
                        {
                            "id": "P2",
                            "content": {
                                "__typename": "Issue",
                                "id": "I2",
                                "number": 2,
                                "body": "```eval\nrepetitions = 1\n```",
                                "state": "OPEN",
                                "author": {"login": "writer"},
                                "repository": {"nameWithOwner": "example/evals"},
                                "labels": {"nodes": [{"name": "run-eval"}]},
                                "issueType": {"name": "Eval"},
                            },
                            "fieldValues": {
                                "nodes": [{}, {"field": {"id": "status"}, "optionId": "ready"}]
                            },
                        },
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }
    gh = RecordingGh([json.dumps(response)])

    items = GitHubClient(gh, lambda: "installation-token").list_project_items("PROJECT")

    assert [item.id for item in items] == ["P2"]
    assert items[0].source.issue_type == "Eval"
    assert items[0].fields == {"status": "ready"}
    assert "issueType" in gh.calls[0].body["query"]  # type: ignore[index]


@pytest.mark.parametrize("lookup", [False, True], ids=["queue", "routing-lookup"])
def test_project_item_query_is_valid_graphql(lookup: bool) -> None:
    gh = RecordingGh(['{"data":{"node":{"items":{"nodes":[],"pageInfo":{"hasNextPage":false}}}}}'])
    client = GitHubClient(gh, lambda: "installation-token")
    if lookup:
        client.find_project_item("PROJECT", "ISSUE")
    else:
        client.list_project_items("PROJECT")
    assert gh.calls[0].body is not None
    query = gh.calls[0].body["query"]
    assert isinstance(query, str)
    parse(query)


def test_select_field_parser_ignores_unselected_graphql_union_members() -> None:
    fields = _single_select_fields(
        {"fieldValues": {"nodes": [{}, {"field": {"id": "status"}, "optionId": "ready"}]}}
    )

    assert fields == {"status": "ready"}


def test_routing_lookup_reads_existing_card_with_other_field_types() -> None:
    response = {
        "data": {
            "node": {
                "items": {
                    "nodes": [
                        {
                            "id": "ITEM",
                            "content": {"id": "ISSUE"},
                            "fieldValues": {
                                "nodes": [{}, {"field": {"id": "status"}, "optionId": "ready"}]
                            },
                        }
                    ],
                    "pageInfo": {"hasNextPage": False},
                }
            }
        }
    }
    gh = RecordingGh([json.dumps(response)])

    item = GitHubClient(gh, lambda: "installation-token").find_project_item("PROJECT", "ISSUE")

    assert item is not None
    assert item.id == "ITEM"
    assert item.fields == {"status": "ready"}


@pytest.mark.parametrize("response", ["", "not JSON", "{}"])
@pytest.mark.parametrize("endpoint", ["labels", "comments"])
def test_list_endpoints_report_invalid_responses_as_api_errors(
    response: str, endpoint: str
) -> None:
    from agent_factory.github import GitHubApiError

    client = GitHubClient(RecordingGh([response]), lambda: "installation-token")
    with pytest.raises(GitHubApiError):
        if endpoint == "labels":
            client.set_attention_label("example/evals", 42, False)
        else:
            client.list_comment_records("example/evals", 42)


@dataclass
class RaisingGh:
    error: Exception

    def run(
        self, arguments: list[str], body: dict[str, object] | None, environment: dict[str, str]
    ) -> str:
        raise self.error


def test_get_branch_returns_info_when_branch_exists() -> None:
    gh = RecordingGh([json.dumps({"name": "main", "commit": {"sha": "abc123"}})])
    client = GitHubClient(gh, lambda: "installation-token")

    branch = client.get_branch("example/repository", "main")

    assert branch is not None
    assert branch.name == "main"
    assert branch.sha == "abc123"
    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/branches/main",
        "--method",
        "GET",
    ]


def test_get_branch_url_encodes_branch_names_containing_slashes() -> None:
    gh = RecordingGh(
        [json.dumps({"name": "factory/fix-212-1a2b3c4d", "commit": {"sha": "deadbeef"}})]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    branch = client.get_branch("example/repository", "factory/fix-212-1a2b3c4d")

    assert branch is not None
    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/branches/factory%2Ffix-212-1a2b3c4d",
        "--method",
        "GET",
    ]


def test_get_branch_returns_none_when_branch_missing() -> None:
    client = GitHubClient(RaisingGh(GitHubNotFoundError("not found")), lambda: "installation-token")

    assert client.get_branch("example/repository", "factory/fix-1-abcdef12") is None


def test_get_branch_raises_on_lookup_failure() -> None:
    client = GitHubClient(RaisingGh(GitHubApiError("network unreachable")), lambda: "token")

    with pytest.raises(GitHubApiError):
        client.get_branch("example/repository", "main")


def test_list_open_pull_requests_for_head_returns_matches() -> None:
    response = [
        {
            "html_url": "https://github.com/example/repository/pull/214",
            "number": 214,
            "head": {"sha": "deadbeef"},
        }
    ]
    gh = RecordingGh([json.dumps(response)])
    client = GitHubClient(gh, lambda: "installation-token")

    pulls = client.list_open_pull_requests_for_head(
        "example/repository", "factory/fix-212-1a2b3c4d"
    )

    assert len(pulls) == 1
    assert pulls[0].url == "https://github.com/example/repository/pull/214"
    assert pulls[0].number == 214
    assert pulls[0].head_sha == "deadbeef"
    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/pulls?head=example%3Afactory%2Ffix-212-1a2b3c4d&state=open",
        "--method",
        "GET",
    ]


def test_list_open_pull_requests_for_head_raises_on_lookup_failure() -> None:
    client = GitHubClient(RaisingGh(GitHubApiError("network unreachable")), lambda: "token")

    with pytest.raises(GitHubApiError):
        client.list_open_pull_requests_for_head("example/repository", "factory/fix-212-1a2b3c4d")


def test_get_pull_request_reads_state_and_merged_at() -> None:
    gh = RecordingGh([json.dumps({"state": "MERGED", "mergedAt": "2026-01-01T00:00:00Z"})])
    client = GitHubClient(gh, lambda: "installation-token")

    state = client.get_pull_request("example/repository", 214)

    assert state.state == "MERGED"
    assert state.merged_at == "2026-01-01T00:00:00Z"
    assert gh.calls[0].arguments == [
        "pr",
        "view",
        "214",
        "--repo",
        "example/repository",
        "--json",
        "state,mergedAt",
    ]


def test_get_pull_request_reports_missing_merged_at_as_none() -> None:
    gh = RecordingGh([json.dumps({"state": "OPEN", "mergedAt": None})])
    client = GitHubClient(gh, lambda: "installation-token")

    state = client.get_pull_request("example/repository", 214)

    assert state.state == "OPEN"
    assert state.merged_at is None


def test_close_issue_patches_state_closed() -> None:
    gh = RecordingGh([json.dumps({"number": 42, "state": "closed"})])
    client = GitHubClient(gh, lambda: "installation-token")

    client.close_issue("example/repository", 42)

    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/issues/42",
        "--method",
        "PATCH",
        "--input",
        "-",
    ]
    assert gh.calls[0].body == {"state": "closed"}
