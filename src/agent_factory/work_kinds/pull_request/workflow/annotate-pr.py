#!/usr/bin/env python3
"""Render review attention into the existing feature pull request description."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


RED_VALIDATOR_TITLE = "Validator red after acceptance fixes"
LATER_COMMITS_TITLE = "Commits after acceptance"
# The "Review first" section shows at most this many orange items; the rest are collapsed.
ORANGE_SHOWN = 5


def flag_red_acceptance_validator(flags: dict[str, list[dict[str, str]]], result: Path) -> None:
    """A red validator after an acceptance fix does not block finalization, so flag it red."""
    lines = result.read_text().splitlines() if result.is_file() else []
    if not lines or lines[0].strip() != "FAIL":
        return
    if any(item.get("title") == RED_VALIDATOR_TITLE for item in flags["red"]):
        return
    failures = "; ".join(line.strip() for line in lines[1:] if line.strip())
    flags["red"].append(
        {
            "title": RED_VALIDATOR_TITLE,
            "detail": failures or "the validator stayed red after its bounded repair",
            "link": "#acceptance-evidence",
        }
    )


EVIDENCE_LINK = "#acceptance-evidence"
LINE_SUFFIX = re.compile(r":(\d+)(?:-(\d+))?$")


def review_line(item: dict[str, Any]) -> str:
    """One list line per item: a multi-line title or detail would break the list."""
    title, detail = (" ".join(str(item.get(key, "")).split()) for key in ("title", "detail"))
    return f"- [{title}]({item['link']}): {detail}"


def plural(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def item_link(link: str, repository: str, branch: str, root: Path) -> str:
    """A GitHub reader can open only files committed on the branch; any other local path
    the classifier cited (session evidence, untracked files) points at the evidence section.
    A cited line (`file:12`, `file:12-14`) or anchor (`file#section`) is kept."""
    if link.startswith(("http://", "https://", "#")):
        return link
    base, _, fragment = link.partition("#")
    lines = LINE_SUFFIX.search(base)
    if lines:
        base = base[: lines.start()]
        first, last = lines.group(1), lines.group(2)
        fragment = f"L{first}-L{last}" if last else f"L{first}"
    path = Path(base)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except ValueError:
            return EVIDENCE_LINK
    relative = path.as_posix()
    if not repository or not base or relative == "." or ".." in path.parts:
        return EVIDENCE_LINK
    tracked = subprocess.run(
        ["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True, check=False
    )
    if tracked.returncode != 0:
        return EVIDENCE_LINK
    url = f"https://github.com/{repository}/blob/{quote(branch, safe='/')}/{quote(relative)}"
    return f"{url}#{quote(fragment, safe='-')}" if fragment else url


def main() -> None:
    if sys.argv[1] == "--json":
        payload = json.loads(sys.argv[2])
        artifact_dir = Path(payload["artifact_dir"])
        issue_file = Path(payload["issue_file"])
        change_name = payload["change_name"]
        archive = Path(payload["archived_dir"])
        session_dir = Path(payload.get("session_dir", artifact_dir))
    else:
        artifact_dir, issue_file = map(Path, sys.argv[1:3])
        change_name = sys.argv[3]
        archive = Path(sys.argv[4])
        session_dir = artifact_dir
    issue = json.loads(issue_file.read_text())
    evidence_dir = session_dir / "output" if (session_dir / "output").exists() else artifact_dir
    path = artifact_dir / "review-attention.json"
    flags = json.loads(path.read_text())
    flag_red_acceptance_validator(flags, evidence_dir / "acceptance-validator-result.txt")
    accepted = flags["accepted_head"]
    later = command("git", "log", "--format=%H", f"{accepted}..HEAD").splitlines()
    # Acceptance evidence covers only the accepted head, so a later commit is covered
    # only when an orange item already names it, not merely because it was listed.
    orange_text = " ".join(
        f"{item.get('title', '')} {item.get('detail', '')}" for item in flags["orange"]
    )
    uncovered = [sha for sha in later if sha[:7] not in orange_text]
    flags["later_commits"] = later
    later_item = next(
        (item for item in flags["orange"] if item.get("title") == LATER_COMMITS_TITLE), None
    )
    if uncovered:
        detail = ", ".join(
            f"{sha[:12]} ({command('git', 'log', '-1', '--format=%s', sha)})" for sha in uncovered
        )
        if later_item is None:
            flags["orange"].append(
                {
                    "title": LATER_COMMITS_TITLE,
                    "detail": detail,
                    "link": "#acceptance-evidence",
                }
            )
        else:
            later_item["detail"] = f"{later_item['detail']}, {detail}"
    path.write_text(json.dumps(flags, indent=2) + "\n")
    branch = command("git", "branch", "--show-current")
    pr = json.loads(
        command(
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "number,url",
            "--limit",
            "1",
        )
    )
    if not pr:
        raise SystemExit("no open pull request on feature branch")
    number = pr[0]["number"]
    repository: object = issue.get("repository", "")
    if isinstance(repository, dict):
        repository = cast(dict[str, object], repository).get("nameWithOwner", "")
    if not isinstance(repository, str):
        repository = ""
    prefix = (
        f"https://github.com/{repository}/blob/{branch}/{archive}" if repository else str(archive)
    )
    root = Path(command("git", "rev-parse", "--show-toplevel")).resolve()
    for tier in ("red", "orange", "yellow", "white"):
        for item in flags[tier]:
            item["link"] = item_link(str(item.get("link", "")), repository, branch, root)
    title = issue.get("title")
    lines = [
        f"**Feature for #{issue['number']}:** {title}"
        if title
        else f"**Feature for #{issue['number']}**",
        "",
        "# Review first",
        "",
    ]

    # Commits after acceptance are never cut: acceptance evidence does not cover them.
    def names_later(item: dict[str, Any]) -> bool:
        text = f"{item.get('title', '')} {item.get('detail', '')}"
        return item.get("title") == LATER_COMMITS_TITLE or any(sha[:7] in text for sha in later)

    pinned = [item for item in flags["orange"] if names_later(item)]
    rest = [item for item in flags["orange"] if not names_later(item)]
    room = max(ORANGE_SHOWN - len(pinned), 0)
    shown, hidden = pinned + rest[:room], rest[room:]
    for tier, icon, items, count in (
        ("red", "🔴", flags["red"], len(flags["red"])),
        ("orange", "🟠", shown, len(flags["orange"])),
    ):
        lines.append(f"### {icon} {tier.title()} ({count})")
        if items:
            for item in items:
                lines.append(review_line(item))
        else:
            lines.append(f"- No {tier} items.")
        lines.append("")
    others = (
        f"{plural(len(hidden), 'more orange item')} and "
        f"{plural(len(flags['yellow']), 'yellow item')}"
    )
    if hidden or flags["yellow"]:
        lines.extend([f"{others} are collapsed below the change summary.", ""])
    lines.extend(
        [
            f"Refs #{issue['number']}",
            f"<!-- agent-factory:claim:{issue['claim_id']} -->",
            "",
            "## Change summary",
            "",
            f"Feature: {issue.get('title', change_name)}. [Proposal]({prefix}/proposal.md) · "
            f"[Specifications]({prefix}/specs/) · [Design]({prefix}/design.md) · "
            f"[Test plan]({prefix}/test-plan.md)",
            "",
        ]
    )
    if hidden or flags["yellow"]:
        lines.extend([f"<details><summary>{others}</summary>", ""])
        for tier, icon, items in (("orange", "🟠", hidden), ("yellow", "🟡", flags["yellow"])):
            if items:
                lines.append(f"### {icon} {tier.title()} ({len(items)})")
                for item in items:
                    lines.append(review_line(item))
                lines.append("")
        lines.extend(["</details>", ""])
    lines.extend(
        [
            "<details><summary>Assumptions and decisions</summary>",
            "",
        ]
    )
    decisions = archive / "decisions.md"
    lines.append(decisions.read_text() if decisions.exists() else "No decisions recorded.")
    assumptions = evidence_dir / "acceptance-assumptions.md"
    if assumptions.exists():
        lines.append(assumptions.read_text())
    lines.extend(
        [
            "",
            "</details>",
            "",
            '<details id="acceptance-evidence">'
            "<summary>Acceptance evidence and passed checks</summary>",
            "",
            f"Acceptance ran against `{accepted}`.",
            "",
        ]
    )
    if later:
        lines.append("Later commits: " + ", ".join(f"`{sha}`" for sha in later))
    for name in ("acceptance-flow-evidence.md", "acceptance-findings.md"):
        evidence = evidence_dir / name
        if evidence.exists():
            lines.extend(["", evidence.read_text()])
    for item in flags["white"]:
        lines.append(review_line(item))
    lines.extend(["", "</details>", ""])
    body = artifact_dir / "feature-pr-body.md"
    body.write_text("\n".join(lines))
    edit = ["gh", "pr", "edit", str(number), "--body-file", str(body)]
    last_returncode = 1
    for attempt in range(3):
        result = subprocess.run(edit, check=False)
        last_returncode = result.returncode
        if result.returncode == 0:
            break
        if attempt < 2:
            time.sleep(attempt + 1)
    else:
        raise subprocess.CalledProcessError(last_returncode, edit)


if __name__ == "__main__":
    main()
