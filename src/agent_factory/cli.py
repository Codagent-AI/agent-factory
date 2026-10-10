"""Public process entry points for durable factory operation."""

from __future__ import annotations

import argparse
import signal
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from agent_factory.config import ConfigurationError, JobCapConfig, LocalConfig, SharedConfig
from agent_factory.operations import Diagnostic, doctor, format_doctor, status
from agent_factory.store import TERMINAL_LIFECYCLES, ClaimStore
from agent_factory.suites.and_scene import inputs
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
    if config_path is not None:
        from agent_factory.runtime import cycle

        cycle(state, config_path)


def _status(state: Path, config: LocalConfig | None = None, *, include_all: bool = False) -> str:
    store = ClaimStore(state)
    try:
        return status(store, config, include_all=include_all)
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
    subcommands.add_parser("honored-revisions")
    lanes = subcommands.add_parser("lanes").add_subparsers(dest="lanes_command", required=True)
    lanes.add_parser("supported")
    lanes.add_parser("enable")
    lanes.add_parser("downgrade").add_argument("--check", action="store_true")
    pinned = subcommands.add_parser("pinned-claims")
    pinned.add_argument("--revision", required=True)
    status_parser = subcommands.add_parser("status")
    status_parser.add_argument(
        "--all", action="store_true", help="list every saved claim, including settled ones"
    )
    subcommands.add_parser("doctor")
    subcommands.add_parser("pause")
    subcommands.add_parser("resume")
    job_cap_parser = subcommands.add_parser("job-cap")
    job_cap_parser.add_subparsers(dest="job_cap_command", required=True).add_parser("reset")
    watch_parser = subcommands.add_parser("watch")
    watch_commands = watch_parser.add_subparsers(dest="watch_command", required=True)
    watch_commands.add_parser("redispatch").add_argument("dispatch_id")
    resident = subcommands.add_parser("resident")
    resident.add_argument("--poll-seconds", type=_positive_seconds)
    args = parser.parse_args()
    if args.command == "lanes" and args.lanes_command == "supported":
        print("priority-lanes")
        return
    if args.command == "honored-revisions":
        print("\n".join(inputs.honored_revisions()))
        return
    if args.command == "doctor":
        if args.config is None:
            parser.error("doctor requires --config")
        diagnostics = _doctor_diagnostics(args.config)
        print(format_doctor(diagnostics))
        raise SystemExit(0 if all(item.available for item in diagnostics) else 1)
    local = _load_local(args.config, required=args.state is None)
    if args.state is None:
        if local is None:  # pragma: no cover - _load_local exits in this case
            raise RuntimeError("local configuration is required")
        state = local.state_path
    else:
        state = args.state
    if args.command == "lanes":
        with closing(
            ClaimStore(state, read_only=args.lanes_command == "downgrade" and args.check)
        ) as store:
            if args.lanes_command == "enable":
                store.enable_lanes()
            else:
                offenders = store.restore_kind_guard(args.check)
                for run in offenders:
                    claim = store.get_claim(run.claim_id)
                    if claim is not None:
                        print(
                            f"{run.kind}: {claim.id} {claim.repository}#{claim.issue_number} "
                            f"{run.lane or 'all'}"
                        )
                raise SystemExit(1 if offenders else 0)
    elif args.command == "tick":
        _tick(state, args.config)
    elif args.command == "pinned-claims":
        with closing(ClaimStore(state, read_only=True)) as store:
            for claim in store.all_claims():
                revisions = claim.frozen_spec.get("revisions")
                if (
                    claim.kind == "eval"
                    and claim.lifecycle not in TERMINAL_LIFECYCLES
                    and isinstance(revisions, dict)
                    and args.revision in revisions
                ):
                    print(f"{claim.id}\t{claim.repository}#{claim.issue_number}")
    elif args.command == "watch":
        from agent_factory.watch.store import redispatch

        with closing(ClaimStore(state)) as store:
            try:
                new_id = redispatch(store, args.dispatch_id)
            except ValueError as error:
                print(str(error))
                raise SystemExit(2) from error
        print(new_id)
        if local is None or not SharedConfig.from_file(local.shared_config).watch.enabled:
            print("waits until watching is enabled")
    elif args.command == "status":
        print(_status(state, local, include_all=args.all))
    elif args.command == "job-cap":
        cap = SharedConfig.from_file(local.shared_config).job_cap if local else JobCapConfig()
        with closing(ClaimStore(state, job_cap=cap)) as store:
            now = datetime.now(UTC)
            before = store.job_cap_state(now)
            store.set_setting("job-cap", "reset", {"at": now.isoformat()})
            after = store.job_cap_state(now)
            print(
                f"job cap reset at {now.isoformat()}; {after.count}/{after.attempts} "
                f"attempts in the last {after.window_hours} h"
            )
            if before.reached:
                print("held work can start in the next cycle")
    elif args.command in {"pause", "resume"}:
        store = ClaimStore(state)
        try:
            store.set_paused(args.command == "pause")
        finally:
            store.close()
        print(f"paused: {str(args.command == 'pause').lower()}")
    else:
        with closing(ClaimStore(state)) as store:
            store.enable_lanes()
        keep_running = True

        def stop(_signum: int, _frame: object) -> None:
            nonlocal keep_running
            keep_running = False

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while keep_running:
            _tick(state, args.config)
            time.sleep(_poll_seconds(args.poll_seconds, local))


def _doctor_diagnostics(config_path: Path) -> list[Diagnostic]:
    """A broken local file must still yield a grouped doctor report, not a bare exit."""
    try:
        local = LocalConfig.from_file(config_path)
    except ConfigurationError as error:
        return [
            Diagnostic(
                "local configuration",
                False,
                str(error),
                "Correct the local configuration file, then rerun doctor.",
                group="shared",
            )
        ]
    return doctor(local)


def _load_local(path: Path | None, *, required: bool) -> LocalConfig | None:
    if path is None:
        if required:
            raise SystemExit("agent-factory requires --config (or an explicit --state)")
        return None
    try:
        return LocalConfig.from_file(path)
    except ConfigurationError as error:
        raise SystemExit(f"invalid local configuration: {error}") from error


def _poll_seconds(override: float | None, config: LocalConfig | None) -> float:
    if override is not None:
        return override
    if config is not None:
        return config.schedule.poll_seconds
    return 300


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
