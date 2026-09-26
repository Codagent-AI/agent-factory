from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from graphql import parse

from agent_factory.github import (
    GitHubApiError,
    GitHubClient,
    GitHubNotFoundError,
    ProjectQueueItem,
    _single_select_fields,  # pyright: ignore[reportPrivateUsage]
    rank_project_queue,
)
from agent_factory.routing import SourceItem


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


def test_client_reads_the_collaborator_role_name() -> None:
    gh = RecordingGh([json.dumps({"permission": "write", "role_name": "maintain"})])
    client = GitHubClient(gh, lambda: "installation-token")

    role = client.get_role("example/repository", "maintainer")

    assert role == "maintain"
    assert gh.calls[0].arguments == [
        "api",
        "repos/example/repository/collaborators/maintainer/permission",
        "--method",
        "GET",
    ]


def test_client_interprets_missing_collaborator_role_as_untrusted() -> None:
    gh = RecordingGh([json.dumps({"message": "Not Found"})])
    client = GitHubClient(gh, lambda: "installation-token")

    assert client.get_role("example/repository", "outside-contributor") is None


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
    assert "createdAt" in gh.calls[0].body["query"]  # type: ignore[index]
    assert "issueFieldValues" in gh.calls[0].body["query"]  # type: ignore[index]


def _queued(
    item_id: str,
    *,
    priority: str | None = None,
    created_at: str = "",
) -> ProjectQueueItem:
    return ProjectQueueItem(
        item_id,
        f"content-{item_id}",
        {},
        SourceItem(
            f"content-{item_id}",
            "example/evals",
            1,
            "writer",
            frozenset(),
            "Eval",
            "OPEN",
            created_at=created_at,
        ),
        priority=priority,
    )


def test_queue_ranks_priority_then_newest_created() -> None:
    items = [
        _queued("old-high", priority="High", created_at="2026-01-01T00:00:00Z"),
        _queued("new-low", priority="Low", created_at="2026-09-21T00:00:00Z"),
        _queued("new-high", priority="High", created_at="2026-09-20T00:00:00Z"),
        _queued("urgent", priority="Urgent", created_at="2026-02-01T00:00:00Z"),
        _queued("unset-new", created_at="2026-09-22T00:00:00Z"),
        _queued("medium", priority="Medium", created_at="2026-03-01T00:00:00Z"),
    ]

    ranked = rank_project_queue(items)

    assert [item.id for item in ranked] == [
        "urgent",
        "new-high",
        "old-high",
        "medium",
        "new-low",
        "unset-new",
    ]


def test_client_reads_project_card_priority_ahead_of_issue_field() -> None:
    response: dict[str, object] = {
        "data": {
            "node": {
                "items": {
                    "nodes": [
                        {
                            "id": "P-issue-high",
                            "content": {
                                "__typename": "Issue",
                                "id": "I-issue-high",
                                "number": 1,
                                "body": "issue says High",
                                "state": "OPEN",
                                "createdAt": "2026-09-22T00:00:00Z",
                                "author": {"login": "writer"},
                                "repository": {"nameWithOwner": "example/evals"},
                                "labels": {"nodes": []},
                                "issueType": {"name": "Eval"},
                                "issueFieldValues": {
                                    "nodes": [{"name": "High", "field": {"name": "Priority"}}]
                                },
                            },
                            "fieldValues": {
                                "nodes": [
                                    {
                                        "name": "Low",
                                        "optionId": "low",
                                        "field": {"id": "priority-field", "name": "Priority"},
                                    }
                                ]
                            },
                        },
                        {
                            "id": "P-card-urgent",
                            "content": {
                                "__typename": "Issue",
                                "id": "I-card-urgent",
                                "number": 2,
                                "body": "card says Urgent",
                                "state": "OPEN",
                                "createdAt": "2026-01-01T00:00:00Z",
                                "author": {"login": "writer"},
                                "repository": {"nameWithOwner": "example/evals"},
                                "labels": {"nodes": []},
                                "issueType": {"name": "Eval"},
                                "issueFieldValues": {"nodes": []},
                            },
                            "fieldValues": {
                                "nodes": [
                                    {
                                        "name": "Urgent",
                                        "optionId": "urgent",
                                        "field": {"id": "priority-field", "name": "Priority"},
                                    }
                                ]
                            },
                        },
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }
    gh = RecordingGh([json.dumps(response)])

    items = GitHubClient(gh, lambda: "installation-token").list_project_items(
        "PROJECT", priority_id="priority-field"
    )

    assert [item.id for item in items] == ["P-card-urgent", "P-issue-high"]
    assert items[0].priority == "Urgent"
    assert items[1].priority == "Low"


def test_client_reads_issue_priority_and_reorders_away_from_position() -> None:
    response: dict[str, object] = {
        "data": {
            "node": {
                "items": {
                    "nodes": [
                        {
                            "id": "P-low",
                            "content": {
                                "__typename": "Issue",
                                "id": "I-low",
                                "number": 1,
                                "body": "later",
                                "state": "OPEN",
                                "createdAt": "2026-09-22T00:00:00Z",
                                "author": {"login": "writer"},
                                "repository": {"nameWithOwner": "example/evals"},
                                "labels": {"nodes": []},
                                "issueType": {"name": "Eval"},
                                "issueFieldValues": {
                                    "nodes": [
                                        {
                                            "name": "Low",
                                            "field": {"name": "Priority"},
                                        }
                                    ]
                                },
                            },
                            "fieldValues": {"nodes": []},
                        },
                        {
                            "id": "P-high",
                            "content": {
                                "__typename": "Issue",
                                "id": "I-high",
                                "number": 2,
                                "body": "earlier",
                                "state": "OPEN",
                                "createdAt": "2026-01-01T00:00:00Z",
                                "author": {"login": "writer"},
                                "repository": {"nameWithOwner": "example/evals"},
                                "labels": {"nodes": []},
                                "issueType": {"name": "Eval"},
                                "issueFieldValues": {
                                    "nodes": [
                                        {
                                            "name": "High",
                                            "field": {"name": "Priority"},
                                        }
                                    ]
                                },
                            },
                            "fieldValues": {"nodes": []},
                        },
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
    }
    gh = RecordingGh([json.dumps(response)])

    items = GitHubClient(gh, lambda: "installation-token").list_project_items("PROJECT")

    assert [item.id for item in items] == ["P-high", "P-low"]
    assert items[0].priority == "High"
    assert items[1].priority == "Low"


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


def test_client_sets_issue_select_default_only_when_empty() -> None:
    gh = RecordingGh(
        [
            json.dumps(
                {
                    "data": {
                        "node": {
                            "issueFieldValues": {
                                "nodes": [{}],
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                            }
                        }
                    }
                }
            ),
            json.dumps({"data": {"updateIssueFieldValue": {"issue": {"id": "ISSUE"}}}}),
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    client.ensure_issue_select_default("ISSUE", "priority-field", "low-option")

    assert gh.calls[0].body is not None
    parse(str(gh.calls[0].body["query"]))
    assert gh.calls[1].body is not None
    parse(str(gh.calls[1].body["query"]))
    assert gh.calls[1].body["variables"] == {
        "issue": "ISSUE",
        "field": "priority-field",
        "option": "low-option",
    }


def test_client_leaves_an_existing_issue_select_value() -> None:
    gh = RecordingGh(
        [
            json.dumps(
                {
                    "data": {
                        "node": {
                            "issueFieldValues": {
                                "nodes": [{"field": {"id": "priority-field"}}],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            )
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    client.ensure_issue_select_default("ISSUE", "priority-field", "low-option")

    assert len(gh.calls) == 1


def test_client_finds_issue_select_value_on_a_later_page() -> None:
    gh = RecordingGh(
        [
            json.dumps(
                {
                    "data": {
                        "node": {
                            "issueFieldValues": {
                                "nodes": [{"field": {"id": "effort-field"}}],
                                "pageInfo": {"hasNextPage": True, "endCursor": "page-1"},
                            }
                        }
                    }
                }
            ),
            json.dumps(
                {
                    "data": {
                        "node": {
                            "issueFieldValues": {
                                "nodes": [{"field": {"id": "priority-field"}}],
                                "pageInfo": {"hasNextPage": False},
                            }
                        }
                    }
                }
            ),
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    client.ensure_issue_select_default("ISSUE", "priority-field", "low-option")

    assert len(gh.calls) == 2
    assert gh.calls[1].body is not None
    assert gh.calls[1].body["variables"] == {"id": "ISSUE", "cursor": "page-1"}


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


def test_factory_pr_lookup_scans_all_pages_and_filters_by_issue() -> None:
    unrelated = {
        "html_url": "https://example.test/pr/1",
        "number": 1,
        "head": {"ref": "factory/feature-old", "sha": "a" * 40},
        "body": "Refs #41",
        "draft": False,
    }
    match = {
        "html_url": "https://example.test/pr/2",
        "number": 2,
        "head": {"ref": "factory/feature-new", "sha": "b" * 40},
        "body": "Refs #42",
        "draft": False,
    }
    gh = RecordingGh([json.dumps([unrelated] * 100), json.dumps([match])])
    client = GitHubClient(gh, lambda: "installation-token")

    pulls = client.list_open_factory_pull_requests_for_issue("example/repository", 42)

    assert [pull.number for pull in pulls] == [2]
    assert pulls[0].branch == "factory/feature-new"
    assert "page=2" in gh.calls[1].arguments[1]


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


def _review_node(identifier: str) -> dict[str, object]:
    return {
        "id": identifier,
        "body": f"body {identifier}",
        "submittedAt": "2026-09-18T10:00:00Z",
        "createdAt": "2026-09-18T10:00:00Z",
        "author": {"login": "writer"},
    }


def _connection(nodes: list[dict[str, object]], cursor: str | None = None) -> dict[str, object]:
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def test_list_review_activity_follows_every_connection_past_the_first_page() -> None:
    def thread(identifier: str, comments: dict[str, object]) -> dict[str, object]:
        return {
            "id": identifier,
            "isResolved": False,
            "path": "a.py",
            "line": 3,
            "comments": comments,
        }

    def page(reviews: dict[str, object], threads: dict[str, object]) -> str:
        pull = {"reviews": reviews, "reviewThreads": threads}
        return json.dumps({"data": {"repository": {"pullRequest": pull}}})

    gh = RecordingGh(
        [
            page(
                _connection([_review_node("R1")], "reviews-1"),
                _connection([thread("T1", _connection([_review_node("C1")], "c-1"))], "threads-1"),
            ),
            json.dumps({"data": {"node": {"comments": _connection([_review_node("C2")])}}}),
            page(
                _connection([_review_node("R2")]),
                _connection([thread("T2", _connection([_review_node("C3")]))]),
            ),
            json.dumps([]),
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    activity = client.list_review_activity("example/repository", 7)

    assert [r.id for r in activity.reviews] == ["R1", "R2"]
    assert [t.id for t in activity.threads] == ["T1", "T2"]
    assert [c.id for c in activity.threads[0].comments] == ["C1", "C2"]
    assert [c.id for c in activity.threads[1].comments] == ["C3"]
    bodies = [call.body for call in gh.calls[:3]]
    for body in bodies:
        assert body is not None
        parse(str(body["query"]))
    assert bodies[1] is not None and bodies[1]["variables"] == {"thread": "T1", "cursor": "c-1"}
    assert bodies[2] is not None and bodies[2]["variables"] == {
        "owner": "example",
        "name": "repository",
        "number": 7,
        "reviews": "reviews-1",
        "threads": "threads-1",
    }


def test_commit_files_adds_one_commit_on_the_branch_through_the_git_data_api() -> None:
    import base64

    gh = RecordingGh(
        [
            json.dumps({"object": {"sha": "base-commit"}}),
            json.dumps({"tree": {"sha": "base-tree"}}),
            json.dumps({"sha": "blob-a"}),
            json.dumps({"sha": "blob-b"}),
            json.dumps({"sha": "new-tree"}),
            json.dumps({"sha": "new-commit"}),
            json.dumps({"object": {"sha": "new-commit"}}),
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    commit = client.commit_files(
        "org/evals",
        "main",
        {"results/run-1/a.json": b'{"a": 1}', "results/run-1/b.html": b"<p>"},
        "chore: record and-scene eval run-1",
    )

    assert commit == "new-commit"
    routes = [(c.arguments[1], c.arguments[c.arguments.index("--method") + 1]) for c in gh.calls]
    assert routes == [
        ("repos/org/evals/git/ref/heads/main", "GET"),
        ("repos/org/evals/git/commits/base-commit", "GET"),
        ("repos/org/evals/git/blobs", "POST"),
        ("repos/org/evals/git/blobs", "POST"),
        ("repos/org/evals/git/trees", "POST"),
        ("repos/org/evals/git/commits", "POST"),
        ("repos/org/evals/git/refs/heads/main", "PATCH"),
    ]
    assert gh.calls[2].body == {
        "content": base64.b64encode(b'{"a": 1}').decode(),
        "encoding": "base64",
    }
    assert gh.calls[4].body == {
        "base_tree": "base-tree",
        "tree": [
            {"path": "results/run-1/a.json", "mode": "100644", "type": "blob", "sha": "blob-a"},
            {"path": "results/run-1/b.html", "mode": "100644", "type": "blob", "sha": "blob-b"},
        ],
    }
    assert gh.calls[5].body == {
        "message": "chore: record and-scene eval run-1",
        "tree": "new-tree",
        "parents": ["base-commit"],
    }
    # An ordinary fast-forward: a concurrent push is never overwritten.
    assert gh.calls[6].body == {"sha": "new-commit", "force": False}


def test_commit_files_skips_the_commit_when_the_tree_is_unchanged() -> None:
    gh = RecordingGh(
        [
            json.dumps({"object": {"sha": "base-commit"}}),
            json.dumps({"tree": {"sha": "base-tree"}}),
            json.dumps({"sha": "blob-a"}),
            json.dumps({"sha": "base-tree"}),
        ]
    )
    client = GitHubClient(gh, lambda: "installation-token")

    assert client.commit_files("org/evals", "main", {"a": b"a"}, "message") is None
    assert len(gh.calls) == 4
