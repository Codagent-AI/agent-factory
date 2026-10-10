#!/usr/bin/env python3
"""Render, protect, and fit the archived proposal in a feature PR description."""

import hashlib
import html
import re
import sys
from pathlib import Path

LIMIT = 65_536
START = "<!-- agent-factory:proposal:start {token} {href} -->"
SECTION = "<!-- agent-factory:proposal:{token}:section -->"
END = "<!-- agent-factory:proposal:{token}:end -->"
START_LINE = re.compile(r"^<!-- agent-factory:proposal:start ([0-9a-f]{12}) (\S*) -->$")
OMITTED = "_Omitted for length: {names}. Read the [full proposal]({href})._"
ALL_OMITTED = "_The proposal was omitted for length. Read the [full proposal]({href})._"
UNAVAILABLE = "_The proposal could not be included inline; open the Proposal link above._"
HEADING = re.compile(r"^(#{1,6})([ \t].*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def token(text: str) -> str:
    n = 0
    while True:
        candidate = hashlib.sha256(f"{n}\0{text}".encode()).hexdigest()[:12]
        if candidate not in text:
            return candidate
        n += 1


def render(text: str, href: str) -> list[str]:
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    headings: dict[int, int] = {}
    fence: str | None = None
    for i, line in enumerate(lines):
        match = FENCE.match(line)
        if fence is not None:
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = None
        elif match and (match[1][0] != "`" or "`" not in match[2]):
            fence = match[1]
        elif heading := HEADING.match(line):
            headings[i] = len(heading[1])
    shallowest = min(headings.values(), default=3)
    demotion = max(0, 3 - shallowest)
    boundaries = {i for i, level in headings.items() if level == shallowest}
    identifier = token(text)
    result = [START.format(token=identifier, href=html.escape(href, quote=True))]
    for i, line in enumerate(lines):
        if i == 0 or i in boundaries:
            result.extend([SECTION.format(token=identifier), ""])
        level = headings.get(i)
        if level is not None:
            line = "#" * min(6, level + demotion) + line[level:]
        result.append(line)
    result.append(END.format(token=identifier))
    return result


def measure(body: str) -> int:
    return len(body.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n").encode())


def region(lines: list[str]) -> tuple[int, int, str, str] | None:
    for start, line in enumerate(lines):
        if match := START_LINE.fullmatch(line):
            end_line = END.format(token=match[1])
            end = next((i for i in range(start + 1, len(lines)) if lines[i] == end_line), None)
            return (start, end, match[1], match[2]) if end is not None else None
    return None


def masked(lines: list[str]) -> tuple[list[str], list[str] | None]:
    found = region(lines)
    if found is None:
        return lines, None
    start, end, _, _ = found
    return lines[: start + 1] + lines[end + 1 :], lines[start : end + 1]


def unmasked(lines: list[str], removed: list[str] | None) -> list[str]:
    if removed is None:
        return lines
    at = lines.index(removed[0])
    return lines[:at] + removed + lines[at + 1 :]


def section_name(lines: list[str]) -> str:
    first = next((line for line in lines if line.strip()), "")
    heading = HEADING.match(first)
    if heading is None:
        return "Introduction"
    # Optional closing hashes are ATX syntax, not part of the heading's name.
    return re.sub(r"[ \t]+#+[ \t]*$", "", heading[2]).strip()


def fit(body: str) -> str:
    if measure(body) <= LIMIT:
        return body
    eol = "\r\n" if "\r\n" in body else "\n"
    lines = body.split(eol)
    found = region(lines)
    if found is None:
        return body
    start, end, identifier, escaped_href = found
    href = html.unescape(escaped_href)
    marker = SECTION.format(token=identifier)
    boundaries = [i for i in range(start + 1, end) if lines[i] == marker]
    if not boundaries:
        return body
    sections = [lines[a:b] for a, b in zip(boundaries, [*boundaries[1:], end], strict=True)]
    previous: list[str] = []
    notice = lines[start + 1] if start + 1 < end else ""
    suffix = f". Read the [full proposal]({href})._"
    if notice.startswith("_Omitted for length: ") and notice.endswith(suffix):
        previous = notice[len("_Omitted for length: ") : -len(suffix)].split(", ")
    # Empty section markers retain the positions of earlier omissions. Their names
    # live in the notice in that same order, so later rounds can extend the notice
    # in proposal order without retaining any omitted proposal text.
    earlier = iter(previous)
    dropped = {i for i, section in enumerate(sections) if len(section) == 1}
    names = [
        next(earlier, "Introduction") if i in dropped else section_name(section[1:])
        for i, section in enumerate(sections)
    ]
    keys = {"out of scope": 1, "what changes": 2, "why": 3}
    order = sorted(
        (i for i in range(len(sections)) if i not in dropped),
        key=lambda i: (keys.get(names[i].casefold(), 0), -i),
    )
    shortened = body
    for index in order:
        dropped.add(index)
        if len(dropped) == len(sections):
            notice = ALL_OMITTED.format(href=href)
            kept: list[str] = []
        else:
            notice = OMITTED.format(
                names=", ".join(name for i, name in enumerate(names) if i in dropped), href=href
            )
            kept = [
                line
                for i, section in enumerate(sections)
                for line in ([marker] if i in dropped else section)
            ]
        shortened = eol.join([*lines[: start + 1], notice, *kept, *lines[end:]])
        if measure(shortened) <= LIMIT:
            return shortened
    return shortened


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] != "fit":
        raise SystemExit("usage: pr_description.py fit FILE")
    path = Path(sys.argv[2])
    with path.open(newline="") as source:
        body = source.read()
    updated = fit(body)
    if updated != body:
        path.write_text(updated, newline="")


if __name__ == "__main__":
    main()
