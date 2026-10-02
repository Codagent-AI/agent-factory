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


def run(base: str) -> None:
    branch = git("symbolic-ref", "--quiet", "HEAD")
    if git("status", "--porcelain"):
        raise ValueError("working tree is not clean")
    commits = git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    # A review round's branch has an upstream, but its own new commits are not on it yet.
    # Only commits that some remote-tracking ref already contains are published history.
    if commits and git("for-each-ref", "--contains", commits[0], "refs/remotes"):
        raise ValueError("commit already pushed in range")
    if any(len(git("rev-list", "--parents", "-n", "1", commit).split()) != 2 for commit in commits):
        raise ValueError("merge commit in unpushed range")
    parent = base
    for commit in commits:
        message = git("show", "-s", "--format=%B", commit)
        subject, separator, body = message.partition("\n")
        new_message = normalize(subject) + (separator + body if separator else "")
        env = os.environ.copy()
        for key, fmt in (
            ("AUTHOR_NAME", "%an"),
            ("AUTHOR_EMAIL", "%ae"),
            ("AUTHOR_DATE", "%aI"),
            ("COMMITTER_NAME", "%cn"),
            ("COMMITTER_EMAIL", "%ce"),
            ("COMMITTER_DATE", "%cI"),
        ):
            env["GIT_" + key] = git("show", "-s", f"--format={fmt}", commit)
        parent = git(
            "commit-tree",
            git("rev-parse", f"{commit}^{{tree}}"),
            "-p",
            parent,
            env=env,
            input=new_message + "\n",
        )
    if commits:
        git("update-ref", branch, parent, commits[-1])
        git("reset", "--hard", parent)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: normalize-chore-commits.py BASE")
    try:
        run(sys.argv[1])
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"cannot normalize chore commits: {error}")
