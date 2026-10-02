#!/usr/bin/env python3
"""Produce a task-compliance verdict bound to a feature head and target base."""

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

JOB = re.compile(
    r"^\[(PASS|FAIL|ERROR)\]\s+review:(.+):task-compliance \(([^)]+)\) \(([^)]+)\) - (.*)$", re.M
)
RECORD = re.compile(r"review_(.+)_task-compliance_[^/]+\.json$")
CHECKBOX = re.compile(r"(?m)^(\s*- \[)[ xX](\])")
TITLES = {"Review Gates:", "Entry Points:"}


def command(*args: str, cwd: Path | None = None) -> str:
    try:
        return subprocess.check_output(
            args, cwd=cwd, text=True, stderr=subprocess.PIPE, timeout=120
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"exit status {exc.returncode}"
        raise ValueError(f"{' '.join(args)}: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{' '.join(args)}: timed out after {exc.timeout}s") from exc


def tasks_hash(path: Path) -> str:
    normalized = CHECKBOX.sub(r"\1 \2", path.read_text())
    return hashlib.sha256(normalized.encode()).hexdigest()


def declarations(output: str) -> tuple[bool, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in output.splitlines():
        if line.strip() in TITLES:
            current = line.strip()
            sections[current] = []
        elif current and line and not line[0].isspace() and line.endswith(":"):
            current = ""
        elif current:
            sections[current].append(line)
    if not TITLES.issubset(sections):
        raise ValueError("missing Review Gates or Entry Points section")
    declared = any(
        re.match(r"\s*- task-compliance(?:\s|$)", line) for line in sections["Review Gates:"]
    )
    entries: list[str] = []
    entry = ""
    for line in sections["Entry Points:"]:
        found = re.match(r"\s*- (\S+)\s*$", line)
        if found:
            entry = found.group(1)
        elif entry and re.search(r"\bReviews:\s*.*(?<![\w-])task-compliance(?![\w-])", line):
            entries.append(entry)
    if declared and not entries:
        raise ValueError("task-compliance has no declaring entry point")
    return declared, entries


def changed_paths(base: str, head: str) -> list[str]:
    return command("git", "diff", "--name-only", base, head).splitlines()


def uncovered_paths(paths: list[str], entries: list[str]) -> list[str]:
    return [
        path
        for path in paths
        if not path.startswith("openspec/")
        and not any(
            entry == "." or path == entry.rstrip("/") or path.startswith(entry.rstrip("/") + "/")
            for entry in entries
        )
    ]


def reusable(old: dict[str, Any], head: str, base: str, target_ref: str, digest: str) -> bool:
    if (
        old.get("result") not in ("passed", "failed")
        or old.get("base") != base
        or old.get("target_ref") != target_ref
        or old.get("tasks_sha256") != digest
    ):
        return False
    prior = old.get("reviewed_head")
    try:
        if not isinstance(prior, str) or old.get("reviewed_tree") != command(
            "git", "rev-parse", f"{prior}^{{tree}}"
        ):
            return False
        return not any(not path.startswith("openspec/") for path in changed_paths(prior, head))
    except ValueError:
        return False


def verdict(logs: Path, stdout: str) -> tuple[str, str, list[dict[str, Any]], set[str]]:
    """Only dispatched jobs prove a review. The exit status is deliberately ignored."""
    statuses: list[str] = []
    dispatched: set[str] = set()
    preserved: set[str] = set()
    violations: list[dict[str, Any]] = []
    error = ""
    paths = logs.rglob("review_*task-compliance*.json") if logs.exists() else iter(())
    for path in paths:
        if path.parent != logs and not path.parent.name.startswith("previous"):
            return "not-run", "review record in unexpected log location", [], set()
        match = RECORD.fullmatch(path.name)
        if not match:
            return "not-run", "unrecognized task-compliance review record", [], set()
        try:
            raw: object = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return "not-run", "unreadable task-compliance review record", [], set()
        if not isinstance(raw, dict):
            return "not-run", "invalid task-compliance review record", [], set()
        record = cast(dict[str, Any], raw)
        status = record.get("status")
        if status in ("pass", "fail", "error"):
            if not record.get("attempt_id"):
                return "not-run", "review record has no dispatch identifier", [], set()
            dispatched.add(match.group(1))
            statuses.append(status)
            if status == "error":
                error = str(record.get("error") or record.get("message") or "review error")
            if status == "fail":
                found: object = record.get("violations")
                if not isinstance(found, list) or not all(
                    isinstance(item, dict) for item in cast(list[object], found)
                ):
                    return "not-run", "invalid task-compliance violations", [], set()
                violations.extend(cast(list[dict[str, Any]], found))
        elif status in ("skipped_prior_pass", "preserved_one_shot"):
            preserved.add(match.group(1))
        else:
            return "not-run", f"unknown review status: {status}", [], set()
    for status, entry, _adapter, _seconds, message in JOB.findall(stdout):
        if entry in preserved or re.search(r"preserv|prior pass|skip", message, re.I):
            continue
        dispatched.add(entry)
        statuses.append({"PASS": "pass", "FAIL": "fail", "ERROR": "error"}[status])
        if status == "ERROR":
            error = message
    if "fail" in statuses:
        return "failed", "", violations, dispatched
    if "error" in statuses:
        return "error", error or "review error", [], dispatched
    if statuses and all(status == "pass" for status in statuses):
        return "passed", "", [], dispatched
    reason = next(
        (
            word
            for word in ("no_applicable_gates", "no_changes", "Trusted", "trusted")
            if word in stdout
        ),
        "",
    )
    status = re.search(r"(?m)^Status:\s*(.+)$", stdout)
    lines = [
        line.strip()
        for line in stdout.splitlines()
        if line.strip() and not line.strip().startswith("━")
    ]
    return (
        "not-run",
        reason
        or (
            status.group(1)
            if status
            else (lines[-1] if lines else "no task-compliance review record")
        ),
        [],
        set(),
    )


def run_review(args: list[str], cwd: Path, timeout: int) -> tuple[str, str]:
    """Run the review in its own process group so a timeout also stops the reviewer CLI it spawned.

    Colour is disabled so the job lines stay parseable whatever the caller's environment sets.
    """
    env = {key: value for key, value in os.environ.items() if key != "FORCE_COLOR"}
    env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        raise


def save(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".task-compliance-", delete=False
    ) as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def main() -> int:
    raw_payload: object = json.loads(sys.argv[2] if sys.argv[1] == "--json" else sys.stdin.read())
    payload = cast(dict[str, Any], raw_payload) if isinstance(raw_payload, dict) else {}
    if sys.argv[1] != "--json" or len(sys.argv) != 3:
        raise ValueError("expected review --json <payload>")
    phase = str(payload["phase"])
    if phase not in ("implemented", "verified"):
        raise ValueError("invalid phase")
    artifacts = Path(str(payload["artifact_dir"]))
    tasks = Path(str(payload["tasks_file"]))
    target_head = str(payload["target_head"])
    path = artifacts / "task-compliance.json"
    try:
        raw_old: object = json.loads(path.read_text())
        old = cast(dict[str, Any], raw_old) if isinstance(raw_old, dict) else {}
    except (OSError, json.JSONDecodeError):
        old = {}
    runs = (
        cast(list[dict[str, Any]], old.get("runs", [])) if isinstance(old.get("runs"), list) else []
    )
    merge = artifacts / "base-merge.json"
    target_ref = head = tree = base = digest = ""
    try:
        target_ref = (
            str(cast(dict[str, Any], json.loads(merge.read_text()))["base_head"])
            if merge.exists()
            else target_head
        )
        head = command("git", "rev-parse", "HEAD")
        tree = command("git", "rev-parse", "HEAD^{tree}")
        base = command("git", "merge-base", target_ref, head)
        digest = tasks_hash(tasks)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        # A binding failure is an error that prevents a verdict: record it as not-run with its
        # cause, replacing any earlier verdict, so repair never acts on a stale record and the
        # pull request names the cause.
        reason = f"binding failed: {exc}"
        runs.append(
            {"phase": phase, "head": head, "result": "not-run", "reason": reason, "evidence": ""}
        )
        save(
            path,
            {
                "result": "not-run",
                "reason": reason,
                "phase": phase,
                "target_head": target_head,
                "target_ref": target_ref,
                "base": base,
                "declaring_entry_points": [],
                "uncovered_paths": [],
                "reviewed_head": head,
                "reviewed_tree": tree,
                "tasks_file": str(tasks),
                "tasks_sha256": digest,
                "violations": [],
                "runs": runs,
            },
        )
        print(json.dumps({"result": "not-run", "reason": reason}))
        return 0
    if reusable(old, head, base, target_ref, digest):
        return int(old["result"] == "failed")
    record: dict[str, Any] = {
        "result": "not-run",
        "reason": "",
        "phase": phase,
        "target_head": target_head,
        "target_ref": target_ref,
        "base": base,
        "declaring_entry_points": [],
        "uncovered_paths": [],
        "reviewed_head": head,
        "reviewed_tree": tree,
        "tasks_file": str(tasks),
        "tasks_sha256": digest,
        "violations": [],
        "runs": runs,
    }
    entries: list[str] = []
    try:
        listing = subprocess.run(
            ["agent-validator", "list"], capture_output=True, text=True, timeout=30
        )
        if listing.returncode:
            raise ValueError(listing.stderr.strip() or listing.stdout.strip() or "list failed")
        declared, entries = declarations(listing.stdout)
        record["declaring_entry_points"] = entries
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        declared = False
        record["reason"] = f"validator configuration unreadable: {exc}"
    if not declared and not record["reason"]:
        record["result"] = "not-declared"
        record["reason"] = "target declares no task-compliance review"
    if declared:
        for attempt in range(2):
            evidence = artifacts / "task-compliance" / f"{phase}-{len(runs) + 1}"
            evidence.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="task-compliance-") as temporary:
                clone = Path(temporary) / "review"
                try:
                    command(
                        "git",
                        "clone",
                        "--quiet",
                        "--shared",
                        "--no-checkout",
                        str(Path.cwd()),
                        str(clone),
                    )
                    command("git", "checkout", "--detach", head, cwd=clone)
                    context = Path(temporary) / "tasks.md"
                    shutil.copyfile(tasks, context)
                    stdout, stderr = run_review(
                        [
                            "agent-validator",
                            "review",
                            "--gate",
                            "task-compliance",
                            "--enable-review",
                            "task-compliance",
                            "--context-file",
                            str(context),
                            "--base-branch",
                            target_ref,
                        ],
                        clone,
                        900,
                    )
                    # The validator prints its job lines on stderr; both streams are evidence.
                    output = stdout + "\n" + stderr
                    (evidence / "console.txt").write_text(output)
                    logs = clone / "validator_logs"
                    if logs.exists():
                        shutil.copytree(logs, evidence / "validator_logs", dirs_exist_ok=True)
                    result, reason, violations, dispatched = verdict(logs, output)
                except subprocess.TimeoutExpired:
                    result, reason, violations, dispatched = (
                        "not-run",
                        "review timed out",
                        [],
                        set[str](),
                    )
                    (evidence / "console.txt").write_text(reason)
                except (OSError, ValueError) as exc:
                    result, reason, violations, dispatched = (
                        "not-run",
                        f"review setup failed: {exc}",
                        [],
                        set[str](),
                    )
                    (evidence / "console.txt").write_text(reason)
            if result == "passed":
                covered = [entry for entry in entries if entry in dispatched]
                missing = uncovered_paths(changed_paths(base, head), covered)
                record["uncovered_paths"] = missing
                if missing:
                    result, reason = "not-run", "task-compliance did not see: " + ", ".join(missing)
            if result == "error" and attempt == 0:
                runs.append(
                    {
                        "phase": phase,
                        "head": head,
                        "result": "error",
                        "reason": reason,
                        "evidence": str(evidence),
                    }
                )
                continue
            if result == "error":
                result, reason = "not-run", "review error: " + reason
            record.update(result=result, reason=reason, violations=violations)
            runs.append(
                {
                    "phase": phase,
                    "head": head,
                    "result": result,
                    "reason": reason,
                    "evidence": str(evidence),
                }
            )
            break
    else:
        runs.append(
            {
                "phase": phase,
                "head": head,
                "result": record["result"],
                "reason": record["reason"],
                "evidence": "",
            }
        )
    save(path, record)
    print(json.dumps({"result": record["result"], "reason": record["reason"]}))
    return int(record["result"] == "failed")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"task-compliance-gate: {exc}", file=sys.stderr)
        sys.exit(2)
