from __future__ import annotations

from agent_factory.runtime import (
    _presents_card,  # pyright: ignore[reportPrivateUsage]
    _should_cancel,  # pyright: ignore[reportPrivateUsage]
)
from agent_factory.store import Claim


def _claim(lifecycle: str) -> Claim:
    return Claim(
        id="c1",
        repository="example/work",
        issue_number=212,
        issue_id="I212",
        project_item_id="P212",
        kind="fix",
        request_fingerprint="fp",
        frozen_spec={},
        lifecycle=lifecycle,
        outcome={},
        preparation={},
        reporting={},
        cleanup={},
    )


def test_settled_claim_is_exempt_from_closure_cancellation() -> None:
    assert _should_cancel(_claim("settled")) is False


def test_active_claim_is_cancelled_on_closure() -> None:
    assert _should_cancel(_claim("active")) is True


def test_blocked_claim_is_cancelled_on_closure() -> None:
    assert _should_cancel(_claim("blocked")) is True


def test_waiting_claim_is_cancelled_on_closure() -> None:
    assert _should_cancel(_claim("waiting")) is True


def test_cancelled_claim_presents_its_card_while_the_issue_stays_closed() -> None:
    assert _presents_card(_claim("cancelled"), issue_state="CLOSED") is True


def test_cancelled_claim_releases_the_card_once_the_issue_is_reopened() -> None:
    # Presenting it would force Done every poll, so a reopened Ready card could never re-run.
    assert _presents_card(_claim("cancelled"), issue_state="OPEN") is False


def test_settled_claim_still_presents_its_card_on_an_open_issue() -> None:
    assert _presents_card(_claim("settled"), issue_state="OPEN") is True
