#!/usr/bin/env python3
"""Rewrite unpushed task commit subjects while preserving trees and attribution."""

import os
import re
import subprocess
import sys


def git(*args: str, env: dict[str, str] | None = None, input: str | None = None) -> str:
    return subprocess.check_output(["git", *args], env=env, input=input, text=True).strip()


def normalize(subject: str) -> str:
    marker = re.match(r"^(\[[^]]+\]\s*)", subject)
    prefix = marker.group(0) if marker else ""
    rest = subject[len(prefix) :]
    rest = re.sub(r"^[a-z][a-z0-9-]*(?:\([^)]*\))?!?:\s*", "", rest)
    return prefix + "chore: " + rest


# One git log record per commit: hash, parents, tree, attribution, then the full message.
LOG_FIELDS = ("%H", "%P", "%T", "%an", "%ae", "%aI", "%cn", "%ce", "%cI", "%B")
ATTRIBUTION = (
    "AUTHOR_NAME",
    "AUTHOR_EMAIL",
    "AUTHOR_DATE",
    "COMMITTER_NAME",
    "COMMITTER_EMAIL",
    "COMMITTER_DATE",
)


def run(base: str) -> None:
    branch = git("symbolic-ref", "--quiet", "HEAD")
    if git("status", "--porcelain"):
        raise ValueError("working tree is not clean")
    log = git("log", "--reverse", "--format=" + "%x00".join(LOG_FIELDS) + "%x1e", f"{base}..HEAD")
    commits = [record.lstrip("\n").split("\x00") for record in log.split("\x1e") if record.strip()]
    # A review round's branch has an upstream, but its own new commits are not on it yet.
    # Only commits that some remote-tracking ref already contains are published history.
    if commits and git("for-each-ref", "--contains", commits[0][0], "refs/remotes"):
        raise ValueError("commit already pushed in range")
    if any(len(parents.split()) != 1 for _, parents, *_ in commits):
        raise ValueError("merge commit in unpushed range")
    parent = base
    for _, _, tree, *attribution, message in commits:
        subject, separator, body = message.strip().partition("\n")
        new_message = normalize(subject) + (separator + body if separator else "")
        env = os.environ.copy()
        env.update(
            {f"GIT_{key}": value for key, value in zip(ATTRIBUTION, attribution, strict=True)}
        )
        parent = git("commit-tree", tree, "-p", parent, env=env, input=new_message + "\n")
    if commits:
        git("update-ref", branch, parent, commits[-1][0])
        git("reset", "--hard", parent)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: normalize-chore-commits.py BASE")
    try:
        run(sys.argv[1])
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"cannot normalize chore commits: {error}")
