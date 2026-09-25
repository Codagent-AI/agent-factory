#!/usr/bin/env python3
"""Render review attention into the existing feature pull request description."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import cast


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


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
    path = artifact_dir / "review-attention.json"
    flags = json.loads(path.read_text())
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
        (item for item in flags["orange"] if item.get("title") == "Commits after acceptance"), None
    )
    if uncovered:
        detail = ", ".join(
            f"{sha[:12]} ({command('git', 'log', '-1', '--format=%s', sha)})" for sha in uncovered
        )
        if later_item is None:
            flags["orange"].append(
                {
                    "title": "Commits after acceptance",
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
    lines = ["# Review first", ""]
    for tier, icon in (("red", "🔴"), ("orange", "🟠"), ("yellow", "🟡")):
        items = flags[tier]
        lines.append(f"### {icon} {tier.title()} ({len(items)})")
        if items:
            for item in items:
                lines.append(f"- [{item['title']}]({item['link']}): {item['detail']}")
        else:
            lines.append(f"- No {tier} items.")
        lines.append("")
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
            "<details><summary>Assumptions and decisions</summary>",
            "",
        ]
    )
    decisions = archive / "decisions.md"
    lines.append(decisions.read_text() if decisions.exists() else "No decisions recorded.")
    evidence_dir = session_dir / "output" if (session_dir / "output").exists() else artifact_dir
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
        lines.append(f"- [{item['title']}]({item['link']}): {item['detail']}")
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
