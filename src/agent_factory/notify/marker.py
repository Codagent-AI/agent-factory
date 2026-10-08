"""Issue-body marker for the originating Claude Code session."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

# The JSON may not span a comment end, so a malformed comment cannot swallow a later marker.
PATTERN = re.compile(r"<!--\s*codagent-session:\s*(\{(?:(?!-->).)*?\})\s*-->", re.DOTALL)


def parse(body: str) -> dict[str, str] | None:
    last: dict[str, str] | None = None
    for match in PATTERN.finditer(body):
        try:
            value: Any = json.loads(match.group(1))
            if not isinstance(value, dict):
                continue
            value = cast(dict[str, object], value)
            session_id = value.get("session_id")
            if not isinstance(session_id, str) or str(uuid.UUID(session_id)) != session_id:
                continue
            last = {"session_id": session_id}
            for key in ("name", "recorded_at"):
                if isinstance(value.get(key), str):
                    last[key] = value[key]
        except (ValueError, TypeError):
            continue
    return last


def render(session_id: str, name: str | None = None, now: datetime | None = None) -> str:
    canonical = str(uuid.UUID(session_id))
    if canonical != session_id:
        raise ValueError("session_id must be a canonical UUID")
    value = {"session_id": canonical}
    safe_name = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]", "", name or ""))[:64]
    if safe_name:
        value["name"] = safe_name
    value["recorded_at"] = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"<!-- codagent-session: {json.dumps(value, separators=(',', ':'))} -->"


def _append(body: str, marker: str) -> str:
    # Only valid markers are replaced; a malformed marker-like comment is ordinary body text.
    matches = [match for match in PATTERN.finditer(body) if parse(match.group())]
    if not matches:
        separator = (
            "" if not body or body.endswith("\n\n") else "\n" if body.endswith("\n") else "\n\n"
        )
        return body + separator + marker + "\n"
    # Replace the last marker where it stands and drop any earlier ones, so every other byte
    # of the body is kept. An earlier marker on its own line takes its line break with it.
    parts: list[str] = []
    position = 0
    for match in matches[:-1]:
        parts.append(body[position : match.start()])
        position = match.end()
        own_line = match.start() == 0 or body[match.start() - 1] == "\n"
        if own_line and body.startswith("\n", position):
            position += 1
    last = matches[-1]
    parts.extend((body[position : last.start()], marker, body[last.end() :]))
    return "".join(parts)


def stamp(body: str, session_id: str, name: str | None = None, now: datetime | None = None) -> str:
    return _append(body, render(session_id, name, now))


def carry(old_body: str, new_body: str) -> str:
    matches = [match.group() for match in PATTERN.finditer(old_body) if parse(match.group())]
    return _append(new_body, matches[-1]) if matches else new_body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stamp", "carry"))
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    if args.action == "stamp":
        if len(args.files) != 1:
            parser.error("stamp requires FILE")
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not session_id:
            print("session marker unchanged (no Claude session)")
            return
        from agent_factory.notify.registry import resolve

        path = Path(args.files[0])
        session = resolve(session_id)
        path.write_text(stamp(path.read_text(), session_id, session.name if session else None))
        print("session marker stamped")
    else:
        if len(args.files) != 2:
            parser.error("carry requires OLD NEW")
        old, new = map(Path, args.files)
        new.write_text(carry(old.read_text(), new.read_text()))
        print("session marker carried")


if __name__ == "__main__":
    main()
