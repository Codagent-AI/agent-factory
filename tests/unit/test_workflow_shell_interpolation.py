"""Packaged workflow shell commands must survive Agent Runner's shell-safe interpolation.

Agent Runner refuses at run time, not at `-validate` time, to interpolate a `{{name}}`
placeholder that sits inside a shell single-quoted string (textfmt.interpolateShellSafe).
This scans every `command:` and `sh:` skip condition with the same lexical rule.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import pytest

WORKFLOW = Path(str(files("agent_factory.work_kinds.pull_request") / "workflow"))
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
KEY = re.compile(r"^(\s*)(?:- )?(command|skip_if): ?(.*)$")


def _single_quoted(prefix: str) -> bool:
    """Mirror of Agent Runner's shellQuoteContext: is the text after `prefix` single-quoted?"""
    state = "bare"
    index = 0
    while index < len(prefix):
        char = prefix[index]
        if state == "bare":
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "\\":
                index += 1
        elif state == "single":
            if char == "'":
                state = "bare"
        elif char == '"':
            state = "bare"
        elif char == "\\" and index + 1 < len(prefix) and prefix[index + 1] in '$`"\\\n':
            index += 1
        index += 1
    return state == "single"


def _shell_texts(text: str) -> list[tuple[int, str]]:
    """Each command and `sh:` skip condition in a workflow, as (line number, shell text)."""
    lines = text.splitlines()
    result: list[tuple[int, str]] = []
    for number, line in enumerate(lines):
        match = KEY.match(line)
        if match is None:
            continue
        indent, key, value = len(match.group(1)), match.group(2), match.group(3).strip()
        if value in {"|", ">", ">-", "|-"}:
            block: list[str] = []
            for following in lines[number + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                block.append(following)
            body = (
                "\n".join(block)
                if value.startswith("|")
                else " ".join(part.strip() for part in block)
            )
        elif value.startswith("'") and value.endswith("'"):
            body = value[1:-1].replace("''", "'")
        elif value.startswith('"') and value.endswith('"'):
            body = value[1:-1]
        else:
            body = value
        if key == "skip_if":
            body = body.strip()
            if not body.startswith("sh:"):
                continue
            body = body[3:]
        result.append((number + 1, body))
    return result


@pytest.mark.parametrize("workflow", sorted(WORKFLOW.glob("*.yaml")), ids=lambda path: path.name)
def test_no_placeholder_inside_shell_single_quotes(workflow: Path) -> None:
    offending = [
        f"{workflow.name}:{line}: {{{{{match.group(1)}}}}}"
        for line, body in _shell_texts(workflow.read_text())
        for match in PLACEHOLDER.finditer(body)
        if _single_quoted(body[: match.start()])
    ]
    assert offending == []


def test_the_scanner_matches_the_runner_rule() -> None:
    assert _single_quoted("python3 -c 'print(") is True
    assert _single_quoted('python3 - "') is False
    assert _single_quoted("python3 -c 'a' && test ") is False
    assert _single_quoted('echo "it\'s " ') is False
