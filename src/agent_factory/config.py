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


_SHA = re.compile(r"^[0-9a-f]{40}$")


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


@dataclass(frozen=True)
class EvalConfig:
    harness_sha: str
    suite: str
    repetitions: int
    defaults: Mapping[str, object] = field(default_factory=lambda: dict[str, object]())


@dataclass(frozen=True)
class RepositoryConfig:
    agent_evals: Path
    agent_runner: Path
    agent_skills: Path


@dataclass(frozen=True)
class ScheduleConfig:
    timezone: ZoneInfo
    poll_seconds: int
    start_hour: int
    stop_hour: int

    def allows_admission(self, now: datetime) -> bool:
        """Check the configured daytime or overnight window, excluding its stop hour."""
        if self.start_hour < self.stop_hour:
            return self.start_hour <= now.hour < self.stop_hour
        return now.hour >= self.start_hour or now.hour < self.stop_hour


@dataclass(frozen=True)
class LimitsConfig:
    minimum_free_gib: int
    inactivity_seconds: int
    execution_seconds: int
    total_seconds: int
    codex_reset_fallback_seconds: int


@dataclass(frozen=True)
class CredentialsConfig:
    github_app_key: Path
    suite_environment: Path


@dataclass(frozen=True)
class LocalConfig:
    """Machine-specific paths and limits, deliberately separate from deployment TOML."""

    shared_config: Path
    storage_root: Path
    repositories: RepositoryConfig
    schedule: ScheduleConfig
    limits: LimitsConfig
    credentials: CredentialsConfig

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
        except ZoneInfoNotFoundError as error:
            raise ConfigurationError(f"schedule.timezone is unknown: {timezone_name}") from error
        poll_seconds = (
            _positive_int(schedule, "poll_seconds", "schedule")
            if "poll_seconds" in schedule
            else _positive_int(schedule, "poll_minutes", "schedule") * 60
        )
        start_hour = _hour(schedule, "start_hour")
        stop_hour = _hour(schedule, "stop_hour")
        if start_hour == stop_hour:
            raise ConfigurationError("schedule start_hour and stop_hour must differ")
        return cls(
            shared_config=_path(document, "shared_config", "local configuration"),
            storage_root=_path(document, "storage_root", "local configuration"),
            repositories=RepositoryConfig(
                agent_evals=_path(repositories, "agent_evals", "repositories"),
                agent_runner=_path(repositories, "agent_runner", "repositories"),
                agent_skills=_path(repositories, "agent_skills", "repositories"),
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
            ),
            credentials=CredentialsConfig(
                github_app_key=_path(credentials, "github_app_key", "credentials"),
                suite_environment=_path(credentials, "suite_environment", "credentials"),
            ),
        )


@dataclass(frozen=True)
class SharedConfig:
    organization: str
    app_id: str
    installation_id: str
    project: ProjectConfig
    routing: RoutingConfig
    eval: EvalConfig
    bot_login: str = ""

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
        harness_sha = _string(eval_config, "harness_sha", "eval")
        if not _SHA.fullmatch(harness_sha):
            raise ConfigurationError("eval.harness_sha must be a full 40-character commit SHA")
        repetitions = _positive_int(eval_config, "repetitions", "eval")
        project_number = _positive_int(project, "number", "project")
        return cls(
            organization=_string(github, "organization", "github"),
            bot_login=str(github.get("bot_login", "")),
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
                eval_source=_string(routing, "eval_source", "routing"),
                general_sources=frozenset(source_repositories),
                eval_label=_string(routing, "eval_label", "routing"),
                eval_type=_string(routing, "eval_type", "routing"),
            ),
            eval=EvalConfig(
                harness_sha=harness_sha,
                suite=_string(eval_config, "suite", "eval"),
                repetitions=repetitions,
                defaults=dict(_table(eval_config.get("defaults", {}), "eval.defaults")),
            ),
        )


def _select_field(fields: Mapping[str, Any], name: str) -> SelectField:
    field = _table(fields.get(name), f"fields.{name}")
    options = _table(field.get("options"), f"fields.{name}.options")
    parsed_options = {key: _string(options, key, f"fields.{name}.options") for key in options}
    return SelectField(id=_string(field, "id", f"fields.{name}"), options=parsed_options)
