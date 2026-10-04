#!/usr/bin/env python3
"""Report paths that definitively cross the Task boundary."""

import json
import re
import subprocess
import sys
from pathlib import Path

RELEASE_WORDS = re.compile(r"release|publish|deploy", re.I)
RELEASE_TRIGGER = re.compile(r"\b(release|publish|deploy)\b|tags\s*:", re.I)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def content_at(ref: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def release_workflow(text: str) -> bool:
    in_triggers = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[0].isspace():
            in_triggers = stripped.startswith("on:")
            if in_triggers and RELEASE_TRIGGER.search(stripped[3:]):
                return True
            if stripped.startswith("name:") and RELEASE_WORDS.search(stripped):
                return True
        elif in_triggers and RELEASE_TRIGGER.search(stripped):
            return True
    return False


def crossings(base: str) -> list[str]:
    result: list[str] = []
    for path in git("diff", "--name-only", f"{base}..HEAD").splitlines():
        name = Path(path).name.lower()
        if (
            path.startswith("openspec/specs/")
            or name == "codeowners"
            or (
                path.startswith(".github/workflows/")
                and (
                    RELEASE_WORDS.search(name)
                    or release_workflow(content_at("HEAD", path))
                    or release_workflow(content_at(base, path))
                )
            )
        ):
            result.append(path)
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: task-scope-floor.py BASE")
    print(json.dumps(crossings(sys.argv[1])))
