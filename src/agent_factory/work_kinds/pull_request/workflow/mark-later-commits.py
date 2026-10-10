#!/usr/bin/env python3
"""Bring a feature pull request description up to date with the commits after acceptance.

Usage: mark-later-commits.py BODY_FILE [BASE] (run in the feature branch's clone; edits in place).

Acceptance evidence covers only the accepted commit, and the review items were classified
before later commits (finalization fixes, review rounds) landed. For every commit from the
accepted one (named by the description's "Acceptance ran against" line) to HEAD, this:
- lists it in the evidence's "Later commits:" line unless the line names it;
- names it in the "Commits after acceptance" orange item unless an orange item names it;
- marks each red, orange, or yellow item whose linked file it changed as possibly fixed;
- with a resolvable target-branch BASE, instead marks items whose linked file no longer
  differs between the PR merge base and HEAD as no longer changed by this PR, provided
  the PR changed that file at acceptance.
Without BASE or when its git checks fail, items keep the possibly-fixed behavior.
Everything else in the description is left as it is. Without an acceptance line or a
readable history, the description is left unchanged.
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

LATER_COMMITS_TITLE = "Commits after acceptance"
ACCEPTED = re.compile(r"^Acceptance ran against `([0-9a-f]{7,40})`\.")
TIER = re.compile(r"^### (\S+) (Red|Orange|Yellow) \((\d+)\)$")
ITEM = re.compile(r"^- \[[^\]]*\]\(([^)]*)\)")
MARKER = re.compile(r" _\(may be fixed by [^)]*: changed its linked file after acceptance\)_$")
UNCHANGED_MARKER = re.compile(r" _\(linked file no longer changed by this PR\)_$")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def later_commits(accepted: str) -> list[tuple[str, str, set[str]]]:
    """(sha, subject, files changed) per commit on the branch after acceptance, oldest
    first. A merge is listed but names no files, and the commits it brings in from another
    branch are not listed: they are not fixes of the feature's items."""
    output = git(
        "log",
        "--first-parent",
        "--reverse",
        "--format=%x00%H%x00%s",
        "--name-only",
        f"{accepted}..HEAD",
    )
    commits: list[tuple[str, str, set[str]]] = []
    parts = output.split("\0")[1:]
    for sha, rest in zip(parts[0::2], parts[1::2], strict=True):
        subject, _, names = rest.partition("\n")
        commits.append((sha, subject, {name for name in names.split("\n") if name}))
    return commits


def mark(text: str, base: str | None = None) -> str:
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    accepted = next((m.group(1) for line in lines if (m := ACCEPTED.match(line))), None)
    if accepted is None:
        return text
    try:
        commits = later_commits(accepted)
    except (subprocess.CalledProcessError, OSError, ValueError):
        return text
    if not commits:
        return text

    merge_base = None
    changed_since_acceptance: set[str] = set()
    changed_at_acceptance: set[str] = set()
    if base:
        try:
            merge_base = git("merge-base", base, "HEAD")
            acceptance_base = git("merge-base", base, accepted)
            changed_at_acceptance = set(
                git("diff", "--name-only", acceptance_base, accepted).splitlines()
            )
            changed_since_acceptance = set(
                git("diff", "--name-only", accepted, "HEAD").splitlines()
            )
        except (subprocess.CalledProcessError, OSError):
            merge_base = None
    candidate_paths = changed_since_acceptance | {name for _, _, names in commits for name in names}

    tier_of: list[str | None] = []
    tier = None
    for line in lines:
        heading = TIER.match(line)
        if heading:
            tier = heading.group(2)
        elif line.startswith("#") or line.startswith("</details>"):
            tier = None
        tier_of.append(tier)

    for index, line in enumerate(lines):
        item = ITEM.match(line)
        if not item or tier_of[index] is None:
            continue
        target = item.group(1).split("#", 1)[0]
        fixes = [
            sha[:7]
            for sha, _, names in commits
            if any(target.endswith("/" + quote(name)) for name in names)
        ]
        line = MARKER.sub("", UNCHANGED_MARKER.sub("", line))
        unchanged = False
        matches = sorted(
            (name for name in candidate_paths if target.endswith("/" + quote(name))),
            key=len,
            reverse=True,
        )
        # A target-branch merge can change a cited file the PR never changed. Such an
        # item may still apply, even though the file now equals the target branch.
        if merge_base and matches and matches[0] in changed_at_acceptance:
            try:
                result = subprocess.run(
                    [
                        "git",
                        "--literal-pathspecs",
                        "diff",
                        "--quiet",
                        merge_base,
                        "HEAD",
                        "--",
                        matches[0],
                    ],
                    capture_output=True,
                    check=False,
                )
                unchanged = result.returncode == 0
            except OSError:
                pass
        if unchanged:
            line += " _(linked file no longer changed by this PR)_"
        elif fixes:
            named = ", ".join(f"`{sha}`" for sha in fixes)
            line += f" _(may be fixed by {named}: changed its linked file after acceptance)_"
        lines[index] = line

    orange = " ".join(line for line, tier in zip(lines, tier_of, strict=True) if tier == "Orange")
    unnamed = [(sha, subject) for sha, subject, _ in commits if sha[:7] not in orange]
    if unnamed:
        detail = ", ".join(f"{sha[:12]} ({subject})" for sha, subject in unnamed)
        prefix = f"- [{LATER_COMMITS_TITLE}]("
        existing = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
        if existing is not None:
            lines[existing] += f", {detail}"
        else:
            heading = next((i for i, tier in enumerate(tier_of) if tier == "Orange"), None)
            if heading is not None:
                match = TIER.match(lines[heading])
                assert match is not None
                count = int(match.group(3)) + 1
                lines[heading] = f"### {match.group(1)} Orange ({count})"
                new = f"{prefix}#acceptance-evidence): {detail}"
                if heading + 1 < len(lines) and lines[heading + 1] == "- No orange items.":
                    lines[heading + 1] = new
                else:
                    lines.insert(heading + 1, new)

    prefix = "Later commits: "
    existing = next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)
    if existing is None:
        at = next(i for i, line in enumerate(lines) if ACCEPTED.match(line))
        lines[at + 1 : at + 1] = ["", prefix.rstrip()]
        existing = at + 2
    missing = [f"`{sha}`" for sha, _, _ in commits if sha not in lines[existing]]
    if missing:
        separator = ", " if lines[existing] != prefix.rstrip() else " "
        lines[existing] += separator + ", ".join(missing)
    return eol.join(lines)


def main() -> None:
    path = Path(sys.argv[1])
    text = path.open(newline="").read()
    updated = mark(text, sys.argv[2] if len(sys.argv) > 2 else None)
    if updated != text:
        path.write_text(updated, newline="")


if __name__ == "__main__":
    main()
