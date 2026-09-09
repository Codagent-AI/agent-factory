from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from graphql import parse

from agent_factory.github import GitHubClient


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
                                "nodes": [{"field": {"id": "status"}, "optionId": "ready"}]
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
