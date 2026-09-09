"""Public process entry points for durable factory operation."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from agent_factory.store import ClaimStore
from agent_factory.supervisor import resume_supervisor


def _tick(state: Path) -> None:
    """Reattach short-lived watchers; this never waits for suite completion."""
    store = ClaimStore(state)
    try:
        for run in store.nonterminal_runs():
            resume_supervisor(state, run.id)
    finally:
        store.close()


def _status(state: Path) -> str:
    store = ClaimStore(state)
    try:
        paused = store.is_paused()
        active_runs = store.nonterminal_runs()
    finally:
        store.close()
    lines = [f"paused: {str(paused).lower()}"]
    for run in active_runs:
        lines.append(f"active: {run.unit_key} ({run.status})")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-factory")
    parser.add_argument("--state", type=Path, required=True, help="explicit SQLite state path")
    parser.add_argument("--config", type=Path, help="explicit installed local configuration path")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("tick")
    subcommands.add_parser("status")
    subcommands.add_parser("pause")
    subcommands.add_parser("resume")
    resident = subcommands.add_parser("resident")
    resident.add_argument("--poll-seconds", type=float, default=300)
    args = parser.parse_args()
    if args.command == "tick":
        _tick(args.state)
    elif args.command == "status":
        print(_status(args.state))
    elif args.command in {"pause", "resume"}:
        store = ClaimStore(args.state)
        try:
            store.set_paused(args.command == "pause")
        finally:
            store.close()
    else:
        keep_running = True

        def stop(_signum: int, _frame: object) -> None:
            nonlocal keep_running
            keep_running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while keep_running:
            _tick(args.state)
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
