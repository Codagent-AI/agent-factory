#!/usr/bin/env python3
"""Check the classify step's review-attention.json, reporting every problem at once."""

import json
import re
import sys
from pathlib import Path
from typing import Any, cast

TIERS = ("red", "orange", "yellow", "white")
# `git diff --shortstat` wording, for example "3 files changed, 40 insertions(+)".
DIFF_SIZE = re.compile(r"\b\d+ files? changed\b")
TESTS = re.compile(r"\bTests:", re.IGNORECASE)

value: dict[str, Any] = json.loads(Path(sys.argv[1]).read_text())
errors: list[str] = []
for tier in TIERS:
    if not isinstance(value.get(tier), list):
        raise SystemExit(f"{tier} must be a list")
later_value: object = value.get("later_commits")
if not isinstance(value.get("accepted_head"), str) or not isinstance(later_value, list):
    raise SystemExit("missing acceptance commit or later commits")
later = cast(list[object], later_value)

items: dict[str, list[dict[str, str]]] = {tier: [] for tier in TIERS}
seen: dict[str, str] = {}
for tier in TIERS:
    for index, raw in enumerate(value[tier]):
        where = f"{tier}[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where} must be an object with string title, detail, and link")
            continue
        item = cast(dict[str, object], raw)
        title, detail, link = (item.get(key) for key in ("title", "detail", "link"))
        if not (isinstance(title, str) and isinstance(detail, str) and isinstance(link, str)):
            errors.append(f"{where} must be an object with string title, detail, and link")
            continue
        if not title.strip() or not detail.strip():
            errors.append(f"{where} needs a non-empty title and detail")
            continue
        items[tier].append({"title": title, "detail": detail, "link": link})
        # Each item sits in exactly one tier. Generic titles may repeat within a tier.
        key = title.strip().casefold()
        if key in seen and not seen[key].startswith(f"{tier}["):
            errors.append(
                f"{where} repeats {seen[key]} ({title!r}); an item belongs to exactly one "
                "tier, and items sharing one root cause are one item in the highest tier"
            )
        seen.setdefault(key, where)

for sha in later:
    if not isinstance(sha, str) or not sha:
        errors.append("later_commits must list commit SHAs")
        continue
    naming = [item for item in items["orange"] if sha[:7] in f"{item['title']} {item['detail']}"]
    if not naming:
        errors.append(f"later commit {sha[:7]} is not named by SHA in an orange item")
        continue
    text = " ".join(item["detail"] for item in naming)
    if not DIFF_SIZE.search(text):
        errors.append(
            f"the orange item naming {sha[:7]} must state the diff size as "
            "`git diff --shortstat` prints it (N files changed, ...)"
        )
    if not TESTS.search(text):
        errors.append(
            f"the orange item naming {sha[:7]} must say whether tests cover it in a "
            "sentence starting `Tests:`"
        )

if errors:
    raise SystemExit("\n".join(errors))
