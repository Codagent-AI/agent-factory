"""Poll the live Agent Factory until it stops making progress on the given issues.

    python3 watch.py OWNER/REPO#N [OWNER/REPO#N ...] [--poll 60] [--settle 360] [--max-hours 12]

An issue is progressing while any of these hold:
- a run of its latest claim has not finished;
- a watch dispatch for that run is pending or launched;
- that run ended with an event the service watcher should pick up (PR-READY, FAILURE)
  and no dispatch exists yet, within the detection window;
- its card is queued: open, Owner=factory, Status=Ready, no needs-input label.

It stops once none holds for --settle seconds, which covers the resident's gaps
between ticks. Each state change prints one line; each stop prints a STOPPED line.
The script exits 0 when every issue has stopped, and 2 after --max-hours.
It is read-only: sqlite reads and GitHub reads through Paul's gh login.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB = Path("~/.agent-factory/state.sqlite3").expanduser()
FAILURES = {"failed", "interrupted", "cancelled", "timed_out"}
OPEN_DISPATCH = {"pending", "launched"}
# FAILURE waits for the grace period (7 minutes) and result consumption; allow a few ticks.
DETECTION_WINDOW = timedelta(minutes=25)
CARD_TTL = timedelta(minutes=3)
REF = re.compile(r"(?:https://github\.com/)?([\w.-]+/[\w.-]+)(?:#|/issues/)(\d+)")


def now() -> datetime:
    return datetime.now(UTC)


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if value else None


def query(sql: str, args: tuple) -> list[dict]:
    # Not -readonly: the database uses WAL (see factory-assign).
    with sqlite3.connect(DB, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, args)]


def card(repository: str, number: int) -> dict:
    owner, name = repository.split("/")
    graphql = (
        "query($o:String!,$n:String!,$i:Int!){repository(owner:$o,name:$n){issue(number:$i){"
        "state labels(first:20){nodes{name}} projectItems(first:5){nodes{fieldValues(first:20){"
        "nodes{... on ProjectV2ItemFieldSingleSelectValue{name field{"
        "... on ProjectV2SingleSelectField{name}}}}}}}}}}"
    )
    completed = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={graphql}",
            "-f",
            f"o={owner}",
            "-f",
            f"n={name}",
            "-F",
            f"i={number}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip()[:200]}
    issue = json.loads(completed.stdout)["data"]["repository"]["issue"] or {}
    fields: dict[str, str] = {}
    for item in issue.get("projectItems", {}).get("nodes", []):
        for value in item["fieldValues"]["nodes"]:
            if value.get("field", {}).get("name"):
                fields[value["field"]["name"]] = value["name"]
    return {
        "state": issue.get("state"),
        "labels": sorted(label["name"] for label in issue.get("labels", {}).get("nodes", [])),
        "status": fields.get("Status"),
        "owner": fields.get("Owner"),
    }


class Watched:
    def __init__(self, repository: str, number: int, started: datetime) -> None:
        self.repository, self.number, self.started = repository, number, started
        self.name = f"{repository}#{number}"
        self.signature = ""
        self.idle_since: datetime | None = None
        self.stopped = False
        self._card: dict = {}
        self._card_at: datetime | None = None

    def card(self) -> dict:
        if self._card_at is None or now() - self._card_at > CARD_TTL:
            self._card, self._card_at = card(self.repository, self.number), now()
        return self._card

    def observe(self) -> tuple[bool, str]:
        claims = query(
            "SELECT id, kind, lifecycle FROM claim WHERE repository=? AND issue_number=? "
            "ORDER BY created_at DESC LIMIT 1",
            (self.repository, self.number),
        )
        claim = claims[0] if claims else None
        parts: list[str] = []
        busy: list[str] = []
        if claim:
            parts.append(f"claim {claim['id'][:8]} {claim['kind']} {claim['lifecycle']}")
            runs = query(
                "SELECT id, kind, reason, status, finished_at, result_json FROM run "
                "WHERE claim_id=? ORDER BY created_at DESC",
                (claim["id"],),
            )
            if any(run["finished_at"] is None for run in runs):
                busy.append("run unfinished")
            if runs:
                run = runs[0]
                result = json.loads(run["result_json"] or "{}")
                outcome = result.get("outcome")
                parts.append(f"run {run['reason']} {run['status']}")
                if outcome:
                    parts[-1] += f" outcome={outcome}"
                dispatches = query(
                    "SELECT id, event_kind, state, detail FROM watch_dispatch WHERE run_id=? "
                    "ORDER BY created_at",
                    (run["id"],),
                )
                for dispatch in dispatches:
                    parts.append(
                        f"watch {dispatch['event_kind']} {dispatch['id'][:8]} {dispatch['state']}"
                    )
                    if dispatch["state"] in OPEN_DISPATCH:
                        busy.append("watch session open")
                finished = parse_time(run["finished_at"])
                expected = run["status"] in FAILURES or (
                    run["kind"] in {"fix", "feature"}
                    and run["status"] == "completed"
                    and outcome == "pull-request"
                )
                recent = finished is not None and finished >= self.started - DETECTION_WINDOW
                if expected and not dispatches and recent:
                    if now() - finished < DETECTION_WINDOW:
                        busy.append("awaiting watcher detection")
                    else:
                        parts.append("WATCH EVENT MISSING")
        else:
            parts.append("no claim")
        if not busy:
            current = self.card()
            if "error" in current:
                parts.append(f"card read failed: {current['error']}")
                busy.append("card unknown")
            else:
                parts.append(
                    f"card {current['status']}/{current['owner']} {current['state']}"
                    + (f" labels={','.join(current['labels'])}" if current["labels"] else "")
                )
                if (
                    current["state"] == "OPEN"
                    and current["status"] == "Ready"
                    and current["owner"] == "factory"
                    and "needs-input" not in current["labels"]
                ):
                    busy.append("queued in Ready")
        return bool(busy), "; ".join(parts + ([f"[{', '.join(busy)}]"] if busy else []))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("issues", nargs="+")
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--settle", type=int, default=360)
    parser.add_argument("--max-hours", type=float, default=12)
    options = parser.parse_args()
    started = now()
    watched = []
    for text in options.issues:
        match = REF.fullmatch(text.strip())
        if not match:
            print(f"cannot parse issue reference: {text}", file=sys.stderr)
            return 1
        watched.append(Watched(match[1], int(match[2]), started))
    deadline = started + timedelta(hours=options.max_hours)
    while True:
        for item in watched:
            if item.stopped:
                continue
            try:
                progressing, signature = item.observe()
            except (sqlite3.Error, json.JSONDecodeError, KeyError) as error:
                progressing, signature = True, f"read error: {error}"
            stamp = now().strftime("%H:%M:%SZ")
            if signature != item.signature:
                print(f"{stamp} {item.name}: {signature}", flush=True)
                item.signature = signature
            if progressing:
                item.idle_since = None
            elif item.idle_since is None:
                item.idle_since = now()
            elif (now() - item.idle_since).total_seconds() >= options.settle:
                item.stopped = True
                print(f"{stamp} STOPPED {item.name}: {signature}", flush=True)
        if all(item.stopped for item in watched):
            print("all watched issues stopped", flush=True)
            return 0
        if now() >= deadline:
            print(
                f"TIMEOUT after {options.max_hours}h; still progressing: "
                + ", ".join(item.name for item in watched if not item.stopped),
                flush=True,
            )
            return 2
        time.sleep(options.poll)


if __name__ == "__main__":
    sys.exit(main())
