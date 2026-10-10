#!/usr/bin/env python3
"""Record explained repair stops, without changing recovery on incomplete evidence."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

Event = tuple[str, str, dict[str, Any]]

ORDER = (
    "proposal",
    "proposal-review",
    "specs",
    "design",
    "test-plan",
    "approach-review",
    "write-tasks",
    "implement",
    "archive",
    "verify",
    "finalize",
)
NEXT = {"planned": "implement", "implemented": "archive", "archived": "verify"}
ARCHIVE_SUMMARY = (
    "Implementation is complete and pushed. Archiving is blocked by the cause above. "
    "Fix it on the target branch, or commit the fix to this branch, then comment on the issue: "
    "the next attempt merges the target branch and resumes at archive."
)


def parse_audit(lines: Iterable[str]) -> list[Event]:
    events: list[Event] = []
    for line in lines:
        match = re.match(r"^\S+ (?:\[([^]]+)\] )?(\w+) (.*)$", line)
        if not match:
            continue
        path, event, payload = match.groups()
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        events.append(
            (path or "", event, cast(dict[str, Any], data) if isinstance(data, dict) else {})
        )
    return events


def descendant(path: str, ancestor: str) -> bool:
    return path == ancestor or path.startswith(ancestor + ", ") or path.startswith(ancestor + ":")


def explanation(response: object) -> str:
    if not isinstance(response, str):
        return ""
    return re.sub(r"(?:^|\n)REPAIR_BLOCKED\s*$", "", response).strip()


def blocked_response(events: list[Event], endpoint: str, scope: str) -> tuple[str, str] | None:
    candidate = None
    for path, event, data in events:
        if scope == "path":
            if path != endpoint:
                continue
            if event == "step_start" or (event == "step_end" and data.get("outcome") == "success"):
                candidate = None
        elif scope == "subtree":
            if not descendant(path, endpoint):
                continue
            if event not in ("step_end", "sub_workflow_end"):
                candidate = None
        else:
            raise ValueError("unknown supersession scope")
        if event == "repair_blocked":
            reason = explanation(data.get("response"))
            candidate = (path, reason) if reason else None
    return candidate


def causal_endpoint(events: list[Event]) -> str | None:
    """Follow terminal failed ancestors back to the owning repair-bearing check."""
    run_indices = [i for i, (_, event, _) in enumerate(events) if event.startswith("run_")]
    if not run_indices:
        return None
    index = run_indices[-1]
    _, event, data = events[index]
    if (
        event != "run_end"
        or data.get("outcome") != "failed"
        or data.get("failure_kind") == "infrastructure"
    ):
        return None
    endpoint = None
    blocked = False
    for path, event, data in reversed(events[:index]):
        if event in ("step_end", "sub_workflow_end"):
            if data.get("outcome") != "failed" or (endpoint and not descendant(path, endpoint)):
                break
            endpoint = path
            blocked = event == "step_end" and data.get("repair_blocked") is True
        elif event in ("step_start", "sub_workflow_start"):
            break
    return endpoint if endpoint and blocked else None


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def later(start: str, pushed: str) -> str:
    points = [point for point in (start, pushed) if point in ORDER]
    return max(points, key=ORDER.index) if points else ""


def resume_point(artifact_dir: Path, branch: str) -> tuple[str, str]:
    """Count only checkpoints on published first-parent history after preparation."""
    path = artifact_dir / "attempt-start.json"
    start: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    pushed = ""
    head = start.get("head")
    if head:
        log = git(
            "log", "--first-parent", "--format=%B%x00", f"refs/remotes/origin/{branch}", f"^{head}"
        )
        if log.returncode == 0:
            for commit in log.stdout.split("\0"):
                match = re.search(
                    r"^Factory-Checkpoint: (planned|implemented|archived)\s*$", commit, re.M
                )
                if match:
                    pushed = NEXT[match[1]]
                    break
    return later(start.get("resume_from", ""), pushed), start.get("prior_head", "")


def published(branch: str) -> bool:
    result = git("ls-remote", "--exit-code", "origin", f"refs/heads/{branch}")
    if result.returncode in (0, 2):
        return result.returncode == 0
    return git("show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}").returncode == 0


def build_outcome(
    blocked_step: str, reason: str, resume: str, branch: str, archive: bool = False
) -> dict[str, object]:
    if archive:
        summary = ARCHIVE_SUMMARY
    else:
        summary = (
            f"Repair of {blocked_step} is blocked by the cause above. Fix it on the target branch, "
            "or commit the fix to the claim branch, then comment on the issue: "
        )
        summary += (
            f"the next attempt merges the target branch and resumes at {resume}."
            if resume
            else "the next attempt starts a fresh definition from the target branch."
        )
    outcome: dict[str, object] = {
        "contract": "factory-feature/1",
        "outcome": "needs-input",
        "blocked_step": blocked_step,
        "reasons": [reason],
        "questions": [reason],
        "direction_summary": summary,
    }
    if resume:
        outcome["stopped_step"] = resume
    if branch:
        outcome["branch"] = branch
    return outcome


def record(context: str, session_dir: Path, artifact_dir: Path, branch: str) -> None:
    destination = artifact_dir / "feature-outcome.json"
    if destination.exists():
        return
    audit = session_dir / "audit.log"
    events = parse_audit(audit.read_text().splitlines()) if audit.exists() else []
    if context == "archive":
        block = blocked_response(events, "archive, sub:archive-change", "subtree")
        if not block:
            # Preserve the archive hook's distinct diagnostics for an empty declaration.
            raw = None
            for path, event, data in events:
                if descendant(path, "archive, sub:archive-change") and event not in (
                    "step_end",
                    "sub_workflow_end",
                ):
                    raw = data.get("response") if event == "repair_blocked" else None
            if isinstance(raw, str) and not explanation(raw):
                raise ValueError("archive repair block has no explanation")
            raise ValueError(
                "archive step failed without a REPAIR_BLOCKED declaration; see archive entries in "
                + str(audit)
            )
        resume = "archive"
        branch_value = branch
    else:
        endpoint = causal_endpoint(events)
        block = blocked_response(events, endpoint, "path") if endpoint else None
        if not block:
            return
        resume, prior_head = resume_point(artifact_dir, branch)
        exists = published(branch)
        if resume and not exists:
            if (
                not prior_head
                or git("push", "origin", f"{prior_head}:refs/heads/{branch}").returncode
            ):
                return
            exists = True
        branch_value = branch if exists else ""
    outcome = build_outcome(*block, resume, branch_value, archive=context == "archive")
    # Publish a complete file atomically, without overwriting another recorder's outcome.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=artifact_dir, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(outcome) + "\n")
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("context", choices=("run", "archive"))
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    args = parser.parse_args()
    try:
        record(args.context, args.session_dir, args.artifact_dir, args.branch)
    except Exception as error:
        if args.context == "archive":
            raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
