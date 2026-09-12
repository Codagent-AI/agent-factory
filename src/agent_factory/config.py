"""Versioned deployment configuration for the factory boundary."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(ValueError):
    """Raised when a deployment configuration is incomplete or unsafe."""


_COMMIT_ID = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _table(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{name} must be a TOML table")
    return cast(Mapping[str, Any], value)


def _string(table: Mapping[str, Any], key: str, section: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{section}.{key} must be a non-empty string")
    return value


def _path(table: Mapping[str, Any], key: str, section: str) -> Path:
    return Path(_string(table, key, section)).expanduser().resolve()


def _positive_int(table: Mapping[str, Any], key: str, section: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigurationError(f"{section}.{key} must be a positive integer")
    return value


def _nonnegative_int(table: Mapping[str, Any], key: str, section: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError(f"{section}.{key} must be a non-negative integer")
    return value


def _hour(table: Mapping[str, Any], key: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 23:
        raise ConfigurationError(f"schedule.{key} must be an hour from 0 through 23")
    return value


@dataclass(frozen=True)
class SelectField:
    id: str
    options: Mapping[str, str]

    def option(self, logical_name: str) -> str:
        try:
            return self.options[logical_name]
        except KeyError as error:
            raise ConfigurationError(f"missing option mapping for {logical_name}") from error


@dataclass(frozen=True)
class TextField:
    id: str


@dataclass(frozen=True)
class ProjectConfig:
    id: str
    number: int
    status: SelectField
    owner: SelectField
    refs: TextField
    verdict: SelectField


@dataclass(frozen=True)
class RoutingConfig:
    eval_source: str
    general_sources: frozenset[str]
    eval_label: str
    eval_type: str
    bug_type: str = "Bug"
    hold_label: str = "factory-hold"


@dataclass(frozen=True)
class EvalConfig:
    harness_ref: str
    suite: str
    repetitions: int
    defaults: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True)
class RepositoryConfig:
    agent_evals: Path
    agent_runner: Path
    agent_skills: Path
    working_clones: Mapping[str, Path] = field(default_factory=lambda: dict[str, Path]())


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: ZoneInfo
    poll_seconds: int
    start_hour: int
    stop_hour: int
    always_open: bool = False

    def allows_admission(self, now: datetime) -> bool:
        """Check the configured daytime or overnight window, excluding its stop hour."""
        if self.always_open:
            return True
        if self.start_hour < self.stop_hour:
            return self.start_hour <= now.hour < self.stop_hour
        return now.hour >= self.start_hour or now.hour < self.stop_hour

    @classmethod
    def always(cls, timezone: ZoneInfo, poll_seconds: int) -> ScheduleConfig:
        return cls(timezone, poll_seconds, 0, 0, always_open=True)


@dataclass(frozen=True)
class LimitsConfig:
    minimum_free_gib: int
    inactivity_seconds: int
    execution_seconds: int
    total_seconds: int
    codex_reset_fallback_seconds: int
    memory_reservation_gib: int = 3


@dataclass(frozen=True)
class CredentialsConfig:
    github_app_key: Path
    suite_environment: Path
    fix_environment: Path | None = None


@dataclass(frozen=True)
class FixLimitsConfig:
    inactivity_seconds: int = 900
    execution_seconds: int = 7200
    total_seconds: int = 10800


@dataclass(frozen=True)
class FixLocalConfig:
    """Machine-local fix settings; the window matters only when a fix role selects Codex."""

    limits: FixLimitsConfig = field(default_factory=FixLimitsConfig)
    schedule: ScheduleConfig | None = None


@dataclass(frozen=True)
class LocalConfig:
    """Machine-specific paths and limits, deliberately separate from deployment TOML."""

    shared_config: Path
    storage_root: Path
    repositories: RepositoryConfig
    schedule: ScheduleConfig
    limits: LimitsConfig
    credentials: CredentialsConfig
    fix: FixLocalConfig = field(default_factory=FixLocalConfig)

    @property
    def state_path(self) -> Path:
        return self.storage_root / "state.sqlite3"

    @property
    def log_path(self) -> Path:
        return self.storage_root / "logs" / "controller.log"

    @classmethod
    def from_file(cls, path: Path) -> LocalConfig:
        try:
            return cls.from_toml(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigurationError(f"cannot read local configuration {path}: {error}") from error

    @classmethod
    def from_toml(cls, text: str) -> LocalConfig:
        try:
            document = cast(Mapping[str, Any], tomllib.loads(text))
        except tomllib.TOMLDecodeError as error:
            raise ConfigurationError(f"invalid TOML: {error}") from error
        repositories = _table(document.get("repositories"), "repositories")
        schedule = _table(document.get("schedule"), "schedule")
        limits = _table(document.get("limits"), "limits")
        credentials = _table(document.get("credentials"), "credentials")
        timezone_name = _string(schedule, "timezone", "schedule")
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConfigurationError(
                f"schedule.timezone is invalid or unknown: {timezone_name}"
            ) from error
        poll_seconds = (
            _positive_int(schedule, "poll_seconds", "schedule")
            if "poll_seconds" in schedule
            else _positive_int(schedule, "poll_minutes", "schedule") * 60
        )
        start_hour = _hour(schedule, "start_hour")
        stop_hour = _hour(schedule, "stop_hour")
        if start_hour == stop_hour:
            raise ConfigurationError("schedule start_hour and stop_hour must differ")
        working_clones_raw = _table(
            repositories.get("working_clones", {}), "repositories.working_clones"
        )
        working_clones = {
            key: _path(working_clones_raw, key, "repositories.working_clones")
            for key in working_clones_raw
        }
        fix_environment_value = credentials.get("fix_environment")
        fix_environment = (
            _path(credentials, "fix_environment", "credentials")
            if fix_environment_value is not None
            else None
        )
        return cls(
            shared_config=_path(document, "shared_config", "local configuration"),
            storage_root=_path(document, "storage_root", "local configuration"),
            repositories=RepositoryConfig(
                agent_evals=_path(repositories, "agent_evals", "repositories"),
                agent_runner=_path(repositories, "agent_runner", "repositories"),
                agent_skills=_path(repositories, "agent_skills", "repositories"),
                working_clones=working_clones,
            ),
            schedule=ScheduleConfig(timezone, poll_seconds, start_hour, stop_hour),
            limits=LimitsConfig(
                minimum_free_gib=_nonnegative_int(limits, "minimum_free_gib", "limits"),
                inactivity_seconds=_positive_int(limits, "inactivity_seconds", "limits"),
                execution_seconds=_positive_int(limits, "execution_seconds", "limits"),
                total_seconds=_positive_int(limits, "total_seconds", "limits"),
                codex_reset_fallback_seconds=_positive_int(
                    limits, "codex_reset_fallback_seconds", "limits"
                ),
                memory_reservation_gib=(
                    _positive_int(limits, "memory_reservation_gib", "limits")
                    if "memory_reservation_gib" in limits
                    else 3
                ),
            ),
            credentials=CredentialsConfig(
                github_app_key=_path(credentials, "github_app_key", "credentials"),
                suite_environment=_path(credentials, "suite_environment", "credentials"),
                fix_environment=fix_environment,
            ),
            fix=_fix_local_config(document.get("fix")),
        )


@dataclass(frozen=True)
class FixTarget:
    repository: str
    branch: str = "main"


@dataclass(frozen=True)
class FixBranches:
    runner: str = "main"
    skills: str = "main"


@dataclass(frozen=True)
class FixConfig:
    targets: tuple[FixTarget, ...] = ()
    branches: FixBranches = field(default_factory=FixBranches)
    defaults: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    contract: str = "factory-fix/1"


@dataclass(frozen=True)
class SharedConfig:
    organization: str
    app_id: str
    installation_id: str
    project: ProjectConfig
    routing: RoutingConfig
    eval: EvalConfig
    bot_login: str = ""
    fix: FixConfig = field(default_factory=FixConfig)

    @classmethod
    def from_file(cls, path: Path) -> SharedConfig:
        return cls.from_toml(path.read_text(encoding="utf-8"))

    @classmethod
    def from_toml(cls, text: str) -> SharedConfig:
        try:
            document = cast(Mapping[str, Any], tomllib.loads(text))
        except tomllib.TOMLDecodeError as error:
            raise ConfigurationError(f"invalid TOML: {error}") from error

        github = _table(document.get("github"), "github")
        project = _table(document.get("project"), "project")
        fields = _table(document.get("fields"), "fields")
        routing = _table(document.get("routing"), "routing")
        eval_config = _table(document.get("eval"), "eval")
        sources = routing.get("general_sources")
        if not isinstance(sources, list):
            raise ConfigurationError("routing.general_sources must be a list of repositories")
        source_repositories: list[str] = []
        for value in cast(list[object], sources):
            if not isinstance(value, str) or not value:
                raise ConfigurationError("routing.general_sources must be a list of repositories")
            source_repositories.append(value)
        eval_source = _string(routing, "eval_source", "routing")
        if eval_source not in source_repositories:
            raise ConfigurationError(
                "routing.eval_source must be included in routing.general_sources"
            )
        if "harness_sha" in eval_config:
            raise ConfigurationError(
                "eval.harness_sha is obsolete; configure eval.harness_ref (a branch name) instead"
            )
        harness_ref = eval_config.get("harness_ref", "main")
        if not isinstance(harness_ref, str) or not harness_ref:
            raise ConfigurationError("eval.harness_ref must be a non-empty string")
        if _COMMIT_ID.fullmatch(harness_ref):
            raise ConfigurationError("eval.harness_ref must be a branch name, not a commit SHA")
        repetitions = _positive_int(eval_config, "repetitions", "eval")
        project_number = _positive_int(project, "number", "project")
        return cls(
            organization=_string(github, "organization", "github"),
            bot_login=_string(github, "bot_login", "github"),
            app_id=_string(github, "app_id", "github"),
            installation_id=_string(github, "installation_id", "github"),
            project=ProjectConfig(
                id=_string(project, "id", "project"),
                number=project_number,
                status=_select_field(fields, "status"),
                owner=_select_field(fields, "owner"),
                refs=TextField(
                    _string(_table(fields.get("refs"), "fields.refs"), "id", "fields.refs")
                ),
                verdict=_select_field(fields, "verdict"),
            ),
            routing=RoutingConfig(
                eval_source=eval_source,
                general_sources=frozenset(source_repositories),
                eval_label=_string(routing, "eval_label", "routing"),
                eval_type=_string(routing, "eval_type", "routing"),
                bug_type=(
                    _string(routing, "bug_type", "routing") if "bug_type" in routing else "Bug"
                ),
                hold_label=(
                    _string(routing, "hold_label", "routing")
                    if "hold_label" in routing
                    else "factory-hold"
                ),
            ),
            eval=EvalConfig(
                harness_ref=harness_ref,
                suite=_string(eval_config, "suite", "eval"),
                repetitions=repetitions,
                defaults=dict(_table(eval_config.get("defaults", {}), "eval.defaults")),
            ),
            fix=_fix_shared_config(document.get("fix")),
        )


def _select_field(fields: Mapping[str, Any], name: str) -> SelectField:
    field = _table(fields.get(name), f"fields.{name}")
    options = _table(field.get("options"), f"fields.{name}.options")
    parsed_options = {key: _string(options, key, f"fields.{name}.options") for key in options}
    return SelectField(id=_string(field, "id", f"fields.{name}"), options=parsed_options)


def _fix_shared_config(raw: object) -> FixConfig:
    if raw is None:
        return FixConfig()
    fix = _table(raw, "fix")
    targets_raw = fix.get("targets", [])
    if not isinstance(targets_raw, list):
        raise ConfigurationError("fix.targets must be a list of tables")
    targets: list[FixTarget] = []
    for entry in cast(list[object], targets_raw):
        target = _table(entry, "fix.targets")
        branch = _string(target, "branch", "fix.targets") if "branch" in target else "main"
        targets.append(
            FixTarget(repository=_string(target, "repository", "fix.targets"), branch=branch)
        )
    branches_raw = _table(fix.get("branches", {}), "fix.branches")
    branches = FixBranches(
        runner=(
            _string(branches_raw, "runner", "fix.branches") if "runner" in branches_raw else "main"
        ),
        skills=(
            _string(branches_raw, "skills", "fix.branches") if "skills" in branches_raw else "main"
        ),
    )
    defaults_raw = _table(fix.get("defaults", {}), "fix.defaults")
    defaults = {key: str(value) for key, value in defaults_raw.items()}
    contract = fix.get("contract", "factory-fix/1")
    if not isinstance(contract, str) or not contract:
        raise ConfigurationError("fix.contract must be a non-empty string")
    return FixConfig(
        targets=tuple(targets), branches=branches, defaults=defaults, contract=contract
    )


def _fix_local_config(raw: object) -> FixLocalConfig:
    if raw is None:
        return FixLocalConfig()
    fix = _table(raw, "fix")
    limits_raw = _table(fix.get("limits", {}), "fix.limits")
    limits = FixLimitsConfig(
        inactivity_seconds=(
            _positive_int(limits_raw, "inactivity_seconds", "fix.limits")
            if "inactivity_seconds" in limits_raw
            else 900
        ),
        execution_seconds=(
            _positive_int(limits_raw, "execution_seconds", "fix.limits")
            if "execution_seconds" in limits_raw
            else 7200
        ),
        total_seconds=(
            _positive_int(limits_raw, "total_seconds", "fix.limits")
            if "total_seconds" in limits_raw
            else 10800
        ),
    )
    schedule_raw = fix.get("schedule")
    schedule: ScheduleConfig | None = None
    if schedule_raw is not None:
        schedule_table = _table(schedule_raw, "fix.schedule")
        timezone_name = _string(schedule_table, "timezone", "fix.schedule")
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ConfigurationError(
                f"fix.schedule.timezone is invalid or unknown: {timezone_name}"
            ) from error
        poll_seconds = (
            _positive_int(schedule_table, "poll_seconds", "fix.schedule")
            if "poll_seconds" in schedule_table
            else 300
        )
        if "start_hour" in schedule_table or "stop_hour" in schedule_table:
            start_hour = _hour(schedule_table, "start_hour")
            stop_hour = _hour(schedule_table, "stop_hour")
            if start_hour == stop_hour:
                raise ConfigurationError("fix.schedule start_hour and stop_hour must differ")
            schedule = ScheduleConfig(timezone, poll_seconds, start_hour, stop_hour)
        else:
            schedule = ScheduleConfig.always(timezone, poll_seconds)
    return FixLocalConfig(limits=limits, schedule=schedule)
