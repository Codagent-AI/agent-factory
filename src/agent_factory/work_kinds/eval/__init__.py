"""Eval request parsing, immutable freezing, and result aggregation."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

_BLOCK = re.compile(r"```eval[ \t]*\n(.*?)\n```", re.DOTALL)
_ROLES = frozenset({"lead", "implementor", "tester"})
_KEYS = frozenset(
    {
        "agent_runner_ref",
        "agent_skills_ref",
        "lead",
        "implementor",
        "tester",
        "skip_validator",
        "repetitions",
    }
)


@dataclass(frozen=True)
class EvalDefaults:
    agent_runner_ref: str
    agent_skills_ref: str
    roles: Mapping[str, str]
    skip_validator: bool
    repetitions: int
    max_repetitions: int | None = None


@dataclass(frozen=True)
class FrozenSpec:
    version: int
    payload: dict[str, object]


@dataclass(frozen=True)
class ParsedRequest:
    overrides: dict[str, object]
    settings: dict[str, object]
    fingerprint: str

    def freeze(
        self, *, runner_sha: str, skills_sha: str, harness_sha: str, suite: str
    ) -> FrozenSpec:
        _sha(runner_sha, "runner")
        _sha(skills_sha, "skills")
        _sha(harness_sha, "harness")
        return FrozenSpec(
            1,
            {
                "version": 1,
                "suite": suite,
                "settings": self.settings,
                "revisions": {"runner": runner_sha, "skills": skills_sha, "evals": harness_sha},
            },
        )


def parse_request(body: str, defaults: EvalDefaults) -> ParsedRequest:
    matches = _BLOCK.findall(body)
    if len(matches) != 1:
        raise ValueError("request must contain exactly one fenced eval TOML block")
    try:
        document = tomllib.loads(matches[0])
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid eval TOML: {error}") from error
    document_values = cast(Mapping[str, object], document)
    parsed: dict[str, object] = {}
    for key, value in document_values.items():
        if key not in _KEYS:
            raise ValueError(f"unsupported eval setting: {key}")
        parsed[key] = value
    _validate_overrides(parsed, defaults)
    effective: dict[str, object] = {
        "agent_runner_ref": defaults.agent_runner_ref,
        "agent_skills_ref": defaults.agent_skills_ref,
        "roles": dict(defaults.roles),
        "skip_validator": defaults.skip_validator,
        "repetitions": defaults.repetitions,
    }
    for key, value in parsed.items():
        if key in _ROLES:
            roles = cast(dict[str, str], effective["roles"])
            roles[key] = cast(str, value)
        else:
            effective[key] = value
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
    return ParsedRequest(parsed, effective, hashlib.sha256(canonical.encode()).hexdigest())


def _validate_overrides(overrides: Mapping[str, object], defaults: EvalDefaults) -> None:
    for key in ("agent_runner_ref", "agent_skills_ref"):
        if key in overrides and (not isinstance(overrides[key], str) or not overrides[key]):
            raise ValueError(f"{key} must be a non-empty string")
    for role in _ROLES:
        if role in overrides:
            _profile(overrides[role], role)
    if "skip_validator" in overrides and not isinstance(overrides["skip_validator"], bool):
        raise ValueError("skip_validator must be a boolean")
    repetitions = overrides.get("repetitions", defaults.repetitions)
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    if defaults.max_repetitions is not None and repetitions > defaults.max_repetitions:
        raise ValueError(f"repetitions exceeds configured maximum of {defaults.max_repetitions}")


def _profile(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value.split(":")) != 3
        or any(not part for part in value.split(":"))
    ):
        raise ValueError(f"{name} must contain a complete cli:model:effort triple")


def _sha(value: str, name: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError(f"{name} revision must be a full commit SHA")
