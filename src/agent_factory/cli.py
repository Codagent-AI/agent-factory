"""Public process entry points for durable factory operation."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from agent_factory.config import ConfigurationError, LocalConfig
from agent_factory.operations import doctor, format_doctor, status
from agent_factory.store import ClaimStore
from agent_factory.supervisor import SupervisorLaunchError, resume_supervisor


def _tick(state: Path, config_path: Path | None = None) -> None:
    """Reattach short-lived watchers; this never waits for suite completion."""
    store = ClaimStore(state)
    try:
        for run in store.nonterminal_runs():
            if run.status not in {"running", "observing"}:
                continue
            try:
                resume_supervisor(state, run.id, config_path=config_path)
            except SupervisorLaunchError as error:
                store.report_uncertainty(run.id, f"supervisor replacement failed: {error}")
    finally:
        store.close()


def _status(state: Path, config: LocalConfig | None = None) -> str:
    store = ClaimStore(state)
    try:
        return status(store, config)
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent-factory")
    parser.add_argument(
        "--state", type=Path, help="override the SQLite path from local configuration"
    )
    parser.add_argument("--config", type=Path, help="explicit installed local configuration path")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("tick")
    subcommands.add_parser("status")
    subcommands.add_parser("doctor")
    subcommands.add_parser("pause")
    subcommands.add_parser("resume")
    resident = subcommands.add_parser("resident")
    resident.add_argument("--poll-seconds", type=_positive_seconds, default=300)
    args = parser.parse_args()
    local = _load_local(args.config, required=args.state is None)
    if args.state is None:
        if local is None:  # pragma: no cover - _load_local exits in this case
            raise RuntimeError("local configuration is required")
        state = local.state_path
    else:
        state = args.state
    if args.command == "doctor":
        if local is None:
            parser.error("doctor requires --config")
        diagnostics = doctor(local)
        print(format_doctor(diagnostics))
        raise SystemExit(0 if all(item.available for item in diagnostics) else 1)
    if args.command == "tick":
        _tick(state, args.config)
    elif args.command == "status":
        print(_status(state, local))
    elif args.command in {"pause", "resume"}:
        store = ClaimStore(state)
        try:
            store.set_paused(args.command == "pause")
        finally:
            store.close()
        print(f"paused: {str(args.command == 'pause').lower()}")
    else:
        keep_running = True

        def stop(_signum: int, _frame: object) -> None:
            nonlocal keep_running
            keep_running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while keep_running:
            _tick(state, args.config)
            time.sleep(local.schedule.poll_seconds if local is not None else args.poll_seconds)


def _load_local(path: Path | None, *, required: bool) -> LocalConfig | None:
    if path is None:
        if required:
            raise SystemExit("agent-factory requires --config (or an explicit --state)")
        return None
    try:
        return LocalConfig.from_file(path)
    except ConfigurationError as error:
        raise SystemExit(f"invalid local configuration: {error}") from error


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("poll seconds must be a number") from error
    if seconds <= 0:
        raise argparse.ArgumentTypeError("poll seconds must be greater than zero")
    return seconds


if __name__ == "__main__":
    main()
