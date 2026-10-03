"""Declarative revision inputs for and-scene evaluations."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from agent_factory.suites.and_scene.errors import ReadinessError

FIXTURE_REPOSITORY = "https://github.com/Codagent-AI/and-scene.git"


def _resolve(checkout: Path | None, ref: str) -> str:
    from agent_factory.runtime import _resolve_revision  # pyright: ignore[reportPrivateUsage]

    if checkout is None:
        raise ReadinessError("source checkout is not configured")
    return _resolve_revision(checkout, ref)


def _resolve_validator(checkout: Path | None, ref: str) -> str:
    try:
        return _resolve(checkout, ref)
    except ReadinessError as error:
        raise ReadinessError(f"Agent Validator checkout: {error}") from error


def _saved_mapping(frozen: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = frozen.get(name)
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _validator_text(frozen: Mapping[str, object]) -> str:
    sha = _saved_mapping(frozen, "revisions").get("validator")
    return "\nAgent Validator: " + str(sha or "published npm release (not pinned)")


def _fixture_text(frozen: Mapping[str, object]) -> str | None:
    fixture = _saved_mapping(frozen, "revisions").get("fixture")
    if fixture is None:
        return None
    requested = _saved_mapping(frozen, "settings").get("fixture_ref")
    return (
        f"\nFixture: `{fixture}` (requested `{requested}`), selected by this request "
        "instead of the agent-evals pin."
    )


def _validator_hold(sha: str) -> str:
    return (
        f"this claim pinned Agent Validator {sha[:7]} at admission under Fly "
        "execution; Docker execution installs the published npm release, so the claim "
        "runs only under Fly execution. Switch eval execution back to fly, or close "
        "the issue and submit a fresh request."
    )


@dataclass(frozen=True, kw_only=True)
class RevisionInput:
    name: str
    noun: str
    required: bool
    setting: str | None
    requestable: bool
    has_default: bool
    admission_rank: int
    resolve: Callable[[Path | None, str], str]
    executions: frozenset[str] = frozenset({"docker", "fly"})
    worktree: bool = False
    fly_commit: bool = False
    source_url: Callable[[Path], str] | None = None
    suite_arguments: Callable[[str], tuple[str, ...]] | None = None
    # Harness flags that readiness requires the selected run.sh to accept.
    suite_flags: tuple[str, ...] = ()
    frozen_inputs_text: Callable[[Mapping[str, object]], str | None] | None = None
    report_line: Callable[[str], str] | None = None
    execution_hold: Callable[[str], str] | None = None


EVAL_INPUTS: tuple[RevisionInput, ...] = (
    RevisionInput(
        name="runner",
        noun="runner",
        required=True,
        setting="agent_runner_ref",
        requestable=True,
        has_default=True,
        admission_rank=0,
        resolve=_resolve,
        worktree=True,
        fly_commit=True,
    ),
    RevisionInput(
        name="skills",
        noun="skills",
        required=True,
        setting="agent_skills_ref",
        requestable=True,
        has_default=True,
        admission_rank=1,
        resolve=_resolve,
        worktree=True,
        fly_commit=True,
    ),
    RevisionInput(
        name="evals",
        noun="harness",
        required=True,
        setting=None,
        requestable=False,
        has_default=False,
        admission_rank=4,
        resolve=_resolve,
        worktree=True,
        fly_commit=True,
    ),
    RevisionInput(
        name="validator",
        noun="validator",
        required=False,
        setting="agent_validator_ref",
        requestable=False,
        has_default=True,
        admission_rank=2,
        resolve=_resolve_validator,
        executions=frozenset({"fly"}),
        fly_commit=True,
        source_url=lambda checkout: validator_source_url(checkout),
        frozen_inputs_text=_validator_text,
        execution_hold=_validator_hold,
    ),
    RevisionInput(
        name="fixture",
        noun="fixture",
        required=False,
        setting="fixture_ref",
        requestable=True,
        has_default=False,
        admission_rank=3,
        resolve=lambda checkout, ref: resolve_fixture(checkout, ref),
        suite_arguments=lambda sha: ("--fixture-ref", sha, "--repo", FIXTURE_REPOSITORY),
        suite_flags=("--fixture-ref", "--repo"),
        frozen_inputs_text=_fixture_text,
        report_line=lambda sha: f"Fixture: {sha}",
    ),
)


def by_name(name: str) -> RevisionInput | None:
    return next((entry for entry in EVAL_INPUTS if entry.name == name), None)


def admission_order() -> tuple[RevisionInput, ...]:
    return tuple(sorted(EVAL_INPUTS, key=lambda entry: entry.admission_rank))


def worktree_names() -> tuple[str, ...]:
    return tuple(entry.name for entry in EVAL_INPUTS if entry.worktree)


def honored_revisions() -> tuple[str, ...]:
    return tuple(entry.name for entry in EVAL_INPUTS)


def _public_diagnostic(value: str) -> str:
    value = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[redacted]@", value)
    value = re.sub(r"\b(?:gh[pousr]_|github_pat_|sk-)[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)\b(?:Proxy-)?Authorization\s*:\s*[^\r\n]*",
        "Authorization: [redacted]",
        value,
    )
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
    return re.sub(
        r"(?i)(\b[\w-]*(?:token|secret|password|api[_-]?key)[\w-]*[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]",
        value,
    )


def validator_source_url(checkout: Path) -> str:
    """Return the public GitHub origin usable by Fly's remote builder."""
    return github_https_origin(checkout, "Agent Validator checkout")


def github_https_origin(checkout: Path, label: str) -> str:
    """Normalize a checkout's GitHub origin without exposing its credentials."""
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        origin = result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ReadinessError(f"Cannot read {label} origin at {checkout}") from error
    shorthand = re.fullmatch(
        r"git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", origin
    )
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise ReadinessError(
            f"{label} origin is not a GitHub repository the Fly builder can fetch"
        ) from None
    path = (
        re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", parsed.path)
        if parsed.scheme in {"https", "ssh"}
        and parsed.hostname == "github.com"
        and not parsed.query
        and not parsed.fragment
        else None
    )
    match = shorthand or path
    if match is None:
        # Keep the actionable scheme/host without leaking embedded credentials.
        redacted = (
            f"{parsed.scheme}://{parsed.hostname or 'unknown'}"
            if parsed.scheme
            else origin.split("@", 1)[-1]
        )
        raise ReadinessError(
            f"{label} origin {redacted!r} is not a GitHub repository the Fly builder can fetch"
        )
    return f"https://github.com/{match.group(1)}/{match.group(2)}.git"


def resolve_fixture(checkout: Path | None, ref: str) -> str:
    """Resolve only commits reachable from the published and-scene origin."""
    prefix = f"and-scene checkout {checkout}: "
    if checkout is None or not checkout.is_dir() or not (checkout / ".git").exists():
        raise ReadinessError(
            prefix
            + "is missing; clone "
            + FIXTURE_REPOSITORY
            + " there or set [repositories] and_scene"
        )
    try:
        origin = github_https_origin(checkout, "and-scene checkout")
    except ReadinessError as error:
        raise ReadinessError(prefix + str(error)) from error
    if origin.lower() != FIXTURE_REPOSITORY.lower():
        raise ReadinessError(
            prefix
            + f"origin {origin} is not the and-scene fixture repository "
            + FIXTURE_REPOSITORY
        )
    from agent_factory.runtime import _resolve_revision  # pyright: ignore[reportPrivateUsage]

    try:
        sha = _resolve_revision(checkout, ref)
    except ReadinessError as error:
        raise ReadinessError(prefix + str(error)) from error
    try:
        branches = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "for-each-ref",
                "--contains",
                sha,
                "--format=%(refname)",
                "refs/remotes/origin/",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadinessError(
            prefix + "cannot inspect origin branches; check the checkout"
        ) from error
    if branches.returncode != 0:
        raise ReadinessError(
            prefix + f"cannot inspect origin branches (git exit {branches.returncode})"
        )
    if branches.stdout.strip():
        return sha
    try:
        tags = subprocess.run(
            ["git", "-C", str(checkout), "ls-remote", "--tags", "origin"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadinessError(
            prefix + "cannot list tags on origin (git exit timeout); check remote access"
        ) from error
    if tags.returncode != 0:
        raise ReadinessError(
            prefix + f"cannot list tags on origin (git exit {tags.returncode}); check remote access"
        )
    listed: dict[str, str] = {}
    for line in tags.stdout.splitlines():
        object_id, _, name = line.partition("\t")
        if name.startswith("refs/tags/") and not name.endswith("^{}"):
            listed[name] = object_id
    try:
        containing = subprocess.run(
            [
                "git",
                "-C",
                str(checkout),
                "for-each-ref",
                "--contains",
                sha,
                "--format=%(refname)%09%(objectname)",
                "refs/tags/",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadinessError(prefix + "cannot inspect origin tags; check the checkout") from error
    if containing.returncode != 0:
        stderr = containing.stderr.strip()
        detail = _public_diagnostic(stderr.splitlines()[-1]) if stderr else "no detail"
        raise ReadinessError(
            prefix + f"cannot inspect origin tags (git exit {containing.returncode}): {detail}"
        )
    for line in containing.stdout.splitlines():
        name, _, object_id = line.partition("\t")
        if listed.get(name) == object_id:
            return sha
    raise ReadinessError(
        prefix + f"fixture commit {sha[:12]} is not published on {FIXTURE_REPOSITORY}; "
        "push it to a branch or tag there"
    )
