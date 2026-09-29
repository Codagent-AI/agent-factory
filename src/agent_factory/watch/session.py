# ruff: noqa: E501
# pyright: reportPrivateUsage=false
"""Create a fresh watcher checkout and detach one Runner session."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_factory import audit
from agent_factory.supervisor import (
    _INHERITED_ENVIRONMENT,
    ProcessProbeError,
    process_start_identity,
)
from agent_factory.watch.result import RESULT_FILE, event_line, procedure
from agent_factory.work_kinds.pull_request.launch import (
    SESSION_DIR_NAME,
    _exclude_from_git,
    _hide_tracked_file_from_git,
    _private_file,
    _refuse_symlinked_staging,
    _refuse_tracked_workflow_files,
    resolve_runner_executable,
    staged_config_text,
    tracked_config_text,
    write_input,
)
from agent_factory.work_kinds.pull_request.workspace import (
    PullRequestWorkspace,
    _clone_at,
    _git,
    _require,
)

if TYPE_CHECKING:
    from agent_factory.config import LocalConfig, SharedConfig
    from agent_factory.github import InstallationTokenProvider
    from agent_factory.store import ClaimStore

WORKFLOW_DIR = Path(__file__).parent / "workflow"
WORKFLOW_FILE = "factory-watch-v1.0.yaml"
FILES = (WORKFLOW_FILE, "check-contract.sh", "check-result.sh")


def inherited_environment() -> dict[str, str]:
    """The same token-free operator environment used by readiness and the child."""
    return {key: os.environ[key] for key in _INHERITED_ENVIRONMENT if key in os.environ}


def paths(local: LocalConfig, dispatch_id: str) -> tuple[Path, Path]:
    return (
        local.storage_root / "artifacts" / "watch" / dispatch_id,
        local.storage_root / "clones" / "watch" / dispatch_id / "repo",
    )


def remove_clone(local: LocalConfig, dispatch_id: str) -> None:
    shutil.rmtree(paths(local, dispatch_id)[1].parent, ignore_errors=True)


def start(
    store: ClaimStore,
    row: dict[str, Any],
    local: LocalConfig,
    shared: SharedConfig,
    config_path: Path,
    token_provider: InstallationTokenProvider,
) -> dict[str, object]:
    evidence, clone = paths(local, row["id"])
    (evidence / "private").mkdir(parents=True, exist_ok=True)
    (evidence / "logs").mkdir(exist_ok=True)
    clone.parent.mkdir(parents=True, exist_ok=True)
    workspace = PullRequestWorkspace(
        local.storage_root, local.repositories.agent_runner, local.repositories.agent_skills
    )
    workspace.fetch_mirror(shared.watch.repository, token_provider())
    sha = workspace.resolve_mirror(shared.watch.repository, "main")
    _clone_at(workspace.mirror_path(shared.watch.repository), clone, sha)
    _require(
        _git(
            [
                "-C",
                str(clone),
                "remote",
                "set-url",
                "origin",
                f"https://github.com/{shared.watch.repository}.git",
            ]
        ),
        "cannot point the watch clone at GitHub",
    )
    pr_source = None
    if row["event_kind"] == "PR-READY":
        workspace.fetch_mirror(row["repository"], token_provider())
        pr_source = workspace.mirror_path(row["repository"])
    _refuse_symlinked_staging(clone, FILES)
    _refuse_tracked_workflow_files(clone, FILES)
    catalog = clone / ".agent-runner" / "workflows"
    catalog.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        shutil.copyfile(WORKFLOW_DIR / name, catalog / name)
        if name.endswith(".sh"):
            (catalog / name).chmod(0o755)
    cli, model, effort = str(row["profile"]).split(":")
    (clone / ".agent-runner" / "config.yaml").write_text(
        staged_config_text(tracked_config_text(clone), {"watcher": (cli, model, effort)}),
        encoding="utf-8",
    )
    _hide_tracked_file_from_git(clone, ".agent-runner/config.yaml")
    _exclude_from_git(
        clone, (".agent-runner/config.yaml", *(f".agent-runner/workflows/{name}" for name in FILES))
    )
    claim = store.get_claim(row["claim_id"])
    run = store.get_run(row["run_id"]) if row["run_id"] else None
    brief: dict[str, object] = {
        "dispatch": row["id"],
        "event_kind": row["event_kind"],
        "event_line": event_line(row, claim, run),
        "attempt": row["attempt"],
        "claim": (
            {
                "id": claim.id,
                "repository": claim.repository,
                "issue_number": claim.issue_number,
                "kind": claim.kind,
                "lifecycle": claim.lifecycle,
                "outcome": claim.outcome,
            }
            if claim
            else {}
        ),
        "run": (
            {
                "id": run.id,
                "kind": run.kind,
                "reason": run.reason,
                "attempt_number": run.attempt_number,
                "status": run.status,
                "result": run.result,
                "evidence_path": run.evidence_path,
                "finished_at": run.finished_at,
                "backend": run.plan.get("backend"),
            }
            if run
            else None
        ),
        "pull_request": {"url": row["pr_url"], "number": row["pr_number"]}
        if row["event_kind"] == "PR-READY"
        else None,
        "paths": {
            "state": str(store.path),
            "agent_factory": str(Path(sys.executable).parent / "agent-factory"),
            "factory_python": sys.executable,
            "config": str(config_path),
            "evidence": str(evidence),
            "clone": str(clone),
            "scratch": str(evidence / "scratch"),
            **({"pr_source": str(pr_source)} if pr_source else {}),
        },
        "operator_login": shared.watch.operator,
        "result_file": str(evidence / RESULT_FILE),
        "procedure": procedure(row),
        "forbidden": [
            "Do not deploy, including through the hotfix exception, or merge",
            "Do not edit a release or the service clone",
            "Do not fetch into, add worktrees to, switch branches in, or run commands from the operator's checkout",
            "Do not fetch into the factory's repository mirrors",
            "Do not run tools through the live release symlink",
            "Do not delete shared data, Keychain items, remote branches, or Docker volumes",
            "Do not print credentials or bypass the validator's retry limit",
            "Do not message other sessions or ask questions",
        ],
    }
    brief_file = write_input(evidence, "brief.json", brief)
    runner = resolve_runner_executable()
    session_dir = evidence / SESSION_DIR_NAME
    q = shlex.quote
    audit_command = (
        [
            f"{q(sys.executable)} -P -m agent_factory.audit host --runner {q(runner)} "
            f"--session-dir {q(str(session_dir))} "
            f"--project {q(str(clone))} --evidence {q(str(evidence))} || true"
        ]
        if audit.AUDIT_ENABLED
        else []
    )
    wrapper = _private_file(
        evidence / "private" / "watch-run.sh",
        "\n".join(
            (
                "#!/bin/bash",
                f"echo $$ > {q(str(evidence / 'pid'))}",
                "set -uo pipefail",
                f"exec > >(tee -a {q(str(evidence / 'logs' / 'agent-runner.log'))}) 2>&1",
                "export AGENT_RUNNER_NO_TUI=1",
                f"trap 'rm -rf {q(str(clone.parent))}' EXIT",
                f"date -u +%FT%TZ > {q(str(evidence / 'started-at'))}",
                f"cd {q(str(clone))}",
                f"{q(runner)} run factory-watch --profile factory --session-dir {q(str(session_dir))} "
                f"--param brief_file={q(str(brief_file))} "
                f"--param artifact_dir={q(str(evidence))} --param contract_version=factory-watch/1",
                "status=$?",
                *audit_command,
                f'printf \'{{"code": %d, "finished_at": "%s"}}\\n\' "$status" "$(date -u +%FT%TZ)" > {q(str(evidence / "exit.json"))}',
                'exit "$status"',
                "",
            )
        ),
        0o700,
    )
    env = inherited_environment()
    with (evidence / "factory-watch.log").open("ab") as log:
        process = subprocess.Popen(
            ["/bin/bash", str(wrapper)],
            cwd=clone,
            env=env,
            start_new_session=True,
            stdout=log,
            stderr=log,
        )
    try:
        started = process_start_identity(process.pid)
    except ProcessProbeError:
        return {}
    return {"pid": process.pid, "start": started or "missing"}
