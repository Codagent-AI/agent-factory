#!/usr/bin/env python3
"""Count the published pull request's code changes and maintain its description section."""

import argparse
import ast
import html
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

EXCLUDE = (
    "openspec/*",
    "docs/*",
    "*.md",
    "*.mdx",
    "*.txt",
    "*.rst",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.json",
    "*.ini",
    "*.cfg",
    "*.lock",
    "go.sum",
    "go.mod",
    "LICENSE*",
    ".github/*",
    ".validator/*",
    ".claude/*",
    "testdata/*",
)
TEST = ("*_test.go", "tests/*", "*/tests/*", "test_*.py", "*_test.py", "*.test.ts", "*.spec.ts")
START = "<!-- agent-factory:loc -->"
END = "<!-- agent-factory:loc-end -->"
HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
INDEX = re.compile(r"^index ([0-9a-f]+)\.\.([0-9a-f]+)(?: (\d+))?$")


@dataclass
class Row:
    files: int = 0
    added: int = 0
    modified: int = 0
    deleted: int = 0


@dataclass
class Counts:
    code: Row = field(default_factory=Row)
    tests: Row = field(default_factory=Row)
    merge_base: str = ""


@dataclass
class Unavailable:
    reason: str


def classify(path: str) -> str:
    from fnmatch import fnmatchcase

    name = path.rsplit("/", 1)[-1]
    for patterns, kind in ((EXCLUDE, "excluded"), (TEST, "tests")):
        if any(fnmatchcase(path, p) or fnmatchcase(name, p) for p in patterns):
            return kind
    return "code"


def unquote_path(path: str) -> str:
    # Git quotes bytes (including octal UTF-8), rather than Unicode code points.
    if path.startswith('"'):
        path = (
            ast.literal_eval(path.encode("utf-8", "surrogateescape").decode("latin1"))
            .encode("latin1")
            .decode("utf-8", "surrogateescape")
        )
    else:
        path = path.removesuffix("\t")
    return path[2:] if path.startswith(("a/", "b/")) else path


def count(diff_text: str, binary_blobs: set[str] | None = None) -> Counts:
    """Parse hunks, never content lines, using only LF as a patch separator."""
    result = Counts()
    paths: dict[str, set[str]] = {"code": set(), "tests": set()}
    old_path = new_path = ""
    in_hunks = binary = False
    for line in diff_text.split("\n"):
        if line.startswith("diff --git "):
            old_path = new_path = ""
            in_hunks = binary = False
        elif not in_hunks and (match := INDEX.match(line)):
            binary = bool(binary_blobs and binary_blobs.intersection(match.group(1, 2)))
        elif not in_hunks and line.startswith("--- "):
            old_path = unquote_path(line[4:])
        elif not in_hunks and line.startswith("+++ "):
            new_path = unquote_path(line[4:])
        elif match := HUNK.match(line):
            in_hunks = True
            path = old_path if new_path == "/dev/null" else new_path
            kind = classify(path)
            if binary or not path or kind == "excluded":
                continue
            old, new = (int(value) if value is not None else 1 for value in match.groups())
            if not old and not new:
                continue
            row = result.code if kind == "code" else result.tests
            modified = min(old, new)
            row.added += new - modified
            row.modified += modified
            row.deleted += old - modified
            paths[kind].add(path)
    result.code.files = len(paths["code"])
    result.tests.files = len(paths["tests"])
    return result


# A count must never stall a workflow step: no command may wait for a credential prompt
# or run unbounded on an unresponsive remote.
COMMAND_TIMEOUT = 120.0
NON_INTERACTIVE = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "GH_PROMPT_DISABLED": "1",
}


def command(*args: str, env: dict[str, str] | None = None) -> bytes:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env={**(os.environ if env is None else env), **NON_INTERACTIVE},
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{' '.join(args)}: timed out after {error.timeout:g}s") from error
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{' '.join(args)}: {detail or f'exit {completed.returncode}'}")
    return completed.stdout


def blob_prefix(blob: str, env: dict[str, str]) -> bytes:
    """Read only the binary-detection prefix, then stop and reap the Git process."""
    args = ["git", "cat-file", "blob", blob]
    # A file keeps stderr from blocking the stdout read or accumulating in memory.
    with (
        tempfile.TemporaryFile() as errors,
        subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors, env=env
        ) as process,
    ):
        assert process.stdout is not None
        terminated = False
        try:
            prefix = process.stdout.read(8000)
            if len(prefix) == 8000 and process.poll() is None:
                process.kill()
                terminated = True
            status = process.wait()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        if status and not (terminated and status == -signal.SIGKILL):
            errors.seek(0)
            detail = errors.read(8000).decode("utf-8", "replace").strip()
            raise RuntimeError(f"{' '.join(args)}: {detail or f'exit {status}'}")
        return prefix


def measure(base_ref: str, head_sha: str) -> Counts | Unavailable:
    if not base_ref:
        return Unavailable("pull request base branch unknown")
    if not head_sha:
        return Unavailable("pull request head unknown")
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
    }
    try:
        # Fetch retains the checkout's authentication; isolate config for the diff.
        command(
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}",
        )
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"{head_sha}^{{commit}}"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
        if exists.returncode:
            command("git", "fetch", "--no-tags", "origin", head_sha)
        mb = (
            command("git", "merge-base", f"refs/remotes/origin/{base_ref}", head_sha, env=env)
            .decode()
            .strip()
        )
        patch = command(
            "git",
            "-c",
            "core.quotePath=false",
            "-c",
            "core.attributesFile=/dev/null",
            "diff",
            "-U0",
            "--inter-hunk-context=0",
            "--diff-algorithm=myers",
            "--indent-heuristic",
            "--text",
            "--full-index",
            "--no-renames",
            "--submodule=short",
            "--ignore-submodules=none",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            mb,
            head_sha,
            env=env,
        ).decode("utf-8", "surrogateescape")
        binary_blobs: set[str] = set()
        seen: set[str] = set()
        gitlink = False
        for line in patch.split("\n"):
            if line.startswith("diff --git "):
                gitlink = False
            if line.endswith("mode 160000"):
                gitlink = True
            match = INDEX.match(line)
            if not match or gitlink or match.group(3) == "160000":
                continue
            for blob in match.group(1, 2):
                if blob in seen or not blob.strip("0"):
                    continue
                seen.add(blob)
                if b"\0" in blob_prefix(blob, env):
                    binary_blobs.add(blob)
        result = count(patch, binary_blobs)
        result.merge_base = mb
        return result
    except (OSError, RuntimeError, ValueError, SyntaxError, subprocess.TimeoutExpired) as error:
        return Unavailable(" ".join(str(error).split()))


def pr_state(number: int | None = None) -> dict[str, Any] | None:
    if number is None:
        branch = command("git", "branch", "--show-current").decode().strip()
        prs = json.loads(
            command(
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number",
                "--limit",
                "1",
            )
        )
        if not prs:
            return None
        number = int(prs[0]["number"])
    state: dict[str, Any] = json.loads(
        command("gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{number}")
    )
    state["number"] = number
    return state


def render(result: Counts | Unavailable, base_ref: str, head: str) -> str:
    lines = [START, "### Lines of code", ""]
    if isinstance(result, Unavailable):
        reason = html.escape(" ".join(result.reason.split()), quote=False)
        lines.append(f"Lines-of-code count unavailable: {reason}.")
    else:
        lines += [
            "| kind | files | added | modified | deleted | +/- |",
            "|---|---|---|---|---|---|",
        ]
        for kind, row in (("code", result.code), ("tests", result.tests)):
            lines.append(
                f"| {kind} | {row.files} | {row.added} | {row.modified} | {row.deleted} | "
                f"+{row.added + row.modified} −{row.deleted + row.modified} |"
            )
        lines += [
            "",
            f"Measured `{result.merge_base[:12]}..{head[:12]}` (merge base with `{base_ref}` "
            "to the pull request head).",
            "OpenSpec specifications, other documentation, and configuration are excluded.",
        ]
    return "\n".join([*lines, END])


def live_lines(body: str) -> list[tuple[int, int, str]]:
    """Return offsets and stripped text outside backtick and tilde fences."""
    result: list[tuple[int, int, str]] = []
    offset = 0
    fence = ""
    for line in body.split("\n"):
        end = min(offset + len(line) + 1, len(body))
        text = line.rstrip()
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", text)
        if fence:
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = ""
        elif match:
            fence = match[1]
        else:
            result.append((offset, end, text))
        offset = end
    return result


def upsert(body: str, section: str) -> str:
    eol = "\r\n" if "\r\n" in body else "\n"
    section = section.replace("\r\n", "\n").replace("\n", eol)
    lines = live_lines(body)
    blocks: list[tuple[int, int]] = []
    dangling: list[tuple[int, int]] = []
    start: int | None = None
    for i, (_begin, end, text) in enumerate(lines):
        if text == START and start is None:
            start = i
        elif text == END and start is not None:
            blocks.append((lines[start][0], end))
            start = None
    if start is not None:
        begin, end, _ = lines[start]
        for next_begin, next_end, text in lines[start + 1 :]:
            if next_begin != end or not (
                not text
                or text == "### Lines of code"
                or text.startswith(
                    (
                        "|",
                        "Measured ",
                        "OpenSpec ",
                        "Lines-of-code count unavailable:",
                        "unavailable",
                    )
                )
            ):
                break
            end = next_end
        dangling.append((begin, end))
    if blocks:
        edits = [
            (a, b, section + (eol if body[a:b].endswith("\n") else "")) if i == 0 else (a, b, "")
            for i, (a, b) in enumerate(blocks)
        ]
        edits += [(a, b, "") for a, b in dangling]
        for a, b, replacement in sorted(edits, reverse=True):
            body = body[:a] + replacement + body[b:]
        return body
    for a, b in reversed(dangling):
        body = body[:a] + body[b:]
    lines = live_lines(body)
    anchor: int | None = None
    for i, (_, _, text) in enumerate(lines):
        if text == "## Change summary":
            anchor = next((end for _, end, text in lines[i + 1 :] if text), None)
            break
    if anchor is None:
        anchor = next(
            (end for _, end, text in lines if text.startswith("<!-- agent-factory:claim:")),
            len(body),
        )
    before, after = body[:anchor], body[anchor:]
    prefix = "" if not before or before.endswith("\n") else eol
    blank_before = "" if before.endswith(eol + eol) else eol
    blank_after = "" if after.startswith(eol) else eol
    return before + prefix + blank_before + section + eol + blank_after + after


def state_section(state: dict[str, Any]) -> str:
    base = str(cast(dict[str, Any], state.get("base") or {}).get("ref") or "")
    head = str(cast(dict[str, Any], state.get("head") or {}).get("sha") or "")
    result = measure(base, head)
    return render(result, base, head)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=("section", "refresh-file", "refresh-pr"))
    parser.add_argument("body", nargs="?")
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--number", type=int)
    parser.add_argument("--artifact-dir", default=".")
    args = parser.parse_args()
    if args.mode is None:
        payload = json.load(sys.stdin)
        args.mode = payload["mode"]
        args.artifact_dir = payload.get("artifact_dir", ".")
        args.number = payload.get("number")
    if args.mode == "section":
        result = measure(args.base_ref, args.head)
        print(render(result, args.base_ref, args.head))
        return 0
    try:
        state = pr_state(args.number)
        if state is None:
            return 0
        section = state_section(state)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
        if args.mode != "refresh-file":
            print(str(error), file=sys.stderr)
            return 1
        section = render(Unavailable(str(error)), "", "")
        state = {}
    if args.mode == "refresh-file":
        try:
            path = Path(args.body)
            original = path.read_bytes().decode("utf-8", "surrogateescape")
            path.write_bytes(upsert(original, section).encode("utf-8", "surrogateescape"))
        except OSError as error:
            print(str(error), file=sys.stderr)
        return 0
    original = state.get("body") or ""
    wanted = upsert(original, section)
    directory = Path(args.artifact_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        body = directory / "pr-body-loc.md"
        body.write_bytes(wanted.encode("utf-8", "surrogateescape"))
        if wanted == original:
            return 0
        for attempt in range(3):
            try:
                command(
                    "gh",
                    "api",
                    "--method",
                    "PATCH",
                    f"repos/{{owner}}/{{repo}}/pulls/{state['number']}",
                    "-F",
                    f"body=@{body}",
                    "--silent",
                )
                return 0
            except RuntimeError as error:
                print(str(error), file=sys.stderr)
                if attempt < 2:
                    time.sleep(attempt + 1)
        return 1
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
