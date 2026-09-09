"""Versioned deployment configuration for the factory boundary."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


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
class ProjectConfig:
    id: str
    number: int
    status: SelectField
    owner: SelectField


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


@dataclass(frozen=True)
class SharedConfig:
    organization: str
    app_id: str
    installation_id: str
    project: ProjectConfig
    routing: RoutingConfig
    eval: EvalConfig

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
        repetitions = eval_config.get("repetitions")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
            raise ConfigurationError("eval.repetitions must be a positive integer")
        project_number = project.get("number")
        if (
            isinstance(project_number, bool)
            or not isinstance(project_number, int)
            or project_number < 1
        ):
            raise ConfigurationError("project.number must be a positive integer")
        return cls(
            organization=_string(github, "organization", "github"),
            app_id=_string(github, "app_id", "github"),
            installation_id=_string(github, "installation_id", "github"),
            project=ProjectConfig(
                id=_string(project, "id", "project"),
                number=project_number,
                status=_select_field(fields, "status"),
                owner=_select_field(fields, "owner"),
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
            ),
        )


def _select_field(fields: Mapping[str, Any], name: str) -> SelectField:
    field = _table(fields.get(name), f"fields.{name}")
    options = _table(field.get("options"), f"fields.{name}.options")
    parsed_options = {key: _string(options, key, f"fields.{name}.options") for key in options}
    return SelectField(id=_string(field, "id", f"fields.{name}"), options=parsed_options)
