"""Pinned worktree and invocation support for the ``and-scene`` suite.

This module is deliberately the only place that knows the suite's filenames,
arguments, result names, and recovery checkpoint shape.  The controller deals
only in immutable claims, reserved runs, and normalized attempt results.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_factory.controller import AttemptResult, ExecutionPlan
from agent_factory.store import ClaimStore

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_REQUIRED_EVAL_FILES = (
    "evals/agent-runner/and-scene/run.sh",
    "evals/agent-runner/and-scene/human-review.sh",
    "evals/agent-runner/and-scene/lib/phases.mjs",
    "evals/agent-runner/and-scene/lib/outcomes.mjs",
    "evals/agent-runner/and-scene/lib/result.mjs",
)
_CANDIDATE_MARKERS = (
    "candidate.json",
    "delivery.json",
    "candidate",
    "result.json",
    "result-state.json",
)
_FACTORY_CREDENTIALS = frozenset(
    {"FACTORY_APP_KEY", "FACTORY_APP_TOKEN", "GH_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY"}
)


class WorktreeError(RuntimeError):
    """A pinned worktree could not be prepared or released safely."""


class ReadinessError(RuntimeError):
    """The selected pinned suite cannot run its approved contract."""


class RecoveryStateError(RuntimeError):
    """Saved suite evidence does not permit a safe recovery invocation."""


@dataclass(frozen=True)
class SourceRepositories:
    runner: Path
    skills: Path
    evals: Path


@dataclass(frozen=True)
class PreparedWorktrees:
    claim_id: str
    runner: Path
    skills: Path
    evals: Path
    source_repositories: SourceRepositories

    def paths(self) -> tuple[Path, Path, Path]:
        return (self.runner, self.skills, self.evals)


class GitWorktreeManager:
    """Creates only detached, claim-owned worktrees under the configured root."""

    def __init__(self, local_root: Path, sources: SourceRepositories) -> None:
        self._root = local_root.resolve()
        self._sources = SourceRepositories(
            sources.runner.resolve(), sources.skills.resolve(), sources.evals.resolve()
        )

    def prepare(self, claim_id: str, revisions: Mapping[str, object]) -> PreparedWorktrees:
        safe_claim = _safe_identity(claim_id)
        values = _revisions(revisions)
        targets = PreparedWorktrees(
            claim_id,
            self._root / "worktrees" / safe_claim / "runner",
            self._root / "worktrees" / safe_claim / "skills",
            self._root / "worktrees" / safe_claim / "evals",
            self._sources,
        )
        created: list[tuple[Path, Path]] = []
        try:
            for source, target, revision in (
                (self._sources.runner, targets.runner, values["runner"]),
                (self._sources.skills, targets.skills, values["skills"]),
                (self._sources.evals, targets.evals, values["evals"]),
            ):
                self._ensure_worktree(source, target, revision)
                created.append((source, target))
        except WorktreeError:
            for source, target in reversed(created):
                self._remove_one(source, target, suppress_errors=True)
            raise
        return targets

    def remove(self, worktrees: PreparedWorktrees) -> dict[str, str]:
        """Release recorded worktrees, returning only failures that need retrying."""
        errors: dict[str, str] = {}
        for name, source, target in (
            ("runner", worktrees.source_repositories.runner, worktrees.runner),
            ("skills", worktrees.source_repositories.skills, worktrees.skills),
            ("evals", worktrees.source_repositories.evals, worktrees.evals),
        ):
            try:
                self._require_owned_target(worktrees.claim_id, name, target)
                self._remove_one(source, target, suppress_errors=False)
            except WorktreeError as error:
                errors[name] = str(error)
        return errors

    def _require_owned_target(self, claim_id: str, name: str, target: Path) -> None:
        expected = self._root / "worktrees" / _safe_identity(claim_id) / name
        if target.resolve() != expected.resolve():
            raise WorktreeError(f"refusing to remove a worktree not owned by this claim: {target}")

    def _ensure_worktree(self, source: Path, target: Path, revision: str) -> None:
        if not source.is_dir():
            raise WorktreeError(f"configured source checkout is unavailable: {source}")
        if target.exists():
            actual = _git(target, "rev-parse", "HEAD")
            if actual != revision or _git(target, "status", "--porcelain"):
                raise WorktreeError(
                    f"recorded worktree is not the accepted clean revision: {target}"
                )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        # Fetch updates refs only; it never switches the shared source checkout.
        _git(source, "fetch", "--quiet", "--tags", "origin", allow_failure=True)
        if (
            _git(source, "rev-parse", "--verify", f"{revision}^{{commit}}", allow_failure=True)
            != revision
        ):
            raise WorktreeError(f"accepted commit is unavailable in configured source: {revision}")
        completed = subprocess.run(
            ["git", "-C", str(source), "worktree", "add", "--detach", str(target), revision],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise WorktreeError(_command_error("cannot create detached worktree", completed))
        if _git(target, "status", "--porcelain"):
            raise WorktreeError(f"new worktree is not clean: {target}")

    def _remove_one(self, source: Path, target: Path, *, suppress_errors: bool) -> None:
        if not target.exists():
            return
        completed = subprocess.run(
            ["git", "-C", str(source), "worktree", "remove", "--force", str(target)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0 and target.exists() and not suppress_errors:
            raise WorktreeError(
                _command_error(f"cannot remove recorded worktree {target}", completed)
            )


class AndSceneAdapter:
    """Readiness, argv construction, recovery, and result adaptation for and-scene."""

    def __init__(self, *, environment_file: Path, mac_name: str = "the factory Mac") -> None:
        self._environment_file = environment_file.resolve()
        self._mac_name = mac_name

    def readiness(self, worktrees: PreparedWorktrees) -> str | None:
        for relative in _REQUIRED_EVAL_FILES:
            if not (worktrees.evals / relative).is_file():
                return f"selected and-scene harness is missing {relative}"
        runner_script = worktrees.runner / "scripts/sandbox-run.sh"
        if not runner_script.is_file() or "--docker-run-arg" not in runner_script.read_text(
            encoding="utf-8"
        ):
            return "selected Agent Runner sandbox launcher lacks repeated --docker-run-arg support"
        if not (worktrees.runner / "workflows/core/implement-change-v1.0.yaml").is_file():
            return "selected Agent Runner revision lacks the and-scene implementation workflow"
        if not self._environment_file.is_file():
            return f"candidate delivery environment file is unavailable: {self._environment_file}"
        try:
            candidate_environment(self._environment_file)
        except ReadinessError as error:
            return str(error)
        return None

    def plan(
        self,
        frozen: Mapping[str, object],
        worktrees: PreparedWorktrees,
        artifact_dir: Path,
        *,
        recovery: bool,
        pre_checkpoint_proven: bool = False,
    ) -> ExecutionPlan:
        readiness = self.readiness(worktrees)
        if readiness is not None:
            raise ReadinessError(readiness)
        artifact = artifact_dir.resolve()
        resume = self._recovery_mode(artifact, recovery, pre_checkpoint_proven)
        settings = _settings(frozen)
        roles = _roles(settings)
        run_script = (worktrees.evals / _REQUIRED_EVAL_FILES[0]).resolve()
        arguments = [
            str(run_script),
            "--run-agent",
            "--agent-runner-dir",
            str(worktrees.runner),
            "--agent-skills-dir",
            str(worktrees.skills),
            "--artifact-dir",
            str(artifact),
            "--env-file",
            str(self._environment_file),
        ]
        for role in ("lead", "implementor", "reviewer"):
            cli, model, effort = _profile(roles[role], role)
            arguments.extend(
                (f"--{role}-cli", cli, f"--{role}-model", model, f"--{role}-effort", effort)
            )
        if settings.get("skip_validator") is True:
            arguments.append("--skip-validator")
        if resume:
            arguments.append("--resume")
        return ExecutionPlan(
            tuple(arguments),
            str(worktrees.evals),
            candidate_environment(self._environment_file),
            (str(self._environment_file),),
            tuple(str(artifact / name) for name in ("run-state.json", "result.json", "logs")),
            {"artifact_path": str(artifact), "suite": "and-scene"},
            resume,
        )

    def read_result(self, artifact_dir: Path) -> AttemptResult:
        result_path = artifact_dir / "result.json"
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ReadinessError(f"suite did not write result.json: {result_path}") from error
        except json.JSONDecodeError as error:
            raise ReadinessError(f"suite result.json is invalid: {error}") from error
        if not isinstance(raw, dict):
            raise ReadinessError("suite result.json must contain an object")
        result = cast(dict[str, object], raw)
        status = result.get("evaluation_status")
        if not isinstance(status, str):
            raise ReadinessError("suite result.json has no evaluation_status")
        product = result.get("product_verdict")
        product_verdict = _canonical_product_verdict(product)
        if product_verdict is not None:
            result["product_verdict"] = product_verdict
        if "automated_subtotal" in result:
            result["score"] = result["automated_subtotal"]
        # This maps suite execution facts only; scoring policy stays in the suite.
        execution = "completed" if status in {"complete", "pending-human-review"} else "failed"
        resumable = result.get("resumable")
        return AttemptResult(
            execution,
            product_verdict,
            result,
            resumable=resumable if isinstance(resumable, bool) else None,
        )

    def review_handoff(
        self, result: Mapping[str, object], review_script: Path, artifact_dir: Path
    ) -> str | None:
        if result.get("evaluation_status") != "pending-human-review":
            return None
        command = shlex.join(
            (str(review_script.resolve()), "--run-dir", str(artifact_dir.resolve()))
        )
        return (
            f"Human review is ready on {self._mac_name} while this item remains in Review.\n"
            f"Run: {command}\n"
            "The retained suite worktree may be released only after the reviewed item moves "
            "to Done."
        )

    def quota_until(self, diagnostic: str, *, now: datetime | None = None) -> datetime | None:
        """Recognize explicit Codex quota diagnostics, never arbitrary suite errors."""
        lower = diagnostic.lower()
        if "codex" not in lower or not any(
            token in lower for token in ("rate limit", "usage limit")
        ):
            return None
        reference = now or datetime.now(UTC)
        iso = re.search(r"\b(\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d))\b", diagnostic)
        if iso is not None:
            try:
                return datetime.fromisoformat(iso.group(1).replace("Z", "+00:00"))
            except ValueError:
                return reference + timedelta(hours=5)
        # A recognized limit with no parseable reset gets the documented bounded fallback.
        return reference + timedelta(hours=5)

    def _recovery_mode(self, artifact: Path, recovery: bool, pre_checkpoint_proven: bool) -> bool:
        if not recovery:
            return False
        state = artifact / "run-state.json"
        if state.exists():
            try:
                saved = json.loads(state.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise RecoveryStateError("saved suite checkpoint is corrupt") from error
            if not isinstance(saved, Mapping):
                raise RecoveryStateError("saved suite checkpoint is incompatible")
            checkpoint = cast(Mapping[str, object], saved)
            if not isinstance(checkpoint.get("schema_version"), int):
                raise RecoveryStateError("saved suite checkpoint is incompatible")
            return True
        if any((artifact / marker).exists() for marker in _CANDIDATE_MARKERS):
            raise RecoveryStateError(
                "suite checkpoint is missing after candidate execution evidence"
            )
        if not pre_checkpoint_proven:
            raise RecoveryStateError(
                "suite checkpoint is missing without proof of pre-checkpoint stop"
            )
        return False


class WorktreeCleanup:
    """Durably releases only worktrees retained through a Review handoff."""

    def __init__(self, store: ClaimStore, manager: GitWorktreeManager) -> None:
        self._store = store
        self._manager = manager

    def record(self, claim_id: str, worktrees: PreparedWorktrees) -> None:
        paths = {
            name: {"source": str(source), "path": str(path)}
            for name, source, path in (
                ("runner", worktrees.source_repositories.runner, worktrees.runner),
                ("skills", worktrees.source_repositories.skills, worktrees.skills),
                ("evals", worktrees.source_repositories.evals, worktrees.evals),
            )
        }
        self._store.set_preparation(claim_id, {"worktrees": paths})
        self._store.set_cleanup(
            claim_id, {"review_observed": False, "complete": False, "paths": paths}
        )

    def reconcile(self, claim_id: str, *, board_status: str) -> bool:
        claim = self._store.get_claim(claim_id)
        if claim is None or claim.lifecycle != "settled":
            return False
        cleanup = dict(claim.cleanup)
        if board_status == "Review":
            if cleanup.get("review_observed") is not True:
                cleanup["review_observed"] = True
                self._store.set_cleanup(claim_id, cleanup)
            return False
        if board_status != "Done" or cleanup.get("review_observed") is not True:
            return False
        if cleanup.get("complete") is True:
            return True
        try:
            worktrees = _recorded_worktrees(claim_id, cleanup)
        except WorktreeError as error:
            cleanup["complete"] = False
            cleanup["last_error"] = {"cleanup": str(error)}
            self._store.set_cleanup(claim_id, cleanup)
            return False
        errors = self._manager.remove(worktrees)
        cleanup["complete"] = not errors
        cleanup["last_error"] = errors or None
        self._store.set_cleanup(claim_id, cleanup)
        return not errors


def _git(path: Path, *arguments: str, allow_failure: bool = False) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments], text=True, capture_output=True, check=False
    )
    if completed.returncode != 0 and not allow_failure:
        raise WorktreeError(_command_error("git command failed", completed))
    return completed.stdout.strip()


def _command_error(prefix: str, completed: subprocess.CompletedProcess[str]) -> str:
    detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
    return f"{prefix}: {detail}"


def _safe_identity(value: str) -> str:
    result = _SAFE_ID.sub("-", value).strip(".-")
    if not result:
        raise WorktreeError("claim identity cannot be converted to a safe worktree name")
    return result


def _revisions(value: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("runner", "skills", "evals"):
        revision = value.get(name)
        if not isinstance(revision, str) or not _SHA.fullmatch(revision):
            raise WorktreeError(f"accepted {name} revision is not a full commit SHA")
        result[name] = revision
    return result


def _settings(frozen: Mapping[str, object]) -> Mapping[str, object]:
    if frozen.get("suite") != "and-scene":
        raise ReadinessError("claim is not frozen for the and-scene suite")
    raw = frozen.get("settings")
    if not isinstance(raw, Mapping):
        raise ReadinessError("claim has no frozen evaluation settings")
    return cast(Mapping[str, object], raw)


def _roles(settings: Mapping[str, object]) -> Mapping[str, str]:
    raw = settings.get("roles")
    if not isinstance(raw, Mapping):
        raise ReadinessError("claim has no frozen role profiles")
    roles = cast(Mapping[str, object], raw)
    result: dict[str, str] = {}
    for role in ("lead", "implementor", "reviewer"):
        profile = roles.get(role)
        if not isinstance(profile, str):
            raise ReadinessError(f"claim has no frozen {role} profile")
        result[role] = profile
    return result


def _profile(value: str, role: str) -> tuple[str, str, str]:
    pieces = value.split(":")
    if len(pieces) != 3 or any(not piece for piece in pieces):
        raise ReadinessError(f"frozen {role} profile is invalid")
    return pieces[0], pieces[1], pieces[2]


def candidate_environment(path: Path) -> dict[str, str]:
    environment: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as error:
        raise ReadinessError(
            f"candidate delivery environment file is unavailable: {path}"
        ) from error
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ReadinessError(f"candidate delivery environment has an invalid entry: {raw}")
        if (
            name in _FACTORY_CREDENTIALS
            or name.startswith("FACTORY_")
            or name.startswith("GITHUB_APP_")
        ):
            raise ReadinessError(
                "candidate delivery environment must not contain factory App credentials"
            )
        environment[name] = _dotenv_value(value, raw)
    if not environment:
        raise ReadinessError("candidate delivery environment has no allowed credentials")
    return environment


def _canonical_product_verdict(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return {"fail": "failed", "pass": "passed"}.get(value, value)


def _dotenv_value(value: str, raw: str) -> str:
    if not value:
        return ""
    try:
        values = shlex.split(value, posix=True)
    except ValueError as error:
        raise ReadinessError(
            f"candidate delivery environment has an invalid value: {raw}"
        ) from error
    if len(values) != 1:
        raise ReadinessError(f"candidate delivery environment has an invalid value: {raw}")
    return values[0]


def _recorded_worktrees(claim_id: str, cleanup: Mapping[str, object]) -> PreparedWorktrees:
    raw_paths = cleanup.get("paths")
    if not isinstance(raw_paths, Mapping):
        raise WorktreeError("claim has no recorded worktrees to clean up")
    paths = cast(Mapping[str, object], raw_paths)
    resolved: dict[str, tuple[Path, Path]] = {}
    for name in ("runner", "skills", "evals"):
        raw = paths.get(name)
        if not isinstance(raw, Mapping):
            raise WorktreeError(f"claim has no recorded {name} worktree")
        record = cast(Mapping[str, object], raw)
        source = record.get("source")
        path = record.get("path")
        if not isinstance(source, str) or not isinstance(path, str):
            raise WorktreeError(f"claim has invalid recorded {name} worktree")
        resolved[name] = (Path(source), Path(path))
    return PreparedWorktrees(
        claim_id,
        resolved["runner"][1],
        resolved["skills"][1],
        resolved["evals"][1],
        SourceRepositories(resolved["runner"][0], resolved["skills"][0], resolved["evals"][0]),
    )
