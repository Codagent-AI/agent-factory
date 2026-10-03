"""Extract structured decisions from agent text that may include prose or fences."""

import json
from collections.abc import Callable
from typing import Any


def decision_objects(text: str, predicate: Callable[[Any], bool]) -> list[dict[str, Any]]:
    """Find decision-shaped objects in a left-to-right raw JSON decode scan."""
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if predicate(value):
            candidates.append(value)
        index = text.find("{", max(end, index + 1))
    return candidates
