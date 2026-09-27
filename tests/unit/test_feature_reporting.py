"""Feature outcome comments expose the review counts and host provenance."""

# pyright: reportPrivateUsage=false

from agent_factory.work_kinds.pull_request.handler import _failed_message, _pr_message


def test_pull_request_comment_has_review_attention_counts() -> None:
    body = _pr_message(
        {
            "pr": {"url": "https://github.com/example/work/pull/12"},
            "review_attention_counts": {"red": 2, "orange": 3, "yellow": 12},
            "sandbox": "host",
        }
    )
    assert "2 red flags" in body
    assert "3 orange flags" in body
    assert "12 yellow items" in body
    assert "recorded Runner and Skills commits were not the versions that executed" in body


def test_failed_feature_comment_links_branch() -> None:
    body = _failed_message(
        {
            "reasons": ["validator stayed red"],
            "branch": "factory/feature-12-abcd",
            "sandbox": "host",
        },
        "Feature",
        repository="example/work",
    )
    assert "https://github.com/example/work/tree/factory/feature-12-abcd" in body
    assert "validator stayed red" in body
